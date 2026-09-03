# -*- coding: utf-8 -*-
"""工具函数测试：无 Key 时回退内置数据且不崩溃。"""
import os
os.environ.pop("AMAP_KEY", None)
os.environ.pop("FLIGHT_API_URL", None)

import tools


def test_search_flights_fallback():
    r = tools.search_flights("上海", "成都", "2026-09-10")
    assert r.get("flights") and r.get("source") == "内置演示数据"


def test_search_hotels_fallback():
    r = tools.search_hotels("成都")
    assert r.get("hotels") and r.get("source") == "内置演示数据"


def test_search_attractions():
    r = tools.search_attractions("成都")
    assert r.get("attractions") and "宽窄巷子" in [a["name"] for a in r["attractions"]]


def test_summarize_tool_no_crash():
    h = tools.search_hotels("成都")
    assert isinstance(tools.summarize_tool("search_hotels", h), str)
    f = tools.search_flights("上海", "成都")
    assert isinstance(tools.summarize_tool("search_flights", f), str)
