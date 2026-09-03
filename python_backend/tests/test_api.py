# -*- coding: utf-8 -*-
"""API 集成测试：使用临时数据目录，不污染真实数据。"""
import pytest
from fastapi.testclient import TestClient

import main
from guide_store import GuideStore
from vector_store import VectorStore


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA_DIR", tmp_path)
    monkeypatch.setattr(main, "vector_store", VectorStore(tmp_path / "preferences.json"))
    monkeypatch.setattr(main, "guide_store", GuideStore(tmp_path / "guides.json"))
    with TestClient(main.app) as c:
        yield c


def test_register_login_me(client):
    r = client.post("/api/auth/register", json={"username": "tester01", "password": "pass123"})
    assert r.status_code == 201
    r = client.get("/api/auth/me")
    assert r.status_code == 200 and r.json()["user"]["username"] == "tester01"
    client.post("/api/auth/logout")
    r = client.post("/api/auth/login", json={"username": "tester01", "password": "pass123"})
    assert r.status_code == 200


def test_auth_required(client):
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/preferences").status_code == 401


def test_preferences_crud(client):
    client.post("/api/auth/register", json={"username": "prefuser", "password": "pass123"})
    r = client.post("/api/preferences", json={"text": "喜欢辣", "category": "美食", "weight": 4})
    assert r.status_code == 201
    pid = r.json()["preference"]["id"]
    r = client.patch(f"/api/preferences/{pid}", json={"weight": 2})
    assert r.status_code == 200 and r.json()["preference"]["weight"] == 2
    r = client.delete(f"/api/preferences/{pid}")
    assert r.status_code == 200


def test_favorites(client):
    client.post("/api/auth/register", json={"username": "favuser", "password": "pass123"})
    r = client.post("/api/favorites", json={"title": "成都计划", "content": "# 成都\nDay1 宽窄巷子"})
    assert r.status_code == 201
    fid = r.json()["favorite"]["id"]
    r = client.get("/api/favorites")
    assert r.status_code == 200 and len(r.json()["favorites"]) == 1
    assert client.delete(f"/api/favorites/{fid}").status_code == 200


def test_config(client):
    client.post("/api/auth/register", json={"username": "cfguser", "password": "pass123"})
    r = client.get("/api/config")
    assert r.status_code == 200 and "chatEnabled" in r.json()
