# -*- coding: utf-8 -*-
"""看日志：把 data/llm_calls.jsonl 的最后 N 条打成人类可读的格式。

用法（在 python_backend 目录下）：
    .\\.venv\\Scripts\\python.exe show_log.py            # 看最后 10 条
    .\\.venv\\Scripts\\python.exe show_log.py 30         # 看最后 30 条
    .\\.venv\\Scripts\\python.exe show_log.py 30 chat    # 只看主对话链路
    .\\.venv\\Scripts\\python.exe show_log.py 30 agents  # 只看多 Agent 链路
    .\\.venv\\Scripts\\python.exe show_log.py 30 sp      # 只看检索情况(system_prompt)

为什么需要它：日志是 JSONL（一行一条 JSON），
`tail` 出来的原始行有 200+ 字符、字段挤在一起，排查时读不动。
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_LOG = Path(__file__).resolve().parent.parent / "data" / "llm_calls.jsonl"


def log_path():
    return Path(os.environ.get("TP_LLM_LOG", "").strip() or DEFAULT_LOG)


def local_time(ts):
    """UTC ISO 串 → 本地时区 'MM-DD HH:MM:SS'。"""
    try:
        dt = datetime.fromisoformat(ts).astimezone()
        return dt.strftime("%m-%d %H:%M:%S")
    except Exception:
        return ts or "?"


def _args_text(a):
    """tool_args 里的一条 → 'city=南昌' 这种短文本。"""
    if not isinstance(a, dict):
        return str(a)
    args = a.get("args")
    if isinstance(args, dict) and args:
        return " ".join(f"{k}={v}" for k, v in args.items())
    return "（无参数）"


def _sig(a):
    """一条调用的【参数指纹】，用来判断两次同名调用是不是同参。"""
    raw = a.get("args") if isinstance(a, dict) else a
    return json.dumps(raw, ensure_ascii=False, sort_keys=True)


def _dup_verdict(names, args_list):
    """同名调用 → 用参数判定到底是「真重复」还是「合理扇出」。

    ⚠️ 这一行就是当初补 tool_args 的全部理由：
       同名同参 = 纯浪费，该去重；同名不同参 = 合理扇出，去重会把正确结果删掉。
    """
    pairs = list(zip(names, args_list))
    parts = []
    for n in sorted(set(names)):
        same = [a for nn, a in pairs if nn == n]
        if len(same) < 2:
            continue
        uniq = {_sig(a) for a in same}
        if len(uniq) == 1:
            parts.append(f"{n} ×{len(same)} 【参数完全相同 → 真重复，值得去重】")
        else:
            parts.append(f"{n} ×{len(same)} 【参数有 {len(uniq)} 种 → 合理扇出，不要按名字去重】")
    return "同名判定：" + "；".join(parts) if parts else ""


def fmt(rec):
    """一条记录 → 两三行可读文本。"""
    link = rec.get("link", "?")
    event = rec.get("event", "?")
    head = f"{local_time(rec.get('ts'))}  [{link}] {event}"

    if event == "system_prompt":
        query = (rec.get("query") or "").replace("\n", " ")
        hits = rec.get("kb_hits", "?")
        tags = rec.get("kb_tags") or []
        # 命中 0 条要显眼 —— 这是排查检索问题时的第一眼
        hit_str = f"命中 {hits} 条" if hits else "⚠ 命中 0 条（检索没给东西）"
        lines = [
            f"{head}   用户 {rec.get('user_id', '?')}",
            f"    问：{query}",
            f"    检索：{hit_str}   长期记忆 {rec.get('mem_count', '?')} 条   "
            f"拼出 prompt {rec.get('prompt_len', '?')} 字",
        ]
        if tags:
            lines.append(f"    命中内容：{' | '.join(tags)}")
        return "\n".join(lines)

    if event == "llm_call":
        model = rec.get("model", "?")
        rnd = rec.get("round", "?")
        ms = rec.get("elapsed_ms", "?")
        names = rec.get("tool_names") or []
        tool = "是" if rec.get("has_tool_calls") else "否"
        if rec.get("role"):
            role = rec["role"]
        elif "role" in rec:
            role = "⚠ 未传 role"      # 调用方漏传了
        else:
            role = "（旧记录，无此字段）"
        lines = [
            f"{head}   角色 {role}",
            f"    模型 {model}   第 {rnd} 轮   耗时 {ms} ms",
            f"    触发工具：{tool}   回复 {rec.get('resp_len', '?')} 字   "
            f"上下文 {rec.get('prompt_len', '?')} 字",
        ]
        if names:
            # 同名调用照原样显示。⚠️ 注意：同名 ≠ 重复！
            # 模型可能对 4 个不同城市各查一次天气（合理扇出）。
            # 2026-09-22 起补了 tool_args，现在【能】判断是不是真的重复调用了。
            args_list = rec.get("tool_args")
            paired = isinstance(args_list, list) and len(args_list) == len(names)
            if paired:
                lines.append(f"    工具明细（{len(names)} 个）：")
                for i, (n, a) in enumerate(zip(names, args_list), 1):
                    lines.append(f"      {i}. {n}({_args_text(a)})")
            else:
                lines.append(f"    工具明细（{len(names)} 个）：{'、'.join(names)}")
                if rec.get("has_tool_calls"):
                    lines.append("      ⚠ 无 tool_args（旧记录或埋点没传）——"
                                 "分不清「同名同参=真重复」和「同名不同参=合理扇出」")
            dup = {n: names.count(n) for n in set(names) if names.count(n) > 1}
            if dup:
                if paired:
                    lines.append("    " + _dup_verdict(names, args_list))
                else:
                    lines.append("    同名调用：" +
                                 "、".join(f"{n} ×{c}" for n, c in sorted(dup.items())) +
                                 "   ⚠ 名字相同不代表参数相同，「同名 ≠ 重复」")
        return "\n".join(lines)

    return head


def main():
    args = sys.argv[1:]
    limit = 10
    filt = None
    for a in args:
        if a.isdigit():
            limit = int(a)
        else:
            filt = a

    p = log_path()
    if not p.exists():
        print(f"日志文件还不存在：{p}")
        print("→ 先去提一个问题（对话或点「多 Agent 规划」），日志就会生成。")
        return

    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue  # 半行/坏行直接跳过，不因为一行坏掉看不到全部
        if filt == "sp" and rec.get("event") != "system_prompt":
            continue
        if filt in ("chat", "agents") and rec.get("link") != filt:
            continue
        rows.append(rec)

    if not rows:
        print(f"没有匹配的记录（筛选条件：{filt}）")
        return

    print(f"日志文件：{p}")
    print(f"共 {len(rows)} 条匹配，显示最后 {min(limit, len(rows))} 条：\n")
    for i, rec in enumerate(rows[-limit:], 1):
        print(f"─── #{i} " + "─" * 46)
        print(fmt(rec))
        print()


if __name__ == "__main__":
    main()
