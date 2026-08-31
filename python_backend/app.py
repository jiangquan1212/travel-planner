# -*- coding: utf-8 -*-
"""
Travel Planner · Python 后端（Flask）
- 与 Node 版接口完全兼容，前端 public/ 无需改动
- 复用项目根目录 data/ 与 .env
- 智能对话：DeepSeek（OpenAI 兼容）SSE 流式 + 向量偏好 RAG

运行：python app.py   （默认 http://127.0.0.1:3000）
"""

import base64
import io
import json
import os
import re
import secrets
import zipfile
from urllib.parse import quote
from datetime import datetime, timezone
from pathlib import Path

import requests
from flask import Flask, Response, jsonify, request, send_from_directory

from auth import (SESSION_COOKIE, build_clear_cookie, build_session_cookie,
                  create_session_token, hash_password, parse_session_token,
                  random_id, sanitize_user, sign, verify_password)
from vector_store import VectorStore
from pdf_extract import extract_pdf_text

# ---------------- 路径与配置 ----------------
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
PUBLIC_DIR = ROOT / "public"
ENV_FILE = ROOT / ".env"


def load_dotenv(path=ENV_FILE):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


load_dotenv()

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.deepseek.com").rstrip("/")
OPENAI_MODEL = os.environ.get("TP_OPENAI_MODEL") or os.environ.get("OPENAI_MODEL") or "deepseek-chat"
PORT = int(os.environ.get("PORT", 3000))
HOST = os.environ.get("HOST", "127.0.0.1")
MAX_HISTORY = 20
MAX_FILE_BYTES = 3 * 1024 * 1024

SYSTEM_PROMPT = """你是"旅行规划师"，一位经验丰富的资深旅游顾问，擅长为不同人群定制多方位旅行计划。

当用户提出旅行规划需求时，请尽量从以下多个方面给出全面、具体的建议（根据用户需求取舍，不必每项都罗列）：
1. 目的地推荐与最佳出行时间（含季节/天气说明）
2. 行程安排：按天展开（Day 1、Day 2…），每段包含上午/下午/晚上做什么
3. 交通方案：大交通（飞机/高铁/自驾对比）与市内交通建议
4. 住宿推荐：区域、价位区间、适合人群与预订提示
5. 美食推荐：必吃当地特色、餐厅类型与预算
6. 预算估算：分项列出（交通/住宿/餐饮/门票/购物/其他）并给出总额区间
7. 必备物品与穿搭建议
8. 安全与注意事项：当地习俗、健康与保险、防坑提示
9. 适合人群与备选方案

要求：
- 默认使用中文回复；条理清晰，可用小标题、加粗和列表
- 如果关键信息不足（如天数、人数、预算、出发地），先简要提出 1-2 个问题补充，或给出合理假设并在开头说明"按 XX 假设"
- 如果用户只是闲聊或咨询其他旅行相关问题，正常友好回答
- 不要编造真实的价格区间之外过于精确的信息，给出区间即可"""

app = Flask(__name__)

# ---------------- 简易 JSON 存储（复用 data/） ----------------
COLLECTIONS = ["users", "sessions", "conversations"]


def load_json(name):
    path = DATA_DIR / f"{name}.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"[store] 无法解析 {path}，已重置: {e}")
        return []


def save_json(name, data):
    path = DATA_DIR / f"{name}.json"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


class Collection:
    def __init__(self, name):
        self.name = name

    def all(self):
        return load_json(self.name)

    def save(self, items):
        save_json(self.name, items)

    def find(self, pred):
        return next((x for x in self.all() if pred(x)), None)

    def get(self, obj_id):
        return self.find(lambda r: r.get("id") == obj_id)

    def insert(self, record):
        items = self.all()
        items.append(record)
        self.save(items)
        return record

    def update(self, obj_id, patch):
        items = self.all()
        for i, r in enumerate(items):
            if r.get("id") == obj_id:
                merged = {**r, **patch, "id": obj_id}
                items[i] = merged
                self.save(items)
                return merged
        return None

    def remove(self, obj_id):
        items = self.all()
        rest = [r for r in items if r.get("id") != obj_id]
        if len(rest) == len(items):
            return False
        self.save(rest)
        return True


users_coll = Collection("users")
sessions_coll = Collection("sessions")
conversations_coll = Collection("conversations")
vector_store = VectorStore(DATA_DIR / "preferences.json")


def now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# ---------------- 会话与权限 ----------------
def get_session_user():
    token = parse_session_token(request.headers.get("Cookie", ""))
    if not token:
        return None
    session = sessions_coll.find(lambda s: s.get("token") == token)
    if not session:
        return None
    expires = session.get("expiresAt")
    if expires and datetime.fromisoformat(expires.replace("Z", "+00:00")).timestamp() < datetime.now(timezone.utc).timestamp():
        sessions_coll.remove(session["id"])
        return None
    return users_coll.get(session.get("userId"))


def require_auth():
    user = get_session_user()
    if not user:
        return None, (jsonify({"error": "未登录或会话已过期"}), 401)
    return user, None


def require_admin():
    user, err = require_auth()
    if err:
        return None, err
    if user.get("role") != "admin":
        return None, (jsonify({"error": "需要管理员权限"}), 403)
    return user, None


def find_user_by_username(username):
    return users_coll.find(lambda u: u.get("username", "").lower() == str(username).lower())


def validate_credentials(body):
    username = str(body.get("username") or "").strip()
    password = str(body.get("password") or "")
    if not re.fullmatch(r"[A-Za-z0-9_]{3,20}", username):
        return None, "用户名需为 3-20 位字母、数字或下划线"
    if len(password) < 6:
        return None, "密码至少 6 位"
    return {"username": username, "password": password}, None


def create_session(user):
    from datetime import timedelta
    token = create_session_token()
    session = {
        "id": random_id(),
        "token": token,
        "userId": user["id"],
        "createdAt": now_iso(),
        "expiresAt": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat().replace("+00:00", "Z"),
    }
    sessions_coll.insert(session)
    return token


# ---------------- 工具 ----------------
def err(status, message):
    return jsonify({"error": message}), status


def read_json_body():
    return request.get_json(silent=True) or {}


def decode_text_buffer(buf):
    for enc in ("utf-8", "gb18030"):
        try:
            return buf.decode(enc)
        except Exception:
            continue
    return buf.decode("latin-1")


def chunk_text(text, max_len=200, max_items=10):
    parts = []
    for line in re.split(r"\r?\n+", text):
        sentences = [s.strip() for s in re.split(r"(?<=[。；;！!？?])", line) if s.strip()]
        parts.extend(sentences if sentences else [line.strip()])
    parts = [p for p in parts if p]
    chunks = []
    for p in parts:
        if len(chunks) >= max_items:
            break
        if len(p) <= max_len:
            chunks.append(p)
            continue
        for i in range(0, len(p), max_len):
            if len(chunks) >= max_items:
                break
            chunks.append(p[i:i + max_len])
    return chunks[:max_items]


def llm_complete(messages):
    if not OPENAI_API_KEY:
        raise RuntimeError("AI 未配置")
    resp = requests.post(
        f"{OPENAI_BASE_URL}/chat/completions",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {OPENAI_API_KEY}"},
        json={"model": OPENAI_MODEL, "stream": False, "messages": messages,
              "temperature": 0.2, "max_tokens": 1000},
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    return (data.get("choices") or [{}])[0].get("message", {}).get("content", "") or ""


def extract_preferences_from_text(text):
    content = llm_complete([
        {"role": "system", "content": "你是旅行偏好提取助手。从用户上传的旅行资料中提取明确的旅行偏好（美食、住宿、预算、目的地、出行方式、游玩风格等）。只输出 JSON 字符串数组，例如 [\"偏好辣味美食\",\"喜欢海边\"]，每项是一条简短独立的旅行偏好（不超过 40 字），提取 1-15 条；没有明确偏好时输出 []。不要输出对象数组或任何其它内容。"},
        {"role": "user", "content": text[:20000]},
    ])
    m = re.search(r"\[[\s\S]*\]", content)
    parsed = json.loads(m.group(0) if m else content)
    if not isinstance(parsed, list):
        return []
    items = []
    for x in parsed:
        if isinstance(x, str):
            items.append(x)
        elif isinstance(x, dict) and x:
            items.append(next(iter(x.values())))
    return [str(s).strip() for s in items if str(s).strip()]


# ---------------- 静态前端 ----------------
@app.route("/")
def index_page():
    return send_from_directory(PUBLIC_DIR, "index.html")


@app.route("/<path:path>")
def static_fallback(path):
    if path.startswith("api/"):
        return err(404, "接口不存在")
    root = PUBLIC_DIR.resolve()
    target = (PUBLIC_DIR / path).resolve()
    if not str(target).startswith(str(root)):
        return err(403, "禁止访问")
    if target.is_dir():
        target = target / "index.html"
    if not target.exists():
        return err(404, "资源不存在")
    # Windows 下 relative_to 返回反斜杠路径，safe_join 会拒绝；统一用正斜杠
    return send_from_directory(PUBLIC_DIR, target.relative_to(root).as_posix())


# ---------------- 认证接口 ----------------
@app.route("/api/auth/register", methods=["POST"])
def api_register():
    body = read_json_body()
    cred, msg = validate_credentials(body)
    if msg:
        return err(400, msg)
    if find_user_by_username(cred["username"]):
        return err(409, "用户名已被占用")
    email = str(body.get("email") or "").strip()
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return err(400, "邮箱格式不正确")
    user = {
        "id": random_id(), "username": cred["username"],
        "email": email or None, "passwordHash": hash_password(cred["password"]),
        "role": "user", "createdAt": now_iso(),
    }
    users_coll.insert(user)
    token = create_session(user)
    resp = jsonify({"user": sanitize_user(user)})
    resp.status_code = 201
    resp.set_cookie(SESSION_COOKIE, f"{token}.{sign(token)}",
                    httponly=True, max_age=7 * 24 * 3600, samesite="Lax")
    return resp


@app.route("/api/auth/login", methods=["POST"])
def api_login():
    body = read_json_body()
    username = str(body.get("username") or "").strip()
    password = str(body.get("password") or "")
    user = find_user_by_username(username)
    if not user or not verify_password(password, user.get("passwordHash")):
        return err(401, "用户名或密码错误")
    token = create_session(user)
    resp = jsonify({"user": sanitize_user(user)})
    resp.set_cookie(SESSION_COOKIE, f"{token}.{sign(token)}",
                    httponly=True, max_age=7 * 24 * 3600, samesite="Lax")
    return resp


@app.route("/api/auth/logout", methods=["POST"])
def api_logout():
    token = parse_session_token(request.headers.get("Cookie", ""))
    if token:
        session = sessions_coll.find(lambda s: s.get("token") == token)
        if session:
            sessions_coll.remove(session["id"])
    resp = jsonify({"ok": True})
    resp.delete_cookie(SESSION_COOKIE)
    return resp


@app.route("/api/auth/me", methods=["GET"])
def api_profile():
    user, err_resp = require_auth()
    if err_resp:
        return err_resp
    return jsonify({"user": sanitize_user(user)})


# ---------------- 个人中心 ----------------
@app.route("/api/me", methods=["GET"])
def api_me():
    user, err_resp = require_auth()
    if err_resp:
        return err_resp
    safe = sanitize_user(user)
    safe.setdefault("nickname", None)
    safe.setdefault("avatar", None)
    prefs_count = len(vector_store.list(user["id"]))
    conv_count = len([c for c in conversations_coll.all() if c.get("userId") == user["id"]])
    return jsonify({"user": safe,
                    "stats": {"preferences": prefs_count, "conversations": conv_count}})


@app.route("/api/me", methods=["PATCH"])
def api_profile_update():
    user, err_resp = require_auth()
    if err_resp:
        return err_resp
    body = read_json_body()
    patch = {}
    if "nickname" in body:
        nickname = str(body.get("nickname") or "").strip()[:20]
        patch["nickname"] = nickname or None
    if "avatar" in body:
        avatar = str(body.get("avatar") or "").strip()[:8]
        patch["avatar"] = avatar or None
    if "email" in body:
        email = str(body.get("email") or "").strip()
        if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            return err(400, "邮箱格式不正确")
        patch["email"] = email or None
    if not patch:
        return err(400, "没有可更新的内容")
    updated = users_coll.update(user["id"], patch)
    return jsonify({"user": sanitize_user(updated)})


@app.route("/api/me/password", methods=["POST"])
def api_profile_password():
    user, err_resp = require_auth()
    if err_resp:
        return err_resp
    body = read_json_body()
    old_pwd = str(body.get("oldPassword") or "")
    new_pwd = str(body.get("newPassword") or "")
    if not verify_password(old_pwd, user.get("passwordHash")):
        return err(400, "原密码不正确")
    if len(new_pwd) < 6:
        return err(400, "新密码至少 6 位")
    users_coll.update(user["id"], {"passwordHash": hash_password(new_pwd)})
    return jsonify({"ok": True})


# ---------------- 配置 / 对话 ----------------
@app.route("/api/config", methods=["GET"])
def api_config():
    user, err_resp = require_auth()
    if err_resp:
        return err_resp
    return jsonify({"chatEnabled": bool(OPENAI_API_KEY),
                    "model": OPENAI_MODEL, "baseUrl": OPENAI_BASE_URL})


def _sse(payload):
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def save_conversation(user, conv_id, raw_messages, assistant_content):
    """保存/追加一次对话；返回会话 id。"""
    base = [m for m in raw_messages
            if m and m.get("role") in ("user", "assistant")
            and isinstance(m.get("content"), str) and m["content"].strip()]
    all_msgs = base + [{"role": "assistant", "content": assistant_content}]
    now = now_iso()
    if conv_id:
        conv = conversations_coll.get(conv_id)
        if conv and conv.get("userId") == user["id"]:
            conversations_coll.update(conv_id, {"messages": all_msgs, "updatedAt": now})
            return conv_id
    first_user = next((m["content"] for m in base if m["role"] == "user"), "未命名对话")
    conv = {
        "id": random_id(), "userId": user["id"],
        "title": first_user[:40], "messages": all_msgs,
        "createdAt": now, "updatedAt": now,
    }
    conversations_coll.insert(conv)
    return conv["id"]


@app.route("/api/chat", methods=["POST"])
def api_chat():
    user, err_resp = require_auth()
    if err_resp:
        return err_resp
    body = read_json_body()
    messages = body.get("messages") or []
    conv_id = body.get("conversationId") or None
    raw_messages = [m for m in messages
                    if m and m.get("role") in ("user", "assistant")
                    and isinstance(m.get("content"), str) and m["content"].strip()]
    history = raw_messages[-MAX_HISTORY:]
    history = [{"role": m["role"], "content": m["content"][:8000]} for m in history]

    def generate():
        if not history:
            yield _sse({"type": "error", "message": "消息不能为空"})
            return
        if not OPENAI_API_KEY:
            yield _sse({"type": "error", "message": "AI 服务未配置：请设置环境变量 OPENAI_API_KEY（或项目根目录 .env）后重启服务，可配合 OPENAI_BASE_URL / TP_OPENAI_MODEL"})
            return

        system_prompt = SYSTEM_PROMPT
        last_user = next((m["content"] for m in reversed(history) if m["role"] == "user"), None)
        if last_user:
            related = vector_store.search(user["id"], last_user, 3)
            if related:
                lines = "\n".join(
                    f"- [{p.get('category', '其他')}·{'★' * p.get('weight', 3)}] {p['text']}"
                    for p in related)
                system_prompt += (f"\n\n【用户已保存的旅行偏好，按与当前问题的相关度从高到低排列】\n"
                                  f"{lines}\n请在回答时优先参考这些偏好。")

        try:
            resp = requests.post(
                f"{OPENAI_BASE_URL}/chat/completions",
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {OPENAI_API_KEY}"},
                json={"model": OPENAI_MODEL, "stream": True,
                      "messages": [{"role": "system", "content": system_prompt}] + history,
                      "temperature": 0.7},
                stream=True, timeout=120,
            )
        except Exception as e:
            yield _sse({"type": "error", "message": f"无法连接 AI 服务：{e}"})
            return

        if resp.status_code != 200:
            detail = ""
            try:
                detail = resp.json().get("error", {}).get("message", "")
            except Exception:
                detail = resp.text[:200]
            yield _sse({"type": "error",
                        "message": f"AI 服务返回错误（HTTP {resp.status_code}）：{detail}"})
            return

        assistant_parts = []
        for raw in resp.iter_lines(decode_unicode=False):
            if not raw:
                continue
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                obj = json.loads(data)
                delta = (obj.get("choices") or [{}])[0].get("delta", {})
                content = delta.get("content")
                if content:
                    assistant_parts.append(content)
                    yield _sse({"type": "delta", "content": content})
            except Exception:
                continue

        assistant_content = "".join(assistant_parts)
        if assistant_content.strip():
            saved_id = save_conversation(user, conv_id, raw_messages, assistant_content)
            yield _sse({"type": "done", "conversationId": saved_id})
        else:
            yield _sse({"type": "error", "message": "AI 没有返回内容，请重试"})

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


# ---------------- 对话历史 ----------------
@app.route("/api/conversations", methods=["GET"])
def api_conversations_list():
    user, err_resp = require_auth()
    if err_resp:
        return err_resp
    convs = [c for c in conversations_coll.all() if c.get("userId") == user["id"]]
    convs.sort(key=lambda c: c.get("updatedAt", ""), reverse=True)
    items = [{"id": c["id"], "title": c.get("title", "未命名对话"),
              "createdAt": c.get("createdAt"), "updatedAt": c.get("updatedAt"),
              "messageCount": len(c.get("messages", []))} for c in convs]
    return jsonify({"conversations": items})


@app.route("/api/conversations/<cid>", methods=["GET"])
def api_conversation_get(cid):
    user, err_resp = require_auth()
    if err_resp:
        return err_resp
    conv = conversations_coll.get(cid)
    if not conv or conv.get("userId") != user["id"]:
        return err(404, "对话不存在")
    return jsonify({"conversation": conv})


@app.route("/api/conversations/<cid>", methods=["PUT"])
def api_conversation_update(cid):
    user, err_resp = require_auth()
    if err_resp:
        return err_resp
    conv = conversations_coll.get(cid)
    if not conv or conv.get("userId") != user["id"]:
        return err(404, "对话不存在")
    body = read_json_body()
    patch = {"updatedAt": now_iso()}
    if "title" in body and str(body.get("title") or "").strip():
        patch["title"] = str(body["title"]).strip()[:40]
    if "messages" in body and isinstance(body.get("messages"), list):
        patch["messages"] = body["messages"]
    updated = conversations_coll.update(cid, patch)
    return jsonify({"conversation": updated})


@app.route("/api/conversations/<cid>", methods=["DELETE"])
def api_conversation_delete(cid):
    user, err_resp = require_auth()
    if err_resp:
        return err_resp
    conv = conversations_coll.get(cid)
    if not conv or conv.get("userId") != user["id"]:
        return err(404, "对话不存在")
    conversations_coll.remove(cid)
    return jsonify({"ok": True})


# ---------------- 偏好（向量库） ----------------
@app.route("/api/preferences", methods=["GET"])
def api_prefs_list():
    user, err_resp = require_auth()
    if err_resp:
        return err_resp
    return jsonify({"preferences": vector_store.list(user["id"])})


@app.route("/api/preferences", methods=["POST"])
def api_prefs_add():
    user, err_resp = require_auth()
    if err_resp:
        return err_resp
    body = read_json_body()
    result = vector_store.add(user["id"], body.get("text"),
                              body.get("category"), body.get("weight"))
    if "error" in result:
        return err(400, result["error"])
    return jsonify({"preference": result["doc"]}), 201


@app.route("/api/preferences/<pref_id>", methods=["PATCH"])
def api_prefs_update(pref_id):
    user, err_resp = require_auth()
    if err_resp:
        return err_resp
    body = read_json_body()
    patch = {}
    if "category" in body:
        patch["category"] = body.get("category")
    if "weight" in body:
        patch["weight"] = body.get("weight")
    if "text" in body:
        patch["text"] = body.get("text")
    result = vector_store.update(user["id"], pref_id, patch)
    if result is None:
        return err(404, "偏好不存在")
    if "error" in result:
        return err(400, result["error"])
    return jsonify({"preference": result})


@app.route("/api/preferences/<pref_id>", methods=["DELETE"])
def api_prefs_delete(pref_id):
    user, err_resp = require_auth()
    if err_resp:
        return err_resp
    if not vector_store.remove(user["id"], pref_id):
        return err(404, "偏好不存在")
    return jsonify({"ok": True})


@app.route("/api/preferences/import", methods=["POST"])
def api_prefs_import():
    user, err_resp = require_auth()
    if err_resp:
        return err_resp
    body = read_json_body()
    filename = str(body.get("filename") or "").strip()
    b64 = str(body.get("contentBase64") or "")
    if not filename or not b64:
        return err(400, "缺少文件名或文件内容")
    try:
        buf = base64.b64decode(b64)
    except Exception:
        return err(400, "文件内容编码无效")
    if not buf:
        return err(400, "文件为空")
    if len(buf) > MAX_FILE_BYTES:
        return err(400, "文件不能超过 3MB")

    ext = Path(filename).suffix.lower()
    if ext in (".txt", ".text", ".md"):
        text = decode_text_buffer(buf)
    elif ext == ".pdf":
        text = extract_pdf_text(buf)
    else:
        return err(400, "仅支持 PDF 或 TXT 文件")

    text = text.replace("\r\n", "\n").strip()
    if not text:
        return err(400, "未能从文件中读取到文本（扫描件/图片型 PDF 可能无法提取）")
    if len(text) > 50000:
        text = text[:50000]

    method = "ai"
    items = None
    try:
        items = extract_preferences_from_text(text)
    except Exception as e:
        print(f"[import] AI 提取失败: {e}")
        items = None
    if not items:
        method = "chunk"
        items = chunk_text(text)

    added, skipped = [], 0
    for item in items:
        s = str(item or "").strip()[:200]
        if not s:
            continue
        r = vector_store.add(user["id"], s)
        if "doc" in r:
            added.append(r["doc"])
        else:
            skipped += 1

    return jsonify({"added": added, "method": method, "skipped": skipped,
                    "total": len(added), "filename": filename,
                    "textPreview": text[:120]}), 201


# ---------------- 导出旅行计划 ----------------
def _xml_escape(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;"))


def build_docx(title, content):
    """生成极简 .docx（纯 zip+XML，支持中文）。"""
    lines = content.splitlines()
    paragraphs = []
    for line in lines:
        text = line.rstrip()
        if not text.strip():
            paragraphs.append('<w:p/>')
            continue
        m = re.match(r'^(#{1,6})\s+(.*)$', text)
        if m:
            level = len(m.group(1))
            size = max(2, 6 - level)
            paragraphs.append(
                '<w:p><w:pPr><w:pBdr><w:bottom w:val="single" w:sz="6" w:space="1" w:color="CCCCCC"/></w:pBdr></w:pPr>'
                f'<w:r><w:rPr><w:b/><w:sz w:val="{size * 2}"/></w:rPr><w:t xml:space="preserve">{_xml_escape(m.group(2))}</w:t></w:r></w:p>')
            continue
        paragraphs.append(
            f'<w:p><w:r><w:t xml:space="preserve">{_xml_escape(text)}</w:t></w:r></w:p>')
    body = "".join(paragraphs)
    title_para = (f'<w:p><w:r><w:rPr><w:b/><w:sz w:val="36"/></w:rPr>'
                  f'<w:t xml:space="preserve">{_xml_escape(title)}</w:t></w:r></w:p>')
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f'<w:body>{title_para}{body}'
        '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/></w:sectPr></w:body></w:document>')
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '</Types>')
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        '</Relationships>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", document)
    return buf.getvalue()


PDF_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\simhei.ttf", r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyh.ttf", r"C:\Windows\Fonts\simsun.ttc",
    r"C:\Windows\Fonts\simfang.ttf",
]


def build_pdf(title, content):
    """用 fpdf2 生成 PDF（需要中文字体）；字体缺失返回 None。"""
    try:
        from fpdf import FPDF
    except Exception:
        return None
    font_path = next((p for p in PDF_FONT_CANDIDATES if os.path.exists(p)), None)
    if not font_path:
        return None
    try:
        pdf = FPDF()
        pdf.add_font("cjk", "", font_path)
        pdf.set_auto_page_break(auto=True, margin=18)
        width = pdf.w - pdf.l_margin - pdf.r_margin
        pdf.add_page()
        pdf.set_font("cjk", size=18)
        pdf.multi_cell(width, 10, title)
        pdf.ln(4)
        pdf.set_font("cjk", size=11)
        for line in content.splitlines():
            text = line.rstrip()
            if not text.strip():
                pdf.ln(4)
                continue
            pdf.set_x(pdf.l_margin)
            m = re.match(r'^(#{1,6})\s+(.*)$', text)
            if m:
                pdf.set_font("cjk", size=14)
                pdf.multi_cell(width, 9, m.group(2))
                pdf.set_font("cjk", size=11)
            else:
                pdf.multi_cell(width, 7, text)
        return bytes(pdf.output())
    except Exception as e:
        print(f"[pdf] build_pdf failed: {type(e).__name__}: {e}")
        return None


@app.route("/api/export", methods=["POST"])
def api_export():
    user, err_resp = require_auth()
    if err_resp:
        return err_resp
    body = read_json_body()
    fmt = str(body.get("format") or "md").lower()
    if fmt == "markdown":
        fmt = "md"
    title = str(body.get("title") or "旅行计划").strip() or "旅行计划"
    content = str(body.get("content") or "").strip()
    if not content:
        return err(400, "没有可导出的内容")
    if fmt not in ("md", "txt", "docx", "pdf"):
        return err(400, "不支持的导出格式")

    base = re.sub(r'[\\/:*?"<>|]', "_", title)[:40] or "旅行计划"
    if fmt == "md":
        data = f"# {title}\n\n{content}\n"
        mime = "text/markdown; charset=utf-8"
        fname = f"{base}.md"
    elif fmt == "txt":
        data = f"{title}\n{'=' * len(title)}\n\n{content}\n"
        mime = "text/plain; charset=utf-8"
        fname = f"{base}.txt"
    elif fmt == "docx":
        data = build_docx(title, content)
        mime = ("application/vnd.openxmlformats-officedocument."
                "wordprocessingml.document")
        fname = f"{base}.docx"
    else:
        data = build_pdf(title, content)
        if data is None:
            return err(400, "无法生成 PDF：缺少中文字体或 fpdf2（可导出 Word/文本）")
        mime = "application/pdf"
        fname = f"{base}.pdf"

    if isinstance(data, str):
        data = data.encode("utf-8")
    resp = Response(data, mimetype=mime)
    resp.headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(fname)}"
    return resp


# ---------------- 实时天气（Open-Meteo，免 key） ----------------
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


@app.route("/api/weather", methods=["GET"])
def api_weather():
    user, err_resp = require_auth()
    if err_resp:
        return err_resp
    city = (request.args.get("city") or "").strip()
    if not city:
        return err(400, "请提供城市名，如 /api/weather?city=杭州")
    try:
        geo = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": city, "count": 1, "language": "zh", "format": "json"},
            timeout=15,
        )
        geo.raise_for_status()
        geo_data = geo.json().get("results") or []
        if not geo_data:
            return err(404, f"未找到城市：{city}")
        loc = geo_data[0]
        lat, lon = loc["latitude"], loc["longitude"]
        name = loc.get("name") or city
        country = (loc.get("country") or loc.get("country_code") or "")[:30]

        wx = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat, "longitude": lon,
                "current_weather": "true",
                "daily": "weathercode,temperature_2m_max,temperature_2m_min",
                "timezone": "auto", "forecast_days": 5,
            },
            timeout=15,
        )
        wx.raise_for_status()
        w = wx.json()
        cur = w.get("current_weather", {})
        desc, icon = _wmo(cur.get("weathercode", -1))
        daily = []
        for i, date in enumerate(w.get("daily", {}).get("time", [])):
            code = w["daily"]["weathercode"][i]
            d, ic = _wmo(code)
            daily.append({
                "date": date,
                "desc": d, "icon": ic,
                "tmax": w["daily"]["temperature_2m_max"][i],
                "tmin": w["daily"]["temperature_2m_min"][i],
            })
        return jsonify({
            "city": name, "country": country,
            "current": {
                "temp": cur.get("temperature"),
                "wind": cur.get("windspeed"),
                "desc": desc, "icon": icon,
                "time": cur.get("time"),
            },
            "daily": daily[:5],
            "source": "Open-Meteo",
        })
    except requests.exceptions.RequestException as e:
        return err(503, f"天气服务暂不可用：{e}")


# ---------------- 管理员 ----------------
@app.route("/api/admin/register", methods=["POST"])
def api_admin_register():
    admin, err_resp = require_admin()
    if err_resp:
        return err_resp
    body = read_json_body()
    cred, msg = validate_credentials(body)
    if msg:
        return err(400, msg)
    if find_user_by_username(cred["username"]):
        return err(409, "用户名已被占用")
    user = {
        "id": random_id(), "username": cred["username"],
        "email": str(body.get("email") or "").strip() or None,
        "passwordHash": hash_password(cred["password"]),
        "role": "admin", "createdAt": now_iso(),
    }
    users_coll.insert(user)
    return jsonify({"user": sanitize_user(user)}), 201


@app.route("/api/admin/users", methods=["GET"])
def api_admin_users():
    admin, err_resp = require_admin()
    if err_resp:
        return err_resp
    return jsonify({"users": [sanitize_user(u) for u in users_coll.all()]})


@app.route("/api/admin/users/<user_id>/role", methods=["PATCH"])
def api_admin_role(user_id):
    admin, err_resp = require_admin()
    if err_resp:
        return err_resp
    target = users_coll.get(user_id)
    if not target:
        return err(404, "用户不存在")
    body = read_json_body()
    role = body.get("role")
    if role not in ("user", "admin"):
        return err(400, "角色只能是 user 或 admin")
    if user_id == admin["id"]:
        return err(400, "不能修改自己的角色")
    updated = users_coll.update(user_id, {"role": role})
    return jsonify({"user": sanitize_user(updated)})


@app.route("/api/admin/users/<user_id>", methods=["DELETE"])
def api_admin_delete(user_id):
    admin, err_resp = require_admin()
    if err_resp:
        return err_resp
    target = users_coll.get(user_id)
    if not target:
        return err(404, "用户不存在")
    if user_id == admin["id"]:
        return err(400, "不能删除自己")
    for s in sessions_coll.all():
        if s.get("userId") == user_id:
            sessions_coll.remove(s["id"])
    users_coll.remove(user_id)
    return jsonify({"ok": True})


# ---------------- 启动 ----------------
if __name__ == "__main__":
    print(f"[travel-planner-python] 后端 API 已启动: http://{HOST}:{PORT}")
    print(f"[travel-planner-python] 前端页面:      http://{HOST}:{PORT}/")
    print(f"[travel-planner-python] AI 对话: {'已启用（' + OPENAI_MODEL + ' @ ' + OPENAI_BASE_URL + '）' if OPENAI_API_KEY else '未配置（请设置 OPENAI_API_KEY）'}")
    app.run(host=HOST, port=PORT, threaded=True)






