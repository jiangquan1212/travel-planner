# -*- coding: utf-8 -*-
"""RAG 重排（BM25 / RRF）测试。"""
from rerank import bm25_scores, rrf_rerank


def test_bm25_prefers_matching():
    docs = ["成都美食火锅串串", "北京故宫门票预约", "杭州西湖龙井"]
    s = bm25_scores("成都火锅", docs)
    assert s[0] > s[1] and s[0] > s[2]


def test_rrf_reorders():
    cands = [
        {"text": "青岛啤酒博物馆门票60元", "score": 0.5},
        {"text": "青岛海鲜和啤酒都出名", "score": 0.7},
    ]
    out = rrf_rerank("啤酒博物馆门票", cands)
    assert out and out[0]["text"] == cands[0]["text"]
    assert "rrf" in out[0]
