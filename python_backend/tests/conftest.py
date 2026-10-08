# -*- coding: utf-8 -*-
import os
import sys

import pytest

# 让 pytest 能 import python_backend 下的模块
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def _disable_embedding(monkeypatch):
    """单元测试默认不请求真实 embedding API。"""
    import guide_store
    import vector_store

    monkeypatch.setattr(vector_store, "embedding_enabled", lambda: False)
    monkeypatch.setattr(guide_store, "embedding_enabled", lambda: False)
