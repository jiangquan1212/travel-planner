# -*- coding: utf-8 -*-
"""真实数据接入层。

- 高德地图 POI：真实酒店 / 景点（需免费 AMAP_KEY，.env 配置）
- 航班：可接入第三方真实接口（FLIGHT_API_URL / FLIGHT_API_KEY，按需配置）
- 未配置 Key 或调用失败时返回 None，由 tools.py 自动回退内置数据
"""
import os

import requests

AMAP_URL = "https://restapi.amap.com/v3/place/text"


def amap_key():
    return os.environ.get("AMAP_KEY", "")


def flight_api():
    return os.environ.get("FLIGHT_API_URL", ""), os.environ.get("FLIGHT_API_KEY", "")


def amap_enabled():
    return bool(amap_key())


def _amap_poi(city, keywords, limit=5):
    """高德文字搜索 POI，失败或未配置返回 None。"""
    key = amap_key()
    if not key:
        return None
    try:
        r = requests.get(AMAP_URL, params={
            "key": key, "keywords": keywords, "city": city,
            "offset": limit, "page": 1, "extensions": "base",
        }, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        r.raise_for_status()
        data = r.json()
        if data.get("status") != "1" or not data.get("pois"):
            return None
        pois = []
        for p in data.get("pois", [])[:limit]:
            ty = (p.get("type") or "").split(";")
            pois.append({
                "name": p.get("name", ""),
                "type": ty[-1] if ty else "POI",
                "address": (p.get("address") or "") or (p.get("cityname") or ""),
                "tel": p.get("tel") or "",
                "location": p.get("location") or "",
                "pname": p.get("pname") or "",
                "cityname": p.get("cityname") or "",
                "adname": p.get("adname") or "",
            })
        return pois
    except Exception as e:
        print(f"[providers] 高德 POI 请求失败: {e}")
        return None


def real_hotels(city, limit=5):
    """真实酒店（高德）。"""
    pois = _amap_poi(city, "酒店", limit)
    if not pois:
        return None
    return {"city": city, "source": "高德地图", "hotels": [
        {"name": p["name"], "price_per_night": None, "rating": None,
         "district": (p["adname"] or p["cityname"]) + " " + p["address"],
         "address": p["address"], "tel": p["tel"],
         "in_budget": True} for p in pois]}


def real_attractions(city, limit=8):
    """真实景点（高德）。"""
    pois = _amap_poi(city, "景点 旅游", limit)
    if not pois:
        return None
    return {"city": city, "source": "高德地图", "attractions": [
        {"name": p["name"], "type": p["type"] or "景点", "note": p["address"],
         "address": p["address"], "tel": p["tel"]} for p in pois]}


def real_flights(from_city, to_city, date):
    """真实航班（第三方接口，需配置 FLIGHT_API_URL/KEY）；未配置返回 None。

    接口约定：GET {FLIGHT_API_URL}?from={from_city}&to={to_city}&date={date}
    返回 JSON: {"flights":[{airline,flight_no,departure,arrival,price,...}]}
    """
    url, key = flight_api()
    if not (url and key):
        return None
    try:
        r = requests.get(url, params={
            "from": from_city, "to": to_city, "date": date, "key": key,
        }, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        r.raise_for_status()
        data = r.json()
        if data.get("flights"):
            return {"from": from_city, "to": to_city, "date": date,
                    "source": "第三方航班接口", "flights": data["flights"]}
    except Exception as e:
        print(f"[providers] 航班接口请求失败: {e}")
    return None
