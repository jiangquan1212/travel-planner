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


AMAP_GEO_URL = "https://restapi.amap.com/v3/geocode/geo"

# 省 / 直辖市 / 自治区 → 高德 adcode 的前两位。
# 用途：拿到多条候选时，用它判断哪一条落在期望的省级行政区里（Q11 的校验层）。
_PROV_ADCODE = {
    "北京": "11", "天津": "12", "河北": "13", "山西": "14", "内蒙古": "15",
    "辽宁": "21", "吉林": "22", "黑龙江": "23", "上海": "31", "江苏": "32",
    "浙江": "33", "安徽": "34", "福建": "35", "江西": "36", "山东": "37",
    "河南": "41", "湖北": "42", "湖南": "43", "广东": "44", "广西": "45",
    "海南": "46", "重庆": "50", "四川": "51", "贵州": "52", "云南": "53",
    "西藏": "54", "陕西": "61", "甘肃": "62", "青海": "63", "宁夏": "64",
    "新疆": "65",
}


def _geocode(city, province=""):
    """城市名 → {"lat", "lon", "resolved"}；失败返回 {"error": ...}。

    为什么是两个数据源（2026-09-23 实测，见 experiments/q11_geocode_census.py）：
    - Open-Meteo 的 geocoding 传 `language=zh` 时，是**拿中文名去匹配条目**，
      而它库里中国大城市的主名是拼音，中文名多挂在**同名小村镇**上 ——
      实测项目内置 16 城**只有 10 个对**，其中 5 个**静默返回别省**的同名地点。
    - 高德是国内数据源，中文明细齐；同一批城市实测 **12/12 全对**。
    → 优先高德；没配 AMAP_KEY 或高德调用失败时回落 Open-Meteo。

    为什么要校验、为什么要报错（**这一层跟数据源无关，两个源都必须做**）：
    - **换数据源降低的是【概率】，"无条件信第一条"决定的是【性质】。**
      高德同样会返回多条同名候选，实测：
        西湖 → 台湾省苗栗县西湖乡 排第 1，**浙江省杭州市西湖区排第 3**
        新城 → 台湾省花莲县新城乡 排第 1，陕西省西安市新城区 排第 2
      直接取第一条 → 返回另一个城市的天气，**坐标合法、天气真实、全程不报错**。
    - 所以这里做两件事：
      ① 给了 province → 只认 adcode 落在该省的候选；一条都没有 → **返回 error**
      ② 没给 province 且候选多于一条 → **不猜**，返回"地名有歧义"的 error
    → 原则：**宁可报错让上层/用户知道没查准，也不静默拿别省的数据往下写。**
    """
    key = os.environ.get("AMAP_KEY", "").strip()
    if key:
        try:
            # ⚠️ 省份必须拼进 address，**不能**用高德的 `city` 参数：
            #    实测 address=白沙 + city=海南 → 直接 0 条，
            #    而 address=海南省白沙 → 正确拿到「海南省白沙黎族自治县」。
            #    高德的 `city` 参数不是"省份过滤器"，传进去会把查询收成空。
            addr = f"{province}{city}" if province else city
            data = requests.get(AMAP_GEO_URL,
                                params={"address": addr, "key": key, "output": "json"},
                                timeout=15).json()
            geos = (data.get("geocodes") or []) if data.get("status") == "1" else []
            pref = _PROV_ADCODE.get(province) if province else None
            picked = None
            if pref:
                picked = next((g for g in geos
                               if str(g.get("adcode", ""))[:2] == pref), None)
                if not geos:
                    return {"error": f"未找到地名「{city}」"}
                if not picked:
                    return {"error": f"地名「{city}」在{province}没有匹配地点；"
                                     f"其余 {len(geos)} 条同名地点在其他省份，未采用"}
            elif len(geos) == 1:
                picked = geos[0]
            else:
                # 没给省份时：**只认唯一的「市 / 省」级候选**。
                # 实测 level 就是这个用途：
                #   西安 → [陕西省西安市(市), 黑龙江牡丹江市西安区(区县), 吉林辽源市西安区(区县)]
                #          → 唯一的"市"级 = 西安 ✅
                #   西湖 → [苗栗县西湖乡, 南昌市西湖区, 杭州市西湖区] 全是"区县"级
                #          → 有歧义，报错 ✅（宁可报错，也不能猜一个杭州西湖给用户）
                top = [g for g in geos if g.get("level") in ("市", "省")]
                if len(top) == 1:
                    picked = top[0]
                else:
                    same = top or geos
                    names = "、".join(g.get("formatted_address", "?") for g in same[:3])
                    return {"error": f"地名「{city}」有歧义（{len(same)} 个同名地点："
                                     f"{names}…），请补充省份后再查"}
            if picked:
                lon, _, lat = (picked.get("location") or "").partition(",")
                if lon and lat:
                    return {"lat": float(lat), "lon": float(lon),
                            "resolved": picked.get("formatted_address") or city}
        except Exception:
            pass          # 高德不可用不算错误，交给下面的回落
    try:
        res = requests.get("https://geocoding-api.open-meteo.com/v1/search",
                           params={"name": city, "count": 1, "language": "zh",
                                   "format": "json"},
                           timeout=15).json().get("results") or []
    except Exception:
        return {"error": f"未找到城市：{city}"}
    if not res:
        return {"error": f"未找到城市：{city}"}
    loc = res[0]
    return {"lat": loc.get("latitude"), "lon": loc.get("longitude"),
            "resolved": loc.get("name") or city}


def get_weather(city, province=""):
    """真实天气（Open-Meteo），缓存 30 分钟。

    province 可选：用户明确提到省份时传进来（如「江西婺源」→ city=婺源, province=江西），
    用于消解同名地名 —— 不传时会退化成"多候选就报错"。
    """
    cache_key = f"weather:{province}:{city}"
    hit = cache_get(cache_key)
    if hit:
        try:
            return json.loads(hit)
        except Exception:
            pass
    try:
        loc = _geocode(city, province)
        if "error" in loc:
            return loc
        w = requests.get("https://api.open-meteo.com/v1/forecast",
                         params={"latitude": loc["lat"], "longitude": loc["lon"],
                                 "current_weather": "true",
                                 "daily": "weathercode,temperature_2m_max,temperature_2m_min",
                                 "timezone": "auto", "forecast_days": 5},
                         timeout=15).json()
        cur = w.get("current_weather", {})
        result = {
            "city": city,
            # 解析到的规范地名：让「静默返回别省数据」变得可见
            # （只看 city 看不出差异 —— 输入"青岛"、拿到辽宁的天气，两者都显示"青岛"）
            "resolved": loc.get("resolved"),
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
        return get_weather(args.get("city") or "", args.get("province") or "")
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
        tag = "（内置演示，非实时）" if result.get("source", "").startswith("内置") else ""
        return f"航班：{result['from']}→{result['to']} 最低 ¥{low}{tag}"
    if name == "search_hotels":
        hs = result.get("hotels", [])
        if not hs:
            return "酒店：暂无"
        priced = [h["price_per_night"] for h in hs if h.get("price_per_night") is not None]
        if not priced:
            return f"酒店：{result.get('city', '')} {len(hs)} 家真实酒店（高德，含地址/电话）"
        low = min(priced)
        tag = "（内置演示，非实时）" if result.get("source", "").startswith("内置") else ""
        return f"酒店：{result.get('city', '')} 最低 ¥{low}/晚{tag}"
    if name == "search_attractions":
        tag = "" if result.get("source") == "高德地图" else "（内置推荐）"
        return f"景点：{result['city']} {len(result.get('attractions', []))} 个推荐{tag}"
    return f"工具：{name}"


TOOL_DEFS = [
    {"type": "function", "function": {
        "name": "get_weather", "description": "查询指定城市当前的实时天气与未来几天预报",
        "parameters": {"type": "object", "properties": {
            "city": {"type": "string", "description": "城市名，如 杭州"},
            # 用户话里提到省份/所属大区时必须带上 —— 中国有大量同名地名
            # （西湖/新城/白沙/太平…），不带省份会被判为歧义而查不到。
            "province": {"type": "string",
                         "description": "省份或直辖市，如 江西、浙江。"
                                        "用户问题里提到省份时【必须】一起传，"
                                        "否则遇到同名地名会返回歧义错误"}},
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
