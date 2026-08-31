# -*- coding: utf-8 -*-
"""RAG 旅行攻略知识库：上传攻略文档 → 分块 → TF-IDF 向量化 → 相似度检索。

对应课程要求 W7（RAG 检索增强生成）。数据持久化到 data/guides.json。
"""

import json
import math
import re
import secrets
from datetime import datetime, timezone

from vector_store import tokenize, _cosine

CHUNK_SIZE = 500


def _now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def split_chunks(text, max_len=CHUNK_SIZE):
    """按段落/句子切分为不超过 max_len 的块。"""
    parts = []
    for line in re.split(r"\r?\n+", text):
        sentences = re.split(r"(?<=[。；;！!？?])", line)
        for s in sentences:
            s = s.strip()
            if s:
                parts.append(s)
    chunks = []
    cur = ""
    for p in parts:
        if len(cur) + len(p) <= max_len:
            cur += p
        else:
            if cur:
                chunks.append(cur)
            cur = p
            while len(cur) > max_len:
                chunks.append(cur[:max_len])
                cur = cur[max_len:]
    if cur:
        chunks.append(cur)
    return chunks


class GuideStore:
    def __init__(self, filepath):
        self.file = filepath
        self.docs = []      # [{id, userId, filename, chunks:[{id,text}], createdAt}]
        self._index = {}    # userId -> {chunk_id: {token: weight}}
        self._load()
        self._rebuild()

    def _load(self):
        if not self.file.exists():
            return
        try:
            parsed = json.loads(self.file.read_text(encoding="utf-8"))
            self.docs = parsed if isinstance(parsed, list) else []
        except Exception:
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
            all_tokens = []
            chunk_id_map = {}
            for d in docs:
                for ch in d.get("chunks", []):
                    toks = tokenize(ch["text"])
                    all_tokens.append((ch["id"], toks))
            df = {}
            for _, toks in all_tokens:
                for t in set(toks):
                    df[t] = df.get(t, 0) + 1
            n = len(all_tokens) or 1
            def idf(t):
                return math.log((n + 1) / (df.get(t, 0) + 1)) + 1
            user_map = {}
            for cid, toks in all_tokens:
                tf = {}
                for t in toks:
                    tf[t] = tf.get(t, 0) + 1
                vec = {t: (c / len(toks)) * idf(t) for t, c in tf.items()}
                user_map[cid] = vec
            self._index[uid] = user_map

    def add(self, user_id, filename, text):
        text = text.strip()
        if not text:
            return {"error": "文档没有可提取的文本"}
        chunks = split_chunks(text)
        if not chunks:
            return {"error": "未能从文档中切分出内容"}
        doc = {
            "id": secrets.token_hex(8),
            "userId": user_id,
            "filename": filename,
            "chunks": [{"id": secrets.token_hex(6), "text": c} for c in chunks],
            "createdAt": _now(),
        }
        self.docs.append(doc)
        self._save()
        self._rebuild()
        return {"doc": doc, "chunks": len(chunks)}

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
        return [{"id": d["id"], "filename": d["filename"],
                 "chunks": len(d.get("chunks", [])), "createdAt": d["createdAt"]}
                for d in self.docs if d["userId"] == user_id]

    def search(self, user_id, query, top_k=4):
        q_tokens = tokenize(query)
        user_map = self._index.get(user_id, {})
        if not q_tokens or not user_map:
            return []
        q_tf = {}
        for t in q_tokens:
            q_tf[t] = q_tf.get(t, 0) + 1
        n = max(1, len(user_map))
        q_vec = {t: (c / len(q_tokens)) * (math.log((n + 1) / 2) + 1) for t, c in q_tf.items()}
        results = []
        for cid, vec in user_map.items():
            score = _cosine(q_vec, vec)
            if score > 0:
                chunk_text = None
                fname = ""
                for d in self.docs:
                    if d["userId"] != user_id:
                        continue
                    for ch in d.get("chunks", []):
                        if ch["id"] == cid:
                            chunk_text = ch["text"]
                            fname = d["filename"]
                            break
                    if chunk_text:
                        break
                if chunk_text:
                    results.append({"text": chunk_text[:300], "filename": fname,
                                    "score": round(score, 4)})
        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:top_k]
