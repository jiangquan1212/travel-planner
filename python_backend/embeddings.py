# -*- coding: utf-8 -*-
"""OpenAI 兼容的 Embedding 客户端。

DeepSeek 不提供 Embedding 接口，因此这里支持任意 OpenAI 兼容服务，
默认示例为硅基流动（SiliconFlow）的 BAAI/bge-m3。未配置 key 时返回禁用，
由 vector_store / guide_store 自动回退到 TF-IDF 稀疏向量检索。
"""

import os

import requests

DEFAULT_BASE_URL = "https://api.siliconflow.cn/v1"
DEFAULT_MODEL = "BAAI/bge-m3"


def _config():
    return {
        "key": (os.environ.get("EMBEDDING_API_KEY") or "").strip(),
        "base": (os.environ.get("EMBEDDING_BASE_URL") or DEFAULT_BASE_URL).rstrip("/"),
        "model": os.environ.get("EMBEDDING_MODEL") or DEFAULT_MODEL,
    }


def embedding_enabled():
    """是否配置了可用的 embedding key。"""
    return bool(_config()["key"])


def embed_texts(texts, batch_size=16):
    """批量生成稠密向量；接口格式与 OpenAI /embeddings 保持一致。"""
    cfg = _config()
    if not cfg["key"]:
        raise RuntimeError("EMBEDDING_API_KEY 未配置")
    items = [str(t).strip() for t in texts]
    items = [t[:8000] for t in items]
    vectors = []
    for i in range(0, len(items), batch_size):
        batch = items[i:i + batch_size]
        resp = requests.post(
            f"{cfg['base']}/embeddings",
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {cfg['key']}"},
            json={"model": cfg["model"], "input": batch},
            timeout=30,
        )
        resp.raise_for_status()
        data = sorted(resp.json().get("data") or [], key=lambda x: x.get("index", 0))
        vectors.extend(d["embedding"] for d in data)
    if len(vectors) != len(items):
        raise RuntimeError(f"Embedding 返回数量异常：{len(vectors)} != {len(items)}")
    return vectors


def embed_text(text):
    return embed_texts([text])[0]


def dense_cosine(a, b):
    """稠密向量余弦相似度。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
