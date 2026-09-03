# -*- coding: utf-8 -*-
"""认证模块测试。"""
from auth import hash_password, verify_password, sign, parse_session_token, create_session_token


def test_hash_verify_roundtrip():
    h = hash_password("abc123")
    assert ":" in h
    assert verify_password("abc123", h)


def test_wrong_password():
    h = hash_password("abc123")
    assert not verify_password("wrong", h)


def test_invalid_stored():
    assert not verify_password("x", "not-a-hash")
    assert not verify_password("x", None)


def test_sign_and_parse_token():
    token = create_session_token()
    sig = sign(token)
    cookie = f"tp_session={token}.{sig}"
    assert parse_session_token(cookie) == token
    assert parse_session_token("tp_session=bad.sig") is None
    assert parse_session_token("") is None
