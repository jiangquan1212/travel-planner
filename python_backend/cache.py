# -*- coding: utf-8 -*-
"""缓存工具：优先 Redis（REDIS_URL），未配置时回退为进程内缓存。"""
import os
import threading
import time

try:
    import redis as _redis_lib
    _client = None
    _lock_init = threading.Lock()

    def _get_client():
        global _client
        url = os.environ.get("REDIS_URL")
        if not url:
            return None
        if _client is None:
            with _lock_init:
                if _client is None:
                    _client = _redis_lib.from_url(url, decode_responses=True)
        return _client
except Exception:
    def _get_client():
        return None

_mem = {}
_mem_lock = threading.Lock()


def cache_get(key):
    c = _get_client()
    if c:
        try:
            v = c.get(key)
            return v
        except Exception:
            pass
    with _mem_lock:
        item = _mem.get(key)
        if item and item[1] >= time.time():
            return item[0]
        return None


def cache_set(key, value, ttl=600):
    c = _get_client()
    if c:
        try:
            c.setex(key, ttl, value)
            return
        except Exception:
            pass
    with _mem_lock:
        _mem[key] = (value, time.time() + ttl)


def cache_delete(key):
    c = _get_client()
    if c:
        try:
            c.delete(key)
            return
        except Exception:
            pass
    with _mem_lock:
        _mem.pop(key, None)
