# -*- coding: utf-8 -*-
"""结构化 LLM 调用日志（JSONL，一行一条）。

为什么单独一个模块：
- 主对话链路（main.py）和多 Agent 链路（agents.py）都要写，
  放中间一层，避免两边互相 import 造成循环依赖。
- 字段名集中在一处定义，事后才能用 grep / jq 按字段捞。

字段规范（改名 = 历史日志全废）：
    公共：                ts, link, event
    event="system_prompt": user_id, query, kb_hits, kb_tags, mem_count, prompt_len
    event="llm_call":      role, model, round, elapsed_ms, prompt_len, resp_len,
                           has_tool_calls, tool_names, tool_args

    注：`tool_names` / `tool_args` **只有会用工具的链路才有**（目前 = main.py 的主对话链路）。
    多 Agent 链路（agents.py，走 llm_complete 纯文本补全）不产生这两个字段 ——
    查日志时别把「这个埋点没这字段」误读成「这轮没调工具」。
    （agent 链路侧写死的 has_tool_calls=False / round=1 也是同一性质：
      字段有，但语义是退化的。）

field 演进记录（日志字段是迭代出来的，不是一次设计完的）：
    2026-09-21 实测后发现 `has_tool_calls`(bool) 不够用 ——
    终端只显示"round 1: tool_calls=7"，但查不出这 7 个是哪几个工具，
    所以补了 `tool_names`(list[str])。
    2026-09-22 再次实测后发现 `tool_names` 还是不够用 ——
    4 次同名 `get_weather` 分不清是「同一城市查 4 遍」（纯浪费，该去重）
    还是「4 个城市各查一次」（合理扇出，去重会把正确结果删掉），
    两种情况的修复方向完全相反，所以补了 `tool_args`：
        [{"name": "get_weather", "args": {"city": "南昌"}}, ...]
    ⚠️ 参数是**解析过的字典**，不是模型吐出来的原始字符串
      （原始 JSON 文本没法按字段比对）；解析失败时记成 {"_raw": "..."} 留证据。
    ⚠️ 每个参数值都会被截断（见 main.py 的 _clip），控制单条体积。

prompt_len 的含义：
    - system_prompt 事件 = 这次拼出来的系统提示字符数
    - llm_call 事件      = 这次真正发给模型的 messages 序列化后的字符数

设计取舍：
- 默认只记 prompt 的「长度」，不记全文：省磁盘，也不把用户隐私落进日志。
  需要还原全文时置环境变量 TP_LOG_FULL_PROMPT=1，代价是日志体积与隐私风险都上升。
- 日志是旁路：写失败只往 stderr 打一行警告，绝不给主流程抛异常。
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# 默认写到 <项目根>/data/llm_calls.jsonl
# 与 main.py 的 ROOT/DATA_DIR、db.py 的 TP_DB_FILE 保持同一套约定
_DEFAULT_LOG = Path(__file__).resolve().parent.parent / "data" / "llm_calls.jsonl"


def log_path():
    """日志文件路径；可用环境变量 TP_LLM_LOG 覆盖（方便测试与负例验证）。"""
    return Path(os.environ.get("TP_LLM_LOG", "").strip() or _DEFAULT_LOG)


def full_prompt_enabled():
    """是否把 prompt 全文也落盘。

    ⚠️ 必须在**函数里**读环境变量，不能在模块顶层读成一个常量。
    原因：main.py 第 41 行 `from llm_log import log_llm_call` 就导入了本模块，
    而第 63 行才 `load_dotenv()` 把 .env 灌进 os.environ ——
    顶层常量会在 .env 生效之前就被求值，写上 `TP_LOG_FULL_PROMPT=1` 也没用。
    """
    return os.environ.get("TP_LOG_FULL_PROMPT", "").strip() == "1"


def log_llm_call(**fields):
    """追加一条 JSONL 记录。

    调用方统一用 prompt=<完整文本> 传原文，本函数负责：
    1. 一定写出 prompt_len（调用方不用自己算长度，避免两处口径不一致）
    2. 按 TP_LOG_FULL_PROMPT 决定要不要把 prompt 原文一起落盘
    """
    try:
        raw_prompt = fields.pop("prompt", None)
        rec = dict(fields)
        if raw_prompt is not None:
            rec.setdefault("prompt_len", len(raw_prompt))
            if full_prompt_enabled():
                rec["prompt"] = raw_prompt
        rec = {"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), **rec}

        p = log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        # 追加写，一行一次 write，不需要把已有内容读进内存
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as ex:
        # 旁路原则：日志坏掉不能让用户请求失败
        print(f"[llm_log] 写日志失败（已忽略，不影响主流程）: {type(ex).__name__}: {ex}",
              file=sys.stderr)

