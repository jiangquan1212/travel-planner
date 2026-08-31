# -*- coding: utf-8 -*-
"""轻量向量数据库（纯 Python）：TF-IDF 中文分词 + 余弦相似度，持久化到 data/preferences.json。

与 Node 版 lib/vector.js 的格式和算法保持一致，数据可直接复用。
新增：偏好分类 category 与重要度 weight（1-5），检索时按重要度加权。
"""

import json
import math
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path

MAX_TEXT_LEN = 200
DEFAULT_CATEGORY = "其他"
DEFAULT_WEIGHT = 3


def _norm_weight(weight):
    try:
        w = int(weight)
    except (TypeError, ValueError):
        w = DEFAULT_WEIGHT
    return max(1, min(5, w))


def _now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def tokenize(text):
    """中文：二元组 + 单字；英文：单词。与 Node 版一致。"""
    s = str(text or "").lower()
    tokens = re.findall(r"[a-z0-9]+", s)
    han = re.sub(r"[^\u4e00-\u9fa5]", "", s)
    if len(han) == 1:
        tokens.append(han)
    for i in range(len(han) - 1):
        tokens.append(han[i:i + 2])
    for ch in han:
        tokens.append(ch)
    return tokens


def _cosine(a, b):
    dot = 0.0
    na = 0.0
    nb = 0.0
    for k, v in a.items():
        dot += v * b.get(k, 0.0)
        na += v * v
    for v in b.values():
        nb += v * v
    if na == 0 or nb == 0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


class VectorStore:
    def __init__(self, filepath):
        self.file = Path(filepath)
        self.docs = []          # [{id, userId, text, category, weight, createdAt}]
        self._index = {}        # userId -> {docId: {token: weight}}
        self._load()
        self._rebuild()

    def _load(self):
        if not self.file.exists():
            return
        try:
            parsed = json.loads(self.file.read_text(encoding="utf-8"))
            self.docs = parsed if isinstance(parsed, list) else []
        except Exception as e:
            print(f"[vector] 无法解析 {self.file}，已重置: {e}")
            self.docs = []

    def _save(self):
        tmp = self.file.with_suffix(self.file.suffix + ".tmp")
        tmp.write_text(json.dumps(self.docs, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.file)

    def _rebuild(self):
        self._index = {}
        by_user = {}
        for doc in self.docs:
            by_user.setdefault(doc["userId"], []).append(doc)
        for uid, docs in by_user.items():
            tokenized = [tokenize(d["text"]) for d in docs]
            df = {}
            for toks in tokenized:
                for t in set(toks):
                    df[t] = df.get(t, 0) + 1
            n = len(docs)
            def idf(t):
                return math.log((n + 1) / (df.get(t, 0) + 1)) + 1
            user_map = {}
            for i, toks in enumerate(tokenized):
                tf = {}
                for t in toks:
                    tf[t] = tf.get(t, 0) + 1
                vec = {t: (c / len(toks)) * idf(t) for t, c in tf.items()}
                user_map[docs[i]["id"]] = vec
            self._index[uid] = user_map

    def add(self, user_id, text, category=None, weight=None):
        text = str(text or "").strip()
        if not text:
            return {"error": "偏好内容不能为空"}
        if len(text) > MAX_TEXT_LEN:
            return {"error": f"偏好最多 {MAX_TEXT_LEN} 字"}
        if any(d["userId"] == user_id and d["text"] == text for d in self.docs):
            return {"error": "该偏好已存在"}
        doc = {
            "id": secrets.token_hex(8),
            "userId": user_id,
            "text": text,
            "category": str(category or "").strip() or DEFAULT_CATEGORY,
            "weight": _norm_weight(weight),
            "createdAt": _now(),
        }
        self.docs.append(doc)
        self._save()
        self._rebuild()
        return {"doc": doc}

    def update(self, user_id, doc_id, patch):
        idx = next((i for i, d in enumerate(self.docs)
                    if d["id"] == doc_id and d["userId"] == user_id), None)
        if idx is None:
            return None
        doc = self.docs[idx]
        if "text" in patch:
            text = str(patch.get("text") or "").strip()
            if not text:
                return {"error": "偏好内容不能为空"}
            if len(text) > MAX_TEXT_LEN:
                return {"error": f"偏好最多 {MAX_TEXT_LEN} 字"}
            if any(d["userId"] == user_id and d["text"] == text and d["id"] != doc_id
                   for d in self.docs):
                return {"error": "该偏好已存在"}
            doc["text"] = text
        if "category" in patch:
            doc["category"] = str(patch.get("category") or "").strip() or DEFAULT_CATEGORY
        if "weight" in patch:
            doc["weight"] = _norm_weight(patch.get("weight"))
        self._save()
        self._rebuild()
        return dict(doc)

    def remove(self, user_id, doc_id):
        idx = next((i for i, d in enumerate(self.docs)
                    if d["id"] == doc_id and d["userId"] == user_id), None)
        if idx is None:
            return False
        self.docs.pop(idx)
        self._save()
        self._rebuild()
        return True

    def list(self, user_id):
        items = [dict(d) for d in self.docs if d["userId"] == user_id]
        for it in items:
            it.setdefault("category", DEFAULT_CATEGORY)
            it.setdefault("weight", DEFAULT_WEIGHT)
        return sorted(items, key=lambda d: d["createdAt"])

    def search(self, user_id, query, top_k=3):
        q_tokens = tokenize(query)
        user_vecs = self._index.get(user_id, {})
        if not q_tokens or not user_vecs:
            return []
        docs = [d for d in self.docs if d["userId"] == user_id]
        df = {}
        for d in docs:
            for t in set(tokenize(d["text"])):
                df[t] = df.get(t, 0) + 1
        n = len(docs) or 1
        def idf(t):
            return math.log((n + 1) / (df.get(t, 0) + 1)) + 1
        q_tf = {}
        for t in q_tokens:
            q_tf[t] = q_tf.get(t, 0) + 1
        q_vec = {t: (c / len(q_tokens)) * idf(t) for t, c in q_tf.items()}
        results = []
        for doc_id, vec in user_vecs.items():
            score = _cosine(q_vec, vec)
            if score > 0:
                doc = next((d for d in docs if d["id"] == doc_id), None)
                if not doc:
                    continue
                # 重要度加权：权重越高排名越靠前
                w = doc.get("weight", DEFAULT_WEIGHT)
                boosted = score * (0.5 + 0.5 * w / 5.0)
                results.append({"id": doc_id, "text": doc["text"],
                                "category": doc.get("category", DEFAULT_CATEGORY),
                                "weight": w, "score": round(boosted, 4)})
        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:top_k]
