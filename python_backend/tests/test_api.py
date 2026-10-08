# -*- coding: utf-8 -*-
"""API 集成测试：使用临时数据目录，不污染真实数据。"""
import json

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


def _sse_bytes(payload):
    return ("data: " + json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")


def _tool_chunks(name, args, call_id):
    return [
        _sse_bytes({"choices": [{"delta": {"role": "assistant"}, "finish_reason": None}]}),
        _sse_bytes({"choices": [{"delta": {"tool_calls": [{
            "index": 0,
            "id": call_id,
            "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
        }]}, "finish_reason": None}]}),
        _sse_bytes({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
    ]


def test_chat_can_call_tools_for_multiple_rounds(client, monkeypatch):
    """模拟两轮连续工具调用，验证 agent 会把结果回填后继续循环。"""
    client.post("/api/auth/register", json={"username": "agentuser", "password": "pass123"})
    monkeypatch.setattr(main, "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(main, "extract_memory", lambda *args, **kwargs: None)

    response_chunks = [
        _tool_chunks("search_attractions", {"city": "成都"}, "call_1"),
        _tool_chunks("search_flights", {"from_city": "上海", "to_city": "成都"}, "call_2"),
        [
            _sse_bytes({"choices": [{"delta": {"role": "assistant", "content": "规划完成"},
                                     "finish_reason": None}]}),
            _sse_bytes({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
        ],
    ]

    class FakeStreamResponse:
        status_code = 200

        def __init__(self, chunks):
            self._chunks = chunks

        def json(self):
            return {}

        def iter_lines(self, decode_unicode=False):
            return iter(self._chunks)

    responses = [FakeStreamResponse(chunks) for chunks in response_chunks]
    calls = []

    def fake_post(url, **kwargs):
        calls.append(kwargs)
        return responses.pop(0)

    monkeypatch.setattr(main.requests, "post", fake_post)

    r = client.post("/api/chat", json={
        "messages": [{"role": "user", "content": "帮我规划成都三天行程"}],
        "conversationId": None,
    })
    assert r.status_code == 200
    assert len(calls) == 3, calls

    events = []
    for block in r.text.split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data:"):
                raw = line[5:].strip()
                if raw:
                    events.append(json.loads(raw))

    tools = [e["name"] for e in events if e["type"] == "tool"]
    assert tools == ["search_attractions", "search_flights"], events
    assert any(e["type"] == "delta" and e["content"] == "规划完成" for e in events)
    assert any(e["type"] == "done" for e in events)
    assert responses == []
