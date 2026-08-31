# -*- coding: utf-8 -*-
"""多 Agent 协作：行程 Agent + 预算 Agent + 天气 Agent 并行，再由协调者汇总。

对应课程要求 W11（多 Agent 协作：行程规划、预算控制、天气查询各司其职）。
"""

import json
import os
from concurrent.futures import ThreadPoolExecutor

import requests

from tools import get_weather


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
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {cfg['key']}"},
        json={"model": model or cfg["model"], "stream": False, "messages": messages,
              "temperature": temperature, "max_tokens": max_tokens},
        timeout=90,
    )
    resp.raise_for_status()
    data = resp.json()
    return (data.get("choices") or [{}])[0].get("message", {}).get("content", "") or ""


def _weather_agent(city):
    """天气 Agent：调用实时天气工具并产出天气分析。"""
    if not city:
        return "未识别到目的地，无法查询天气。"
    w = get_weather(city)
    if "error" in w:
        return f"{city} 天气查询失败：{w['error']}"
    daily = "；".join(f"{d['date'][5:]} {d['tmin']}~{d['tmax']}°C" for d in w.get("daily", []))
    return f"{city} 当前 {w['temp']}°C，未来几天：{daily}。"


def _budget_agent(user_request, prefs, city):
    """预算 Agent：产出分项预算方案。"""
    prefs_txt = "；".join(prefs) if prefs else "无特别偏好"
    prompt = (
        "你是旅行【预算 Agent】，负责为旅行做分项预算。请基于以下用户需求与偏好，"
        "输出包含交通、住宿、餐饮、门票、购物/其他各分项与总额区间的预算方案（人民币）。"
        "控制在 300 字内，用列表呈现。\n"
        f"用户需求：{user_request}\n用户偏好：{prefs_txt}\n目的地：{city or '未指定'}"
    )
    return llm_complete([{"role": "system", "content": "你只输出中文预算方案。"},
                         {"role": "user", "content": prompt}], temperature=0.4, max_tokens=600)


def _itinerary_agent(user_request, prefs, city, weather_txt):
    """行程 Agent：产出按天行程。"""
    prefs_txt = "；".join(prefs) if prefs else "无特别偏好"
    prompt = (
        "你是旅行【行程 Agent】，负责产出按天（Day1/Day2…）的详细行程，"
        "每天包含上午/下午/晚上安排，并给出景点与美食建议。控制在 500 字内。\n"
        f"用户需求：{user_request}\n用户偏好：{prefs_txt}\n目的地：{city or '未指定'}\n天气参考：{weather_txt}"
    )
    return llm_complete([{"role": "system", "content": "你只输出中文按天行程。"},
                         {"role": "user", "content": prompt}], temperature=0.6, max_tokens=1000)


def _coordinator(user_request, city, weather, budget, itinerary):
    """协调者 Agent：将三个 Agent 的结果合并为最终完整方案。"""
    prompt = (
        "你是旅行规划的【总协调 Agent】，请把以下三个专业 Agent 的输出整理为一份完整、通顺、"
        "结构清晰的旅行方案（先行程、再预算、最后天气提醒），不要遗漏关键信息，控制在 900 字内。\n\n"
        f"用户需求：{user_request}\n目的地：{city or '未指定'}\n\n"
        f"【行程 Agent】\n{itinerary}\n\n【预算 Agent】\n{budget}\n\n【天气 Agent】\n{weather}"
    )
    return llm_complete([{"role": "system", "content": "你是中文旅行规划总协调者。"},
                         {"role": "user", "content": prompt}], temperature=0.5, max_tokens=1600)


def run_multi_agent(user_request, prefs, city):
    """并行运行 行程/预算/天气 三个 Agent，返回各部分与最终合并方案。"""
    weather = _weather_agent(city)
    with ThreadPoolExecutor(max_workers=3) as pool:
        f_budget = pool.submit(_budget_agent, user_request, prefs, city)
        f_itinerary = pool.submit(_itinerary_agent, user_request, prefs, city, weather)
        budget = f_budget.result()
        itinerary = f_itinerary.result()
    merged = _coordinator(user_request, city, weather, budget, itinerary)
    return {
        "weather": weather,
        "budget": budget,
        "itinerary": itinerary,
        "merged": merged,
        "agents": ["天气Agent", "预算Agent", "行程Agent", "总协调Agent"],
    }
