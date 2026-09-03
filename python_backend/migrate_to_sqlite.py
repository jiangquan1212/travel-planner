# -*- coding: utf-8 -*-
"""把 JSON 文件存储迁移到 SQLite。

用法:  TP_DB_FILE=data/travel_planner.db python python_backend/migrate_to_sqlite.py
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def main():
    os.environ.setdefault("TP_DB_FILE", str(DATA / "travel_planner.db"))
    db.init_schema()
    total = 0
    for name in db.COLLECTION_NAMES:
        jf = DATA / f"{name}.json"
        rows = []
        if jf.exists():
            try:
                rows = json.loads(jf.read_text(encoding="utf-8")) or []
            except Exception as e:
                print(f"[migrate] 跳过 {name}: {e}")
                continue
        existing = db.all(name)
        if existing:
            print(f"[migrate] {name} 已存在 {len(existing)} 条，跳过")
            continue
        if rows:
            db.replace(name, rows)
            total += len(rows)
        print(f"[migrate] {name}: 导入 {len(rows)} 条")
    print(f"[migrate] 完成，共导入 {total} 条到 {os.environ['TP_DB_FILE']}")


if __name__ == "__main__":
    main()
