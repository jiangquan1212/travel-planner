# -*- coding: utf-8 -*-
"""认证工具：scrypt 密码哈希 + 签名会话 Cookie（与 Node 版格式完全兼容）。"""

import base64
import hashlib
import hmac
import os
import re
import secrets

SESSION_COOKIE = "tp_session"
SESSION_MAX_AGE = 7 * 24 * 3600  # 7 天
SECRET = os.environ.get("TP_SECRET", "travel-planner-dev-secret-please-change-in-prod")


def hash_password(password):
    """生成与 Node scryptSync(password, salt, 64) 兼容的 salt:hash。

    注意：Node 把 salt 的十六进制字符串按 UTF-8/ASCII 字节直接作为盐使用，
    因此这里 salt 用 `salt.encode("ascii")`，而不是 bytes.fromhex。
    """
    salt = secrets.token_hex(16)
    dk = hashlib.scrypt(str(password).encode("utf-8"), salt=salt.encode("ascii"),
                        n=16384, r=8, p=1, dklen=64)
    return f"{salt}:{dk.hex()}"


def verify_password(password, stored):
    """校验密码；兼容 Node 版已存的 salt:hash。"""
    if not stored or not isinstance(stored, str) or ":" not in stored:
        return False
    salt, h = stored.split(":", 1)
    if not salt or not h:
        return False
    try:
        dk = hashlib.scrypt(str(password).encode("utf-8"), salt=salt.encode("ascii"),
                            n=16384, r=8, p=1, dklen=64)
        return hmac.compare_digest(dk.hex(), h)
    except Exception:
        return False


def create_session_token():
    return secrets.token_hex(32)


def _b64url(data):
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def sign(value):
    return _b64url(hmac.new(SECRET.encode("utf-8"), str(value).encode("utf-8"),
                            hashlib.sha256).digest())


def build_session_cookie(token):
    payload = f"{token}.{sign(token)}"
    return (f"{SESSION_COOKIE}={payload}; HttpOnly; Path=/; "
            f"Max-Age={SESSION_MAX_AGE}; SameSite=Lax")


def build_clear_cookie():
    return f"{SESSION_COOKIE}=; HttpOnly; Path=/; Max-Age=0; SameSite=Lax"


def parse_session_token(cookie_header):
    """从 Cookie 头解析并校验签名，返回 token 或 None。"""
    if not cookie_header:
        return None
    m = re.search(r"(?:^|;\s*)tp_session=([^;]+)", cookie_header)
    if not m:
        return None
    payload = m.group(1)
    if "." not in payload:
        return None
    token, sig = payload.rsplit(".", 1)
    expected = sign(token)
    if len(sig) != len(expected) or not hmac.compare_digest(sig, expected):
        return None
    return token


def random_id():
    return secrets.token_hex(16)


def sanitize_user(user):
    if not user:
        return None
    safe = dict(user)
    safe.pop("passwordHash", None)
    return safe
