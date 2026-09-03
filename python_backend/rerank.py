# -*- coding: utf-8 -*-
"""RAG 重排：混合检索 + 倒数排名融合（Reciprocal Rank Fusion）。

思路：先用 TF-IDF 向量召回较多候选（Recall），再用 BM25 词法相关度作为第二路信号，
把两路排序用 RRF 融合，重新排出 Top-K（Precision）。比单一向量排序更稳、可解释。
"""
import math

from vector_store import tokenize

RRF_K = 60  # RRF 平滑常数


def bm25_scores(query, docs):
    """BM25 打分（在给定候选文档集合上计算）。返回与 docs 等长的分数列表。"""
    q_tokens = tokenize(query)
    docs_tokens = [tokenize(d) for d in docs]
    n = len(docs_tokens) or 1
    df = {}
    for toks in docs_tokens:
        for t in set(toks):
            df[t] = df.get(t, 0) + 1
    avgdl = sum(len(t) for t in docs_tokens) / n or 1.0
    k1, b = 1.5, 0.75
    scores = []
    for toks in docs_tokens:
        dl = len(toks)
        tf = {}
        for t in toks:
            tf[t] = tf.get(t, 0) + 1
        s = 0.0
        for t in q_tokens:
            f = tf.get(t, 0)
            idf = math.log((n - df.get(t, 0) + 0.5) / (df.get(t, 0) + 0.5) + 1.0)
            s += idf * (f * (k1 + 1)) / (f + k1 * (1 - b + b * dl / avgdl))
        scores.append(s)
    return scores


def rrf_rerank(query, candidates):
    """对 candidates（[{text, score}]，score 为向量相似度）做 RRF 重排。

    返回新的排序列表 [{text, score(向量), bm25, rrf}]，按 rrf 降序。
    """
    if not candidates:
        return []
    texts = [c["text"] for c in candidates]
    bm = bm25_scores(query, texts)

    # 两路排序（降序 → 升序名次）
    cos_order = sorted(range(len(candidates)), key=lambda i: -candidates[i]["score"])
    bm_order = sorted(range(len(candidates)), key=lambda i: -bm[i])

    cos_rank = [0] * len(candidates)
    bm_rank = [0] * len(candidates)
    for r, i in enumerate(cos_order, start=1):
        cos_rank[i] = r
    for r, i in enumerate(bm_order, start=1):
        bm_rank[i] = r

    results = []
    for i, c in enumerate(candidates):
        rrf = 1.0 / (RRF_K + cos_rank[i]) + 1.0 / (RRF_K + bm_rank[i])
        results.append({"text": c["text"], "score": c.get("score", 0),
                        "bm25": round(bm[i], 4), "rrf": round(rrf, 6)})
    results.sort(key=lambda x: -x["rrf"])
    return results
