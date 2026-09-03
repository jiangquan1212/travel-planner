# -*- coding: utf-8 -*-
"""Function Calling 多工具：天气（真实）/ 航班 / 酒店 / 景点。

对应课程要求 W5/W9（Function Calling 多工具并行调用）。
- get_weather      使用 Open-Meteo 实时数据（带缓存）
- search_flights   确定性模拟航班（基于城市/日期哈希）
- search_hotels    确定性模拟酒店
- search_attractions 内置热门城市景点库 + 兜底
"""

import hashlib
import json
import os
import requests

from cache import cache_get, cache_set
import providers

OPENWEATHER = None  # 复用 main 中的天气函数（延迟注入）

WMO_CODES = {
    0: ("晴", "☀️"), 1: ("大部晴朗", "🌤"), 2: ("多云", "⛅"), 3: ("阴", "☁️"),
    45: ("雾", "🌫"), 48: ("冻雾", "🌫"),
    51: ("小毛毛雨", "🌦"), 53: ("毛毛雨", "🌦"), 55: ("大毛毛雨", "🌦"),
    56: ("冻毛毛雨", "🌧"), 57: ("强冻毛毛雨", "🌧"),
    61: ("小雨", "🌧"), 63: ("中雨", "🌧"), 65: ("大雨", "🌧"),
    66: ("冻雨", "🌧"), 67: ("强冻雨", "🌧"),
    71: ("小雪", "🌨"), 73: ("中雪", "🌨"), 75: ("大雪", "❄️"),
    77: ("雪粒", "🌨"),
    80: ("小阵雨", "🌦"), 81: ("阵雨", "🌦"), 82: ("强阵雨", "⛈"),
    85: ("小阵雪", "🌨"), 86: ("强阵雪", "❄️"),
    95: ("雷暴", "⛈"), 96: ("雷暴伴小冰雹", "⛈"), 99: ("雷暴伴大冰雹", "⛈"),
}


def _wmo(code):
    code = int(code)
    if code in WMO_CODES:
        return WMO_CODES[code]
    if 51 <= code <= 57:
        return WMO_CODES[51]
    if 61 <= code <= 67:
        return WMO_CODES[61]
    if 71 <= code <= 77:
        return WMO_CODES[71]
    if 80 <= code <= 82:
        return WMO_CODES[80]
    if 85 <= code <= 86:
        return WMO_CODES[85]
    if code >= 95:
        return WMO_CODES[95]
    return ("未知", "🌡")


# ---------- 热门城市景点库 ----------
ATTRACTIONS = {
    "北京": ["故宫博物院", "八达岭长城", "天坛公园", "颐和园", "南锣鼓巷"],
    "上海": ["外滩", "东方明珠", "豫园", "迪士尼乐园", "武康路"],
    "成都": ["宽窄巷子", "锦里古街", "大熊猫繁育研究基地", "都江堰", "春熙路"],
    "杭州": ["西湖", "灵隐寺", "西溪湿地", "宋城", "河坊街"],
    "大理": ["洱海", "大理古城", "苍山", "双廊古镇", "崇圣寺三塔"],
    "三亚": ["亚龙湾", "蜈支洲岛", "天涯海角", "南山文化旅游区", "大小洞天"],
    "厦门": ["鼓浪屿", "厦门大学", "环岛路", "曾厝垵", "南普陀寺"],
    "丽江": ["丽江古城", "玉龙雪山", "束河古镇", "泸沽湖", "蓝月谷"],
    "西安": ["兵马俑", "大雁塔", "西安城墙", "回民街", "华清宫"],
    "重庆": ["洪崖洞", "解放碑", "磁器口古镇", "长江索道", "武隆天生三桥"],
    "广州": ["广州塔", "沙面", "白云山", "陈家祠", "珠江夜游"],
    "深圳": ["世界之窗", "深圳湾公园", "大梅沙", "莲花山公园", "欢乐海岸"],
    "青岛": ["栈桥", "八大关", "崂山", "五四广场", "啤酒博物馆"],
    "桂林": ["漓江", "阳朔西街", "象鼻山", "龙脊梯田", "十里画廊"],
    "长沙": ["橘子洲", "岳麓山", "太平街", "湖南省博物馆", "五一广场"],
    "南京": ["中山陵", "夫子庙", "总统府", "明孝陵", "玄武湖"],
}

AIRLINES = ["国航", "东航", "南航", "海航", "川航", "厦航", "春秋航空", "吉祥航空"]
HOTEL_NAMES = ["如家精选", "汉庭优佳", "全季酒店", "亚朵酒店", "维也纳国际",
               "桔子水晶", "丽枫酒店", "希尔顿欢朋"]


def _seed(*parts):
    h = hashlib.md5("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()
    return int(h[:8], 16)


def get_weather(city):
    """真实天气（Open-Meteo），缓存 30 分钟。"""
    cache_key = f"weather:{city}"
    hit = cache_get(cache_key)
    if hit:
        try:
            return json.loads(hit)
        except Exception:
            pass
    try:
        geo = requests.get("https://geocoding-api.open-meteo.com/v1/search",
                           params={"name": city, "count": 1, "language": "zh", "format": "json"},
                           timeout=15).json().get("results") or []
        if not geo:
            return {"error": f"未找到城市：{city}"}
        loc = geo[0]
        w = requests.get("https://api.open-meteo.com/v1/forecast",
                         params={"latitude": loc["latitude"], "longitude": loc["longitude"],
                                 "current_weather": "true",
                                 "daily": "weathercode,temperature_2m_max,temperature_2m_min",
                                 "timezone": "auto", "forecast_days": 5},
                         timeout=15).json()
        cur = w.get("current_weather", {})
        result = {
            "city": loc.get("name") or city,
            "temp": cur.get("temperature"),
            "windspeed": cur.get("windspeed"),
            "weathercode": cur.get("weathercode"),
            "daily": [{"date": d, "weathercode": w["daily"]["weathercode"][i],
                       "tmax": w["daily"]["temperature_2m_max"][i],
                       "tmin": w["daily"]["temperature_2m_min"][i]}
                      for i, d in enumerate(w["daily"]["time"])],
        }
        cache_set(cache_key, json.dumps(result, ensure_ascii=False), ttl=1800)
        return result
    except Exception as e:
        return {"error": f"天气查询失败：{e}"}


def search_flights(from_city, to_city, date="2026-09-01"):
    """真实航班（若配置接口）否则确定性模拟。"""
    real = providers.real_flights(from_city, to_city, date)
    if real:
        return real
    flights = []
    base = _seed("flight", from_city, to_city, date)
    for i in range(4):
        s = base + i * 7919
        airline = AIRLINES[s % len(AIRLINES)]
        price = 380 + (s % 60) * 25
        depart = f"{(s % 16) + 6:02d}:{s % 60:02d}"
        duration = 95 + (s % 200)
        arrive_min = (int(depart[:2]) * 60 + int(depart[3:]) + duration) % 1440
        flights.append({
            "airline": f"{airline}",
            "flight_no": f"{airline[0]}{s % 1000:03d}",
            "from": from_city, "to": to_city, "date": date,
            "departure": depart,
            "arrival": f"{arrive_min // 60:02d}:{arrive_min % 60:02d}",
            "duration_min": duration,
            "price": price,
            "cabin": "经济舱",
        })
    return {"from": from_city, "to": to_city, "date": date,
            "source": "内置演示数据", "flights": flights}


def search_hotels(city, checkin="2026-09-01", checkout="2026-09-03", budget=500):
    """真实酒店（高德，配 AMAP_KEY）否则确定性模拟。"""
    real = providers.real_hotels(city, 5)
    if real:
        return real
    hotels = []
    base = _seed("hotel", city, checkin, checkout)
    for i in range(5):
        s = base + i * 104729
        name = HOTEL_NAMES[s % len(HOTEL_NAMES)]
        price = 150 + (s % 60) * 15
        rating = round(3.8 + (s % 10) / 10.0, 1)
        hotels.append({
            "name": f"{name}（{city}）",
            "price_per_night": price,
            "rating": rating,
            "district": f"{city}{['市中心', '火车站', '景区周边', '老城区', '新城区'][s % 5]}",
            "in_budget": price <= budget,
        })
    return {"city": city, "checkin": checkin, "checkout": checkout,
            "budget": budget, "source": "内置演示数据", "hotels": hotels}


def search_attractions(city):
    """真实景点（高德，配 AMAP_KEY）否则内置热门库。"""
    real = providers.real_attractions(city, 8)
    if real:
        return real
    pool = ATTRACTIONS.get(city) or [
        f"{city}中央公园", f"{city}老城区", f"{city}博物馆",
        f"{city}滨江步道", f"{city}地标塔",
    ]
    attrs = [{"name": n, "type": "景点", "note": "建议游玩 2-4 小时"} for n in pool]
    return {"city": city, "source": "内置数据", "attractions": attrs}


def execute_tool(name, args):
    args = args or {}
    if name == "get_weather":
        return get_weather(args.get("city") or "")
    if name == "search_flights":
        return search_flights(args.get("from_city") or "", args.get("to_city") or "",
                              args.get("date") or "2026-09-01")
    if name == "search_hotels":
        return search_hotels(args.get("city") or "", args.get("checkin") or "2026-09-01",
                             args.get("checkout") or "2026-09-03",
                             int(args.get("budget") or 500))
    if name == "search_attractions":
        return search_attractions(args.get("city") or "")
    return {"error": f"未知工具：{name}"}


def summarize_tool(name, result):
    """生成给前端展示的一行摘要。"""
    if name == "get_weather":
        if "error" in result:
            return f"天气：{result['error']}"
        return f"天气：{result['city']} {result['temp']}°C"
    if name == "search_flights":
        flights = result.get("flights", [])
        if not flights:
            return "航班：暂无"
        low = min(f["price"] for f in flights)
        return f"航班：{result['from']}→{result['to']} 最低 ¥{low}"
    if name == "search_hotels":
        hs = result.get("hotels", [])
        if not hs:
            return "酒店：暂无"
        priced = [h["price_per_night"] for h in hs if h.get("price_per_night") is not None]
        if not priced:
            return f"酒店：{result.get('city', '')} {len(hs)} 家真实酒店（高德，含地址/电话）"
        low = min(priced)
        return f"酒店：{result.get('city', '')} 最低 ¥{low}/晚"
    if name == "search_attractions":
        return f"景点：{result['city']} {len(result.get('attractions', []))} 个推荐"
    return f"工具：{name}"


TOOL_DEFS = [
    {"type": "function", "function": {
        "name": "get_weather", "description": "查询指定城市当前的实时天气与未来几天预报",
        "parameters": {"type": "object", "properties": {"city": {"type": "string", "description": "城市名，如 杭州"}},
                       "required": ["city"]}}},
    {"type": "function", "function": {
        "name": "search_flights", "description": "查询两个城市之间的航班（含价格）",
        "parameters": {"type": "object", "properties": {
            "from_city": {"type": "string"}, "to_city": {"type": "string"},
            "date": {"type": "string", "description": "出行日期 YYYY-MM-DD"}},
            "required": ["from_city", "to_city"]}}},
    {"type": "function", "function": {
        "name": "search_hotels", "description": "查询目的地城市酒店（含价格与评分）",
        "parameters": {"type": "object", "properties": {
            "city": {"type": "string"}, "checkin": {"type": "string"},
            "checkout": {"type": "string"}, "budget": {"type": "integer"}},
            "required": ["city"]}}},
    {"type": "function", "function": {
        "name": "search_attractions", "description": "查询目的地城市的热门景点",
        "parameters": {"type": "object", "properties": {"city": {"type": "string"}},
                       "required": ["city"]}}},
]
