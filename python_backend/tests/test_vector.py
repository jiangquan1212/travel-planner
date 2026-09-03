# -*- coding: utf-8 -*-
"""偏好向量库测试（临时文件，不影响真实数据）。"""
from vector_store import VectorStore


def _store(tmp_path):
    return VectorStore(tmp_path / "prefs.json")


def test_add_and_search(tmp_path):
    vs = _store(tmp_path)
    vs.add("u1", "喜欢美食和当地特色小吃", "美食", 5)
    vs.add("u1", "偏好经济型住宿", "预算", 3)
    vs.add("u1", "喜欢历史古迹", "游玩", 2)
    hits = vs.search("u1", "当地美食", 1)
    assert hits and "美食" in hits[0]["text"]
    assert hits[0]["weight"] == 5  # 重要度加权


def test_duplicate_rejected(tmp_path):
    vs = _store(tmp_path)
    assert "doc" in vs.add("u1", "喜欢海边")
    assert "error" in vs.add("u1", "喜欢海边")


def test_update_category_weight(tmp_path):
    vs = _store(tmp_path)
    d = vs.add("u1", "喜欢爬山")
    doc = d["doc"]
    up = vs.update("u1", doc["id"], {"category": "游玩", "weight": 4})
    assert up["category"] == "游玩" and up["weight"] == 4


def test_remove(tmp_path):
    vs = _store(tmp_path)
    doc = vs.add("u1", "删除我")["doc"]
    assert vs.remove("u1", doc["id"])
    assert not vs.remove("u1", doc["id"])
