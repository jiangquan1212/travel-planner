# -*- coding: utf-8 -*-
"""初始化演示账号与偏好（克隆仓库后运行一次）。

用法:  python python_backend/seed.py
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from auth import hash_password, random_id  # noqa: E402
from vector_store import VectorStore  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DATA.mkdir(parents=True, exist_ok=True)


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load(name):
    p = DATA / f"{name}.json"
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []


def save(name, data):
    p = DATA / f"{name}.json"
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def ensure_user(username, password, role):
    users = load("users")
    for u in users:
        if u["username"].lower() == username.lower():
            print(f"[seed] {username} 已存在，跳过")
            return u
    u = {"id": random_id(), "username": username, "email": None,
         "passwordHash": hash_password(password), "role": role,
         "status": "active", "createdAt": now()}
    users.append(u)
    save("users", users)
    print(f"[seed] 已创建 {role}: {username} / {password}")
    return u


admin = ensure_user("admin", "admin123", "admin")
user = ensure_user("user", "user123", "user")

vs = VectorStore(DATA / "preferences.json")
existing = {d["text"] for d in vs.list(user["id"])}
for text in ["喜欢美食和当地特色小吃", "偏好经济型住宿，注重性价比", "喜欢自然风光，不喜欢人多的热门景点"]:
    if text not in existing:
        vs.add(user["id"], text, "其他", 3)
        print(f"[seed] 已为 user 添加偏好: {text}")

print("[seed] done. demo accounts: admin/admin123, user/user123")
