# -*- coding: utf-8 -*-
"""攻略知识库测试。"""
from guide_store import GuideStore


def test_add_and_search(tmp_path):
    gs = GuideStore(tmp_path / "guides.json")
    gs.add("u1", "成都.txt", "成都美食有火锅、串串香、担担面。大熊猫基地适合亲子游。")
    gs.add("u1", "杭州.txt", "西湖十景包括苏堤春晓、曲院风荷。龙井虾仁是杭州名菜。")
    hits = gs.search("u1", "成都火锅", 1)
    assert hits and hits[0]["filename"] == "成都.txt"


def test_list_and_remove(tmp_path):
    gs = GuideStore(tmp_path / "guides.json")
    d = gs.add("u1", "x.txt", "测试文档内容，包含多个句子。这是第二句。")["doc"]
    assert gs.list("u1") and gs.list("u1")[0]["chunks"] >= 1
    assert gs.remove("u1", d["id"])
    assert not gs.list("u1")
