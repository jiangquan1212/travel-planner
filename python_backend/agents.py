# -*- coding: utf-8 -*-
"""多 Agent 协作（结合真实工具）：
先并行预取 天气/航班/酒店/景点 工具数据，再交给专业 Agent 分工生成，最后总协调者汇总。
"""
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import requests

from tools import get_weather, search_attractions, search_flights, search_hotels


def _cfg():
    return {
        "key": os.environ.get("OPENAI_API_KEY", ""),
        "base": os.environ.get("OPENAI_BASE_URL", "https://api.deepseek.com").rstrip("/"),
        "model": os.environ.get("TP_OPENAI_MODEL") or os.environ.get("OPENAI_MODEL") or "deepseek-chat",
    }


def llm_complete(messages, temperature=0.7, max_tokens=1500, model=None):
    cfg = _cfg()
    if not cfg["key"]:
        raise RuntimeError("AI 未配置")
    resp = requests.post(
        f"{cfg['base']}/chat/completions",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {cfg['key']}"},
        json={"model": model or cfg["model"], "stream": False, "messages": messages,
              "temperature": temperature, "max_tokens": max_tokens},
        timeout=90,
    )
    resp.raise_for_status()
    data = resp.json()
    return (data.get("choices") or [{}])[0].get("message", {}).get("content", "") or ""


# ---------- 工具结果压缩 ----------
def _weather_text(w):
    if not w or "error" in w:
        return "天气查询失败（可能网络问题）"
    daily = "；".join(f"{d['date'][5:]} {d['tmin']}~{d['tmax']}°C" for d in w.get("daily", []))
    return f"{w.get('city', '')} 当前 {w.get('temp')}°C，未来几天：{daily}。"


def _attractions_text(a):
    if not a or "error" in a or not a.get("attractions"):
        return "景点查询失败或暂无数据"
    names = "、".join(x["name"] for x in a["attractions"])
    return f"{a.get('city', '')} 热门景点：{names}。（来源：{a.get('source', '内置')}）"


def _flights_text(f):
    if not f or "error" in f or not f.get("flights"):
        return "航班查询失败或暂无数据"
    fl = f["flights"]
    low = min(x.get("price", 0) for x in fl)
    first = fl[0]
    return (f"{first.get('airline', '')} {first.get('flight_no', '')} {first.get('date', '')} "
            f"{first.get('departure', '')}-{first.get('arrival', '')} ¥{first.get('price')}（共{len(fl)}班，最低 ¥{low}，来源：{f.get('source', '内置')}）")


def _hotels_text(h):
    if not h or "error" in h or not h.get("hotels"):
        return "酒店查询失败或暂无数据"
    hs = h["hotels"]
    priced = [x for x in hs if x.get("price_per_night")]
    if priced:
        low = min(x["price_per_night"] for x in priced)
        sample = "；".join(f"{x['name']} ¥{x['price_per_night']}/晚 评分{x.get('rating')}" for x in hs[:3])
        return f"{h.get('city', '')} 酒店：{sample}。最低 ¥{low}/晚（来源：{h.get('source', '内置')}）"
    sample = "；".join(f"{x['name']} {x.get('district') or x.get('address') or ''}" for x in hs[:3])
    return f"{h.get('city', '')} 酒店推荐：{sample}。（来源：{h.get('source', '内置')}）"


def _budget_agent(user_request, prefs, city, flights_txt, hotels_txt, attractions_txt):
    prefs_txt = "；".join(prefs) if prefs else "无特别偏好"
    prompt = (
        "你是旅行【预算 Agent】，请基于以下真实工具数据做分项预算（交通/住宿/餐饮/门票/其他，含总额区间），"
        "控制在 350 字内，用列表呈现。\n"
        f"用户需求：{user_request}\n用户偏好：{prefs_txt}\n目的地：{city or '未指定'}\n"
        f"【航班工具】{flights_txt}\n【酒店工具】{hotels_txt}\n【景点工具】{attractions_txt}"
    )
    return llm_complete([{"role": "system", "content": "你只输出中文预算方案。"},
                         {"role": "user", "content": prompt}], temperature=0.4, max_tokens=700)


def _itinerary_agent(user_request, prefs, city, weather_txt, attractions_txt):
    prefs_txt = "；".join(prefs) if prefs else "无特别偏好"
    prompt = (
        "你是旅行【行程 Agent】，请基于真实景点与天气数据产出按天（Day1/Day2…）详细行程，"
        "每天含上午/下午/晚上，给出景点与美食建议。控制在 500 字内。\n"
        f"用户需求：{user_request}\n用户偏好：{prefs_txt}\n目的地：{city or '未指定'}\n"
        f"【天气工具】{weather_txt}\n【景点工具】{attractions_txt}"
    )
    return llm_complete([{"role": "system", "content": "你只输出中文按天行程。"},
                         {"role": "user", "content": prompt}], temperature=0.6, max_tokens=1100)


def _coordinator(user_request, city, weather_txt, budget, itinerary):
    prompt = (
        "你是旅行规划的【总协调 Agent】，请把以下专业 Agent 的输出整理为一份完整、通顺、结构清晰的旅行方案"
        "（先行程、再预算、最后天气提醒），不要遗漏关键信息，控制在 900 字内。\n\n"
        f"用户需求：{user_request}\n目的地：{city or '未指定'}\n\n"
        f"【行程 Agent】\n{itinerary}\n\n【预算 Agent】\n{budget}\n\n【天气 Agent】\n{weather_txt}"
    )
    return llm_complete([{"role": "system", "content": "你是中文旅行规划总协调者。"},
                         {"role": "user", "content": prompt}], temperature=0.5, max_tokens=1600)


def _guess_from_city(request):
    m = re.search(r"从\s*([\u4e00-\u9fa5]{2,4})\s*(?:出发)?", request)
    return m.group(1) if m else "北京"


def run_multi_agent(user_request, prefs, city):
    """并行预取 4 路工具数据 → 3 个 Agent 并行分工 → 总协调者汇总。"""
    city = city or ""
    from_city = _guess_from_city(user_request)
    date = (datetime.now(timezone.utc) + timedelta(days=7)).strftime("%Y-%m-%d")
    checkin = date
    checkout = (datetime.now(timezone.utc) + timedelta(days=9)).strftime("%Y-%m-%d")

    tool_raw = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {}
        if city:
            futures["weather"] = pool.submit(get_weather, city)
            futures["attractions"] = pool.submit(search_attractions, city)
            futures["hotels"] = pool.submit(search_hotels, city, checkin, checkout, 500)
        futures["flights"] = pool.submit(search_flights, from_city, city or "杭州", date)
        for name, f in futures.items():
            try:
                tool_raw[name] = f.result()
                if name == "weather" and isinstance(tool_raw[name], dict) and "error" in tool_raw[name]:
                    time.sleep(0.8)
                    tool_raw[name] = get_weather(city)
            except Exception as e:
                tool_raw[name] = {"error": str(e)}

    weather_txt = _weather_text(tool_raw.get("weather"))
    attractions_txt = _attractions_text(tool_raw.get("attractions"))
    flights_txt = _flights_text(tool_raw.get("flights"))
    hotels_txt = _hotels_text(tool_raw.get("hotels"))

    tool_list = [
        {"name": "get_weather", "summary": weather_txt[:50]},
        {"name": "search_flights", "summary": flights_txt[:50]},
        {"name": "search_hotels", "summary": hotels_txt[:50]},
        {"name": "search_attractions", "summary": attractions_txt[:50]},
    ]

    with ThreadPoolExecutor(max_workers=3) as pool:
        f_budget = pool.submit(_budget_agent, user_request, prefs, city, flights_txt, hotels_txt, attractions_txt)
        f_itinerary = pool.submit(_itinerary_agent, user_request, prefs, city, weather_txt, attractions_txt)
        budget = f_budget.result()
        itinerary = f_itinerary.result()

    merged = _coordinator(user_request, city, weather_txt, budget, itinerary)
    return {
        "weather": weather_txt,
        "budget": budget,
        "itinerary": itinerary,
        "merged": merged,
        "agents": ["天气Agent(工具:天气)", "预算Agent(工具:航班+酒店+景点)", "行程Agent(工具:景点+天气)", "总协调Agent"],
        "tools": tool_list,
    }
