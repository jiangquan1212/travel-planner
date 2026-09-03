# -*- coding: utf-8 -*-
"""关系型数据层（SQLite，零第三方依赖）。

- 通过环境变量 TP_DB_FILE 启用（如 TP_DB_FILE=data/travel_planner.db）
- 未启用时回退原 JSON 文件存储，接口保持不变
- 每个集合一张表：(id PK, userId, doc JSON)，带事务与索引，避免并发写损坏
- 同类 SQL 可平滑迁移到 MySQL/PostgreSQL（改连接字符串与驱动即可）
"""
import json
import os
import sqlite3
from pathlib import Path

# Collection 名（VectorStore/GuideStore 属向量/文档存储，不在此列）
COLLECTION_NAMES = ["users", "sessions", "conversations", "audit",
                    "favorites", "reset_requests", "memory", "feedback"]


def db_file():
    return os.environ.get("TP_DB_FILE", "").strip()


def db_enabled():
    return bool(db_file())


def _conn():
    conn = sqlite3.connect(db_file())
    conn.row_factory = sqlite3.Row
    return conn


def init_schema():
    if not db_enabled():
        return
    Path(db_file()).parent.mkdir(parents=True, exist_ok=True)
    conn = _conn()
    for name in COLLECTION_NAMES:
        conn.execute(
            f'CREATE TABLE IF NOT EXISTS "{name}" ('
            'id TEXT PRIMARY KEY, userId TEXT, doc TEXT NOT NULL)')
        conn.execute(f'CREATE INDEX IF NOT EXISTS idx_{name}_user ON "{name}"(userId)')
    conn.commit()
    conn.close()


def _esc(name):
    assert name in COLLECTION_NAMES, f"非法集合: {name}"
    return f'"{name}"'


def all(name):
    conn = _conn()
    rows = conn.execute(f'SELECT doc FROM {_esc(name)}').fetchall()
    conn.close()
    return [json.loads(r["doc"]) for r in rows]


def get(name, obj_id):
    conn = _conn()
    row = conn.execute(f'SELECT doc FROM {_esc(name)} WHERE id=?', (obj_id,)).fetchone()
    conn.close()
    return json.loads(row["doc"]) if row else None


def insert(name, record):
    conn = _conn()
    conn.execute(f'INSERT INTO {_esc(name)}(id, userId, doc) VALUES(?,?,?)',
                 (record["id"], record.get("userId"), json.dumps(record, ensure_ascii=False)))
    conn.commit()
    conn.close()


def update(name, obj_id, patch):
    conn = _conn()
    row = conn.execute(f'SELECT doc FROM {_esc(name)} WHERE id=?', (obj_id,)).fetchone()
    if not row:
        conn.close()
        return None
    merged = {**json.loads(row["doc"]), **patch, "id": obj_id}
    conn.execute(f'UPDATE {_esc(name)} SET doc=? WHERE id=?',
                 (json.dumps(merged, ensure_ascii=False), obj_id))
    conn.commit()
    conn.close()
    return merged


def replace(name, items):
    """整体替换（清空审计等场景），事务内完成。"""
    conn = _conn()
    conn.execute(f'DELETE FROM {_esc(name)}')
    conn.executemany(
        f'INSERT INTO {_esc(name)}(id, userId, doc) VALUES(?,?,?)',
        [(r["id"], r.get("userId"), json.dumps(r, ensure_ascii=False)) for r in items])
    conn.commit()
    conn.close()


def remove(name, obj_id):
    conn = _conn()
    cur = conn.execute(f'DELETE FROM {_esc(name)} WHERE id=?', (obj_id,))
    conn.commit()
    conn.close()
    return cur.rowcount > 0
