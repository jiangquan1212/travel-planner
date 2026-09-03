# -*- coding: utf-8 -*-
"""SQLite 数据层测试。"""
import pytest

import db as dbm


def test_db_enabled_detection(monkeypatch):
    monkeypatch.delenv("TP_DB_FILE", raising=False)
    assert not dbm.db_enabled()


def test_sqlite_crud(tmp_path, monkeypatch):
    monkeypatch.setenv("TP_DB_FILE", str(tmp_path / "t.db"))
    dbm.init_schema()
    dbm.insert("users", {"id": "1", "username": "alice", "role": "user", "userId": None})
    assert dbm.get("users", "1")["username"] == "alice"
    assert len(dbm.all("users")) == 1
    dbm.update("users", "1", {"role": "admin"})
    assert dbm.get("users", "1")["role"] == "admin"
    dbm.insert("sessions", {"id": "s1", "token": "t1", "userId": "1"})
    assert dbm.all("sessions")[0]["token"] == "t1"
    assert dbm.remove("users", "1")
    assert dbm.get("users", "1") is None
    dbm.replace("users", [{"id": "x", "userId": None, "doc": None}])
    assert len(dbm.all("users")) == 1


def test_collection_via_sqlite(tmp_path, monkeypatch):
    monkeypatch.setenv("TP_DB_FILE", str(tmp_path / "c.db"))
    dbm.init_schema()
    from main import Collection
    c = Collection("users")
    c.insert({"id": "u1", "username": "bob", "role": "user"})
    assert c.get("u1")["username"] == "bob"
    c.update("u1", {"role": "admin"})
    assert c.find(lambda r: r["id"] == "u1")["role"] == "admin"
    assert c.remove("u1")
    assert not c.get("u1")
