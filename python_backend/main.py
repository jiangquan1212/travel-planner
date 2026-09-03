# -*- coding: utf-8 -*-
"""
Travel Planner · FastAPI 后端（课程升级版）
- 保留原有全部接口，前端无需改动
- Pydantic 参数校验 / SSE 流式对话 / Function Calling 多工具并行
- RAG 攻略知识库 + 偏好向量库 / 多 Agent 协作 / Leaflet 地图地理编码
- Redis 缓存（REDIS_URL 可选，未配置时回退内存）

运行：python python_backend/main.py
"""

import base64
import io
import json
import os
import re
import secrets
import string
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import requests
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

from auth import (SESSION_COOKIE, create_session_token, hash_password,
                  parse_session_token, random_id, sanitize_user, sign,
                  verify_password)
from vector_store import VectorStore
from guide_store import GuideStore
from pdf_extract import extract_pdf_text
from tools import TOOL_DEFS, _wmo, execute_tool, summarize_tool
from agents import llm_complete, run_multi_agent
from rerank import rrf_rerank
import db
from cache import cache_get, cache_set

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
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_dotenv()

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.deepseek.com").rstrip("/")
OPENAI_MODEL = os.environ.get("TP_OPENAI_MODEL") or os.environ.get("OPENAI_MODEL") or "deepseek-chat"
PORT = int(os.environ.get("PORT", 3000))
HOST = os.environ.get("HOST", "127.0.0.1")
MAX_HISTORY = 20
MAX_FILE_BYTES = 3 * 1024 * 1024
FEEDBACK_CATEGORIES = ("建议", "问题反馈", "功能需求", "其他")

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

app = FastAPI(title="Travel Planner API", version="2.0.0",
              description="基于 RAG 检索增强生成的 AI 旅行助手（FastAPI 版）")


# ---------------- Pydantic 模型（参数验证） ----------------
class Msg(BaseModel):
    role: str
    content: str


class LoginIn(BaseModel):
    username: str
    password: str


class RegisterIn(BaseModel):
    username: str
    password: str
    email: str | None = None


class ChatIn(BaseModel):
    messages: list[Msg]
    conversationId: str | None = None


class PrefIn(BaseModel):
    text: str
    category: str | None = None
    weight: int | None = None


class PrefPatch(BaseModel):
    text: str | None = None
    category: str | None = None
    weight: int | None = None


class ImportIn(BaseModel):
    filename: str
    contentBase64: str


class GuideIn(BaseModel):
    filename: str
    contentBase64: str


class ExportIn(BaseModel):
    format: str
    title: str | None = None
    content: str


class ProfileIn(BaseModel):
    nickname: str | None = None
    avatar: str | None = None
    email: str | None = None


class PasswordIn(BaseModel):
    oldPassword: str
    newPassword: str


class AdminIn(BaseModel):
    username: str
    password: str
    email: str | None = None


class RoleIn(BaseModel):
    role: str


class ResetPasswordIn(BaseModel):
    newPassword: str | None = None


class StatusIn(BaseModel):
    status: str
    banDays: float | None = None


class FavoriteIn(BaseModel):
    title: str
    content: str


class ResetRequestIn(BaseModel):
    reason: str | None = None


class FeedbackIn(BaseModel):
    content: str
    category: str | None = None


class FeedbackHandleIn(BaseModel):
    reply: str | None = None


class ConversationUpdateIn(BaseModel):
    title: str | None = None
    messages: list[dict] | None = None


class AgentsIn(BaseModel):
    request: str
    city: str | None = None


# ---------------- JSON 存储 ----------------
def load_json(name):
    path = DATA_DIR / f"{name}.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_json(name, data):
    path = DATA_DIR / f"{name}.json"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


class Collection:
    """集合存储：启用 SQLite（TP_DB_FILE）时走 db.py，否则 JSON 文件。"""

    def __init__(self, name):
        self.name = name

    def all(self):
        if db.db_enabled():
            return db.all(self.name)
        return load_json(self.name)

    def save(self, items):
        if db.db_enabled():
            db.replace(self.name, items)
        else:
            save_json(self.name, items)

    def find(self, pred):
        return next((x for x in self.all() if pred(x)), None)

    def get(self, obj_id):
        if db.db_enabled():
            return db.get(self.name, obj_id)
        return self.find(lambda r: r.get("id") == obj_id)

    def insert(self, record):
        if db.db_enabled():
            db.insert(self.name, record)
        else:
            items = self.all()
            items.append(record)
            self.save(items)
        return record

    def update(self, obj_id, patch):
        if db.db_enabled():
            return db.update(self.name, obj_id, patch)
        items = self.all()
        for i, r in enumerate(items):
            if r.get("id") == obj_id:
                merged = {**r, **patch, "id": obj_id}
                items[i] = merged
                self.save(items)
                return merged
        return None

    def remove(self, obj_id):
        if db.db_enabled():
            return db.remove(self.name, obj_id)
        items = self.all()
        rest = [r for r in items if r.get("id") != obj_id]
        if len(rest) == len(items):
            return False
        self.save(rest)
        return True


# 若配置了 SQLite，初始化表结构（需在 load_dotenv 之后执行）
db.init_schema()


users_coll = Collection("users")
sessions_coll = Collection("sessions")
conversations_coll = Collection("conversations")
audit_coll = Collection("audit")
favorites_coll = Collection("favorites")
reset_requests_coll = Collection("reset_requests")
feedback_coll = Collection("feedback")
memory_coll = Collection("memory")
vector_store = VectorStore(DATA_DIR / "preferences.json")
guide_store = GuideStore(DATA_DIR / "guides.json")


def now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def err(status, message):
    return JSONResponse({"error": message}, status_code=status)


# ---------------- 会话与权限 ----------------
def get_session_user(request):
    token = request.headers.get("X-Session-Token", "").strip()
    if not token:
        token = parse_session_token(f"{SESSION_COOKIE}={request.cookies.get(SESSION_COOKIE, '')}")
    if not token:
        return None
    session = sessions_coll.find(lambda s: s.get("token") == token)
    if not session:
        return None
    expires = session.get("expiresAt")
    if expires:
        try:
            t = datetime.fromisoformat(expires.replace("Z", "+00:00")).timestamp()
            if t < datetime.now(timezone.utc).timestamp():
                sessions_coll.remove(session["id"])
                return None
        except Exception:
            pass
    user = users_coll.get(session.get("userId"))
    if not user:
        return None
    if user.get("status") == "banned":
        if _ban_expired(user):
            users_coll.update(user["id"], {"status": "active", "banUntil": None})
        else:
            sessions_coll.remove(session["id"])
            return None
    return user


def require_auth(request):
    user = get_session_user(request)
    if not user:
        return None, err(401, "未登录或会话已过期")
    return user, None


def require_admin(request):
    user, e = require_auth(request)
    if e:
        return None, e
    if user.get("role") != "admin":
        return None, err(403, "需要管理员权限")
    return user, None


def find_user_by_username(username):
    return users_coll.find(lambda u: u.get("username", "").lower() == str(username).lower())


def validate_credentials(username, password):
    if not re.fullmatch(r"[A-Za-z0-9_]{3,20}", str(username)):
        return "用户名需为 3-20 位字母、数字或下划线"
    if len(str(password)) < 6:
        return "密码至少 6 位"
    return None


def create_session(user):
    token = create_session_token()
    session = {
        "id": random_id(), "token": token, "userId": user["id"],
        "createdAt": now_iso(),
        "expiresAt": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat().replace("+00:00", "Z"),
    }
    sessions_coll.insert(session)
    return token


def _set_cookie(response, token):
    response.set_cookie(SESSION_COOKIE, f"{token}.{sign(token)}",
                        httponly=True, max_age=7 * 24 * 3600, samesite="lax")


# ---------------- 工具 ----------------
def decode_text_buffer(buf):
    for enc in ("utf-8", "gb18030"):
        try:
            return buf.decode(enc)
        except Exception:
            continue
    return buf.decode("latin-1")


def search_knowledge(user_id, query, top_k=6):
    """统一检索：偏好 + 攻略合并，多召回后 RRF 混合重排取 Top-K。"""
    cands = []
    for p in vector_store.search(user_id, query, 5):
        cands.append({"kind": "pref", "text": p["text"], "score": p["score"],
                      "weight": p.get("weight", 3),
                      "tag": f"偏好[{p.get('category', '其他')}·{'★' * p.get('weight', 3)}]"})
    for g in guide_store.search(user_id, query, 10):  # 多召回，交给重排
        cands.append({"kind": "guide", "text": g["text"], "score": g["score"],
                      "tag": f"攻略[{g['filename']}]"})
    if not cands:
        return []
    reranked = rrf_rerank(query, cands)
    by_text = {c["text"]: c for c in cands}
    out = []
    for item in reranked:
        c = by_text[item["text"]]
        out.append({**c, "rrf": item["rrf"]})
    out.sort(key=lambda x: (x["rrf"], x.get("weight", 3)), reverse=True)
    return out[:top_k]


def memory_texts(user_id, limit=10):
    ms = [m for m in memory_coll.all() if m.get("userId") == user_id]
    ms.sort(key=lambda m: m.get("createdAt", ""), reverse=True)
    return [m["text"] for m in ms[:limit]]


def extract_memory(user, raw_messages):
    """从用户消息中提取长期记忆（短句），去重后存入；最多保留 30 条。"""
    texts = [m["content"] for m in raw_messages
             if m.get("role") == "user" and isinstance(m.get("content"), str)]
    joined = "\n".join(t.strip() for t in texts if t.strip())
    if len(joined) < 8:
        return
    try:
        out = llm_complete([
            {"role": "system", "content": "你是记忆提取助手。从用户消息中提取值得长期记住的旅行信息（目的地/预算/天数/人数/出发地/交通偏好/饮食偏好/游玩偏好等），只输出 JSON 字符串数组，每项不超过 40 字，最多 3 项；没有可记的则输出 []。不要输出其它内容。"},
            {"role": "user", "content": joined[:3000]},
        ], temperature=0.2, max_tokens=300)
        m = re.search(r"\[[\s\S]*\]", out)
        parsed = json.loads(m.group(0) if m else out)
        items = []
        for x in parsed if isinstance(parsed, list) else []:
            if isinstance(x, str) and x.strip():
                items.append(x.strip())
            elif isinstance(x, dict):
                v = x.get("value") or x.get("text") or x.get("content") or ""
                items.append(str(v).strip())
        items = [t[:80] for t in items if t]
    except Exception as e:
        print(f"[memory] 提取失败: {e}")
        return
    existing = {m["text"] for m in memory_coll.all() if m.get("userId") == user["id"]}
    for it in items[:3]:
        if it in existing:
            continue
        memory_coll.insert({"id": random_id(), "userId": user["id"], "text": it,
                            "source": "chat", "createdAt": now_iso()})
        existing.add(it)
    # 修剪：只留最近 30 条
    all_m = [m for m in memory_coll.all() if m.get("userId") == user["id"]]
    all_m.sort(key=lambda m: m.get("createdAt", ""), reverse=True)
    for m in all_m[30:]:
        memory_coll.remove(m["id"])


def build_system_prompt(user, history):
    """偏好 + 攻略 统一知识库 RAG → 系统提示。"""
    sp = SYSTEM_PROMPT
    last_user = next((m["content"] for m in reversed(history) if m["role"] == "user"), None)
    if last_user:
        kbs = search_knowledge(user["id"], last_user, 6)
        if kbs:
            lines = "\n".join(f"- {item['tag']} {item['text']}" for item in kbs)
            sp += (f"\n\n【个人知识库（偏好 + 攻略，按相关度从高到低）】\n{lines}\n"
                   f"请在回答时优先参考这些信息；引用攻略片段时说明来源。")
    mem = memory_texts(user["id"], 10)
    if mem:
        sp += "\n\n【用户长期记忆（历次对话沉淀）】\n" + "\n".join(f"- {t}" for t in mem)
    return sp


def _ban_expired(user):
    """判断定时封禁是否已到期。"""
    until = user.get("banUntil")
    if not until:
        return False
    try:
        t = datetime.fromisoformat(until.replace("Z", "+00:00"))
        return datetime.now(timezone.utc) >= t
    except Exception:
        return False


def add_audit(actor, action, target, detail=""):
    """写入审计日志。"""
    audit_coll.insert({
        "id": random_id(),
        "actorName": actor.get("username", "?"),
        "actorRole": actor.get("role", "?"),
        "action": action,
        "target": target,
        "detail": detail,
        "createdAt": now_iso(),
    })


def save_conversation(user, conv_id, raw_messages, assistant_content):
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
    conv = {"id": random_id(), "userId": user["id"], "title": first_user[:40],
            "messages": all_msgs, "createdAt": now, "updatedAt": now}
    conversations_coll.insert(conv)
    return conv["id"]


def sse(payload):
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _dump(model):
    """兼容 pydantic v1(.dict) / v2(.model_dump)。"""
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


# ---------------- 健康检查（供 Docker/K8s 探活） ----------------
@app.get("/api/health")
def api_health():
    return JSONResponse({"status": "ok", "service": "travel-planner"})


# ---------------- 认证 ----------------
@app.post("/api/auth/register")
def api_register(body: RegisterIn):
    msg = validate_credentials(body.username, body.password)
    if msg:
        return err(400, msg)
    if find_user_by_username(body.username):
        return err(409, "用户名已被占用")
    email = (body.email or "").strip()
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return err(400, "邮箱格式不正确")
    user = {"id": random_id(), "username": body.username, "email": email or None,
            "passwordHash": hash_password(body.password), "role": "user",
            "status": "active", "createdAt": now_iso()}
    users_coll.insert(user)
    token = create_session(user)
    resp = JSONResponse({"user": sanitize_user(user), "token": token}, status_code=201)
    _set_cookie(resp, token)
    return resp


@app.post("/api/auth/login")
def api_login(body: LoginIn):
    user = find_user_by_username(body.username)
    if not user or not verify_password(body.password, user.get("passwordHash")):
        return err(401, "用户名或密码错误")
    if user.get("status") == "banned":
        if _ban_expired(user):
            users_coll.update(user["id"], {"status": "active", "banUntil": None})
        else:
            msg = "账号已被封禁，请联系管理员"
            if user.get("banUntil"):
                msg += f"，将于 {user['banUntil'][:16].replace('T', ' ')} 解封"
            return err(403, msg)
    token = create_session(user)
    resp = JSONResponse({"user": sanitize_user(user), "token": token})
    _set_cookie(resp, token)
    return resp


@app.post("/api/auth/logout")
def api_logout(request: Request):
    token = parse_session_token(f"{SESSION_COOKIE}={request.cookies.get(SESSION_COOKIE, '')}")
    if token:
        session = sessions_coll.find(lambda s: s.get("token") == token)
        if session:
            sessions_coll.remove(session["id"])
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(SESSION_COOKIE)
    return resp


@app.get("/api/auth/me")
def api_me_auth(request: Request):
    user, e = require_auth(request)
    if e:
        return e
    token = request.headers.get("X-Session-Token", "").strip()
    if not token:
        token = parse_session_token(f"{SESSION_COOKIE}={request.cookies.get(SESSION_COOKIE, '')}") or ""
    return JSONResponse({"user": sanitize_user(user), "token": token})


@app.get("/api/config")
def api_config(request: Request):
    user, e = require_auth(request)
    if e:
        return e
    return JSONResponse({"chatEnabled": bool(OPENAI_API_KEY),
                         "model": OPENAI_MODEL, "baseUrl": OPENAI_BASE_URL})


# ---------------- 个人中心 ----------------
@app.get("/api/me")
def api_me(request: Request):
    user, e = require_auth(request)
    if e:
        return e
    safe = sanitize_user(user)
    safe.setdefault("nickname", None)
    safe.setdefault("avatar", None)
    safe.setdefault("status", "active")
    prefs_count = len(vector_store.list(user["id"]))
    conv_count = len([c for c in conversations_coll.all() if c.get("userId") == user["id"]])
    return JSONResponse({"user": safe, "stats": {"preferences": prefs_count, "conversations": conv_count}})


@app.patch("/api/me")
def api_me_update(body: ProfileIn, request: Request):
    user, e = require_auth(request)
    if e:
        return e
    patch = {}
    if body.nickname is not None:
        patch["nickname"] = body.nickname.strip()[:20] or None
    if body.avatar is not None:
        patch["avatar"] = body.avatar.strip()[:8] or None
    if body.email is not None:
        email = body.email.strip()
        if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            return err(400, "邮箱格式不正确")
        patch["email"] = email or None
    if not patch:
        return err(400, "没有可更新的内容")
    updated = users_coll.update(user["id"], patch)
    return JSONResponse({"user": sanitize_user(updated)})


@app.post("/api/me/password")
def api_me_password(body: PasswordIn, request: Request):
    user, e = require_auth(request)
    if e:
        return e
    if not verify_password(body.oldPassword, user.get("passwordHash")):
        return err(400, "原密码不正确")
    if len(body.newPassword) < 6:
        return err(400, "新密码至少 6 位")
    users_coll.update(user["id"], {"passwordHash": hash_password(body.newPassword)})
    add_audit(user, "修改密码", user.get("username", "?"), "用户自行修改")
    return JSONResponse({"ok": True})


# ---------------- 偏好（向量库） ----------------
@app.get("/api/preferences")
def api_prefs_list(request: Request):
    user, e = require_auth(request)
    if e:
        return e
    return JSONResponse({"preferences": vector_store.list(user["id"])})


@app.post("/api/preferences")
def api_prefs_add(body: PrefIn, request: Request):
    user, e = require_auth(request)
    if e:
        return e
    result = vector_store.add(user["id"], body.text, body.category, body.weight)
    if "error" in result:
        return err(400, result["error"])
    return JSONResponse({"preference": result["doc"]}, status_code=201)


@app.patch("/api/preferences/{pref_id}")
def api_prefs_update(pref_id: str, body: PrefPatch, request: Request):
    user, e = require_auth(request)
    if e:
        return e
    patch = {}
    if body.category is not None:
        patch["category"] = body.category
    if body.weight is not None:
        patch["weight"] = body.weight
    if body.text is not None:
        patch["text"] = body.text
    result = vector_store.update(user["id"], pref_id, patch)
    if result is None:
        return err(404, "偏好不存在")
    if "error" in result:
        return err(400, result["error"])
    return JSONResponse({"preference": result})


@app.delete("/api/preferences/{pref_id}")
def api_prefs_delete(pref_id: str, request: Request):
    user, e = require_auth(request)
    if e:
        return e
    if not vector_store.remove(user["id"], pref_id):
        return err(404, "偏好不存在")
    return JSONResponse({"ok": True})


@app.post("/api/preferences/import")
def api_prefs_import(body: ImportIn, request: Request):
    user, e = require_auth(request)
    if e:
        return e
    try:
        buf = base64.b64decode(body.contentBase64)
    except Exception:
        return err(400, "文件内容编码无效")
    if not buf:
        return err(400, "文件为空")
    if len(buf) > MAX_FILE_BYTES:
        return err(400, "文件不能超过 3MB")
    ext = Path(body.filename).suffix.lower()
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
    items = None
    try:
        content = llm_complete([
            {"role": "system", "content": "你是旅行偏好提取助手。从资料中提取明确的旅行偏好。只输出 JSON 字符串数组，每项不超过 40 字；没有则输出 []。不要输出其它内容。"},
            {"role": "user", "content": text[:20000]},
        ], temperature=0.2, max_tokens=800)
        m = re.search(r"\[[\s\S]*\]", content)
        parsed = json.loads(m.group(0) if m else content)
        items = []
        if isinstance(parsed, list):
            for x in parsed:
                if isinstance(x, str):
                    items.append(x)
                elif isinstance(x, dict) and x:
                    items.append(next(iter(x.values())))
    except Exception as ex:
        print(f"[import] AI 偏好提取失败: {type(ex).__name__}: {ex}")
        items = None
    method = "ai"
    if not items:
        method = "chunk"
        items = _chunk_text(text)
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
    return JSONResponse({"added": added, "method": method, "skipped": skipped,
                         "total": len(added), "filename": body.filename,
                         "textPreview": text[:120]}, status_code=201)


def _chunk_text(text, max_len=200, max_items=10):
    parts = []
    for line in re.split(r"\r?\n+", text):
        sentences = [s.strip() for s in re.split(r"(?<=[。；;！!？?])", line) if s.strip()]
        parts.extend(sentences if sentences else [line.strip()])
    chunks = []
    for p in parts:
        if len(chunks) >= max_items:
            break
        if len(p) <= max_len:
            chunks.append(p)
        else:
            for i in range(0, len(p), max_len):
                if len(chunks) >= max_items:
                    break
                chunks.append(p[i:i + max_len])
    return chunks[:max_items]


# ---------------- RAG 攻略知识库 ----------------
@app.get("/api/guides")
def api_guides_list(request: Request):
    user, e = require_auth(request)
    if e:
        return e
    return JSONResponse({"guides": guide_store.list(user["id"])})


@app.post("/api/guides")
def api_guides_add(body: GuideIn, request: Request):
    user, e = require_auth(request)
    if e:
        return e
    try:
        buf = base64.b64decode(body.contentBase64)
    except Exception:
        return err(400, "文件内容编码无效")
    if len(buf) > MAX_FILE_BYTES:
        return err(400, "文件不能超过 3MB")
    ext = Path(body.filename).suffix.lower()
    if ext in (".txt", ".text", ".md"):
        text = decode_text_buffer(buf)
    elif ext == ".pdf":
        text = extract_pdf_text(buf)
    else:
        return err(400, "仅支持 TXT/PDF/MD 攻略文档")
    result = guide_store.add(user["id"], body.filename, text)
    if "error" in result:
        return err(400, result["error"])
    return JSONResponse({"guide": result["doc"], "chunks": result["chunks"]}, status_code=201)


@app.delete("/api/guides/{guide_id}")
def api_guides_delete(guide_id: str, request: Request):
    user, e = require_auth(request)
    if e:
        return e
    if not guide_store.remove(user["id"], guide_id):
        return err(404, "攻略文档不存在")
    return JSONResponse({"ok": True})


# ---------------- 对话（Function Calling + RAG + SSE） ----------------
@app.post("/api/chat")
def api_chat(body: ChatIn, request: Request):
    user, e = require_auth(request)
    if e:
        return e
    raw_messages = [_dump(m) for m in body.messages]
    history = [m for m in raw_messages
               if m.get("role") in ("user", "assistant")
               and isinstance(m.get("content"), str) and m["content"].strip()][-MAX_HISTORY:]
    history = [{"role": m["role"], "content": m["content"][:8000]} for m in history]

    def generate():
        if not history:
            yield sse({"type": "error", "message": "消息不能为空"})
            return
        if not OPENAI_API_KEY:
            yield sse({"type": "error", "message": "AI 服务未配置：请设置环境变量 OPENAI_API_KEY（或项目根目录 .env）后重启服务，可配合 OPENAI_BASE_URL / TP_OPENAI_MODEL"})
            return

        system_prompt = build_system_prompt(user, history)
        messages = [{"role": "system", "content": system_prompt}] + history

        def call_stream(payload):
            return requests.post(f"{OPENAI_BASE_URL}/chat/completions",
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {OPENAI_API_KEY}"},
                                 json=payload, stream=True, timeout=120)

        # ---- 第一轮：带 Function Calling 工具 ----
        payload = {"model": OPENAI_MODEL, "stream": True, "messages": messages,
                   "temperature": 0.7, "tools": TOOL_DEFS, "tool_choice": "auto"}
        assistant_parts = []
        tool_acc = {}
        finish_reason = None
        try:
            resp = call_stream(payload)
        except Exception as ex:
            yield sse({"type": "error", "message": f"无法连接 AI 服务：{ex}"})
            return
        if resp.status_code != 200:
            detail = ""
            try:
                detail = resp.json().get("error", {}).get("message", "")
            except Exception:
                detail = resp.text[:200]
            yield sse({"type": "error", "message": f"AI 服务返回错误（HTTP {resp.status_code}）：{detail}"})
            return

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
            except Exception:
                continue
            choice = (obj.get("choices") or [{}])[0]
            delta = choice.get("delta", {})
            if delta.get("content"):
                assistant_parts.append(delta["content"])
                yield sse({"type": "delta", "content": delta["content"]})
            for tc in delta.get("tool_calls") or []:
                idx = tc.get("index", 0)
                slot = tool_acc.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                if tc.get("id"):
                    slot["id"] = tc["id"]
                if tc.get("function", {}).get("name"):
                    slot["name"] = tc["function"]["name"]
                if tc.get("function", {}).get("arguments"):
                    slot["arguments"] += tc["function"]["arguments"]
            if choice.get("finish_reason"):
                finish_reason = choice["finish_reason"]

        assistant_content = "".join(assistant_parts)

        # ---- 执行工具（并行）并进入第二轮 ----
        if finish_reason == "tool_calls" and tool_acc:
            assistant_msg = {"role": "assistant", "content": assistant_content or None,
                             "tool_calls": [{"id": v["id"], "type": "function",
                                             "function": {"name": v["name"], "arguments": v["arguments"]}}
                                            for v in tool_acc.values()]}
            messages.append(assistant_msg)
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = {pool.submit(execute_tool, v["name"], json.loads(v["arguments"] or "{}")): v
                           for v in tool_acc.values()}
                for f in futures:
                    v = futures[f]
                    result = f.result()
                    if v["name"] == "get_weather" and isinstance(result, dict) and "error" in result:
                        # 偶发网络失败时自动重试一次，避免行程里出现“天气获取失败”
                        try:
                            time.sleep(0.8)
                            result = execute_tool(v["name"], json.loads(v["arguments"] or "{}"))
                        except Exception:
                            pass
                    yield sse({"type": "tool", "name": v["name"],
                               "summary": summarize_tool(v["name"], result)})
                    messages.append({"role": "tool", "tool_call_id": v["id"],
                                     "content": json.dumps(result, ensure_ascii=False)})

            try:
                resp2 = call_stream({"model": OPENAI_MODEL, "stream": True,
                                     "messages": messages, "temperature": 0.7})
            except Exception as ex:
                yield sse({"type": "error", "message": f"工具调用后无法连接 AI：{ex}"})
                return
            assistant_parts = []
            for raw in resp2.iter_lines(decode_unicode=False):
                if not raw:
                    continue
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    delta = (json.loads(data).get("choices") or [{}])[0].get("delta", {})
                except Exception:
                    continue
                if delta.get("content"):
                    assistant_parts.append(delta["content"])
                    yield sse({"type": "delta", "content": delta["content"]})
            assistant_content = "".join(assistant_parts)

        if assistant_content.strip():
            conv_existed = bool(body.conversationId and conversations_coll.get(body.conversationId))
            saved_id = save_conversation(user, body.conversationId, raw_messages, assistant_content)
            if not conv_existed:
                extract_memory(user, raw_messages)
            yield sse({"type": "done", "conversationId": saved_id})
        else:
            yield sse({"type": "error", "message": "AI 没有返回内容，请重试"})

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


# ---------------- 多 Agent 协作 ----------------
@app.post("/api/agents/plan")
def api_agents_plan(body: AgentsIn, request: Request):
    user, e = require_auth(request)
    if e:
        return e
    prefs = [p["text"] for p in vector_store.list(user["id"])]
    try:
        result = run_multi_agent(body.request, prefs, body.city)
        return JSONResponse(result)
    except Exception as ex:
        return err(500, f"多 Agent 规划失败：{ex}")


# ---------------- 对话历史 ----------------
@app.get("/api/conversations")
def api_conversations_list(request: Request):
    user, e = require_auth(request)
    if e:
        return e
    convs = [c for c in conversations_coll.all() if c.get("userId") == user["id"]]
    convs.sort(key=lambda c: c.get("updatedAt", ""), reverse=True)
    items = [{"id": c["id"], "title": c.get("title", "未命名对话"),
              "createdAt": c.get("createdAt"), "updatedAt": c.get("updatedAt"),
              "messageCount": len(c.get("messages", []))} for c in convs]
    return JSONResponse({"conversations": items})


@app.get("/api/conversations/{cid}")
def api_conversation_get(cid: str, request: Request):
    user, e = require_auth(request)
    if e:
        return e
    conv = conversations_coll.get(cid)
    if not conv or conv.get("userId") != user["id"]:
        return err(404, "对话不存在")
    return JSONResponse({"conversation": conv})


@app.put("/api/conversations/{cid}")
def api_conversation_update(cid: str, body: ConversationUpdateIn, request: Request):
    user, e = require_auth(request)
    if e:
        return e
    conv = conversations_coll.get(cid)
    if not conv or conv.get("userId") != user["id"]:
        return err(404, "对话不存在")
    patch = {"updatedAt": now_iso()}
    if body.title and body.title.strip():
        patch["title"] = body.title.strip()[:40]
    if body.messages is not None:
        patch["messages"] = body.messages
    return JSONResponse({"conversation": conversations_coll.update(cid, patch)})


@app.delete("/api/conversations/{cid}")
def api_conversation_delete(cid: str, request: Request):
    user, e = require_auth(request)
    if e:
        return e
    conv = conversations_coll.get(cid)
    if not conv or conv.get("userId") != user["id"]:
        return err(404, "对话不存在")
    conversations_coll.remove(cid)
    return JSONResponse({"ok": True})


# ---------------- 收藏 / 灵感库 ----------------
@app.get("/api/favorites")
def api_favorites_list(request: Request):
    user, e = require_auth(request)
    if e:
        return e
    favs = [f for f in favorites_coll.all() if f.get("userId") == user["id"]]
    favs.sort(key=lambda f: f.get("createdAt", ""), reverse=True)
    return JSONResponse({"favorites": favs})


@app.post("/api/favorites")
def api_favorites_add(body: FavoriteIn, request: Request):
    user, e = require_auth(request)
    if e:
        return e
    title = (body.title or "").strip()[:40] or "未命名收藏"
    content = (body.content or "").strip()
    if not content:
        return err(400, "内容不能为空")
    fav = {"id": random_id(), "userId": user["id"], "title": title,
           "content": content, "createdAt": now_iso()}
    favorites_coll.insert(fav)
    return JSONResponse({"favorite": fav}, status_code=201)


@app.delete("/api/favorites/{fav_id}")
def api_favorites_delete(fav_id: str, request: Request):
    user, e = require_auth(request)
    if e:
        return e
    fav = favorites_coll.get(fav_id)
    if not fav or fav.get("userId") != user["id"]:
        return err(404, "收藏不存在")
    favorites_coll.remove(fav_id)
    return JSONResponse({"ok": True})


# ---------------- 长期记忆管理 ----------------
@app.get("/api/me/memory")
def api_me_memory(request: Request):
    user, e = require_auth(request)
    if e:
        return e
    ms = [m for m in memory_coll.all() if m.get("userId") == user["id"]]
    ms.sort(key=lambda m: m.get("createdAt", ""), reverse=True)
    return JSONResponse({"memory": ms})


@app.delete("/api/me/memory/{mem_id}")
def api_me_memory_delete(mem_id: str, request: Request):
    user, e = require_auth(request)
    if e:
        return e
    mem = memory_coll.get(mem_id)
    if not mem or mem.get("userId") != user["id"]:
        return err(404, "记忆不存在")
    memory_coll.remove(mem_id)
    return JSONResponse({"ok": True})


# ---------------- 密码重置申请 ----------------
@app.post("/api/me/reset-request")
def api_reset_request(body: ResetRequestIn, request: Request):
    user, e = require_auth(request)
    if e:
        return e
    if reset_requests_coll.find(lambda r: r.get("userId") == user["id"] and r.get("status") == "pending"):
        return err(409, "已有待处理的申请，请等待管理员处理")
    req = {"id": random_id(), "userId": user["id"], "username": user.get("username", "?"),
           "reason": (body.reason or "").strip()[:100] or "未填写", "status": "pending",
           "createdAt": now_iso(), "handledAt": None, "handler": None}
    reset_requests_coll.insert(req)
    add_audit(user, "申请重置密码", user.get("username", "?"), req["reason"])
    return JSONResponse({"request": req}, status_code=201)


@app.get("/api/admin/reset-requests")
def api_admin_reset_requests(request: Request):
    admin, e = require_admin(request)
    if e:
        return e
    reqs = reset_requests_coll.all()
    reqs.sort(key=lambda r: r.get("createdAt", ""), reverse=True)
    return JSONResponse({"requests": reqs})


@app.post("/api/admin/reset-requests/{req_id}/approve")
def api_admin_reset_approve(req_id: str, request: Request):
    admin, e = require_admin(request)
    if e:
        return e
    req = reset_requests_coll.get(req_id)
    if not req:
        return err(404, "申请不存在")
    if req.get("status") != "pending":
        return err(400, "该申请已处理")
    target = users_coll.get(req.get("userId"))
    if not target:
        return err(404, "申请用户不存在")
    new_pwd = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(8))
    users_coll.update(target["id"], {"passwordHash": hash_password(new_pwd)})
    for s in sessions_coll.all():
        if s.get("userId") == target["id"]:
            sessions_coll.remove(s["id"])
    reset_requests_coll.update(req_id, {"status": "done", "handledAt": now_iso(),
                                        "handler": admin.get("username", "?")})
    add_audit(admin, "批准重置密码", target.get("username", "?"), "批准用户重置申请并生成新密码")
    return JSONResponse({"ok": True, "newPassword": new_pwd, "username": target.get("username", "")})


@app.post("/api/admin/reset-requests/{req_id}/reject")
def api_admin_reset_reject(req_id: str, request: Request):
    admin, e = require_admin(request)
    if e:
        return e
    req = reset_requests_coll.get(req_id)
    if not req:
        return err(404, "申请不存在")
    if req.get("status") != "pending":
        return err(400, "该申请已处理")
    reset_requests_coll.update(req_id, {"status": "rejected", "handledAt": now_iso(),
                                        "handler": admin.get("username", "?")})
    add_audit(admin, "拒绝重置密码", req.get("username", "?"), "拒绝用户重置申请")
    return JSONResponse({"ok": True})


# ---------------- 意见反馈（用户 → 管理员） ----------------
@app.post("/api/me/feedback")
def api_feedback_add(body: FeedbackIn, request: Request):
    user, e = require_auth(request)
    if e:
        return e
    content = (body.content or "").strip()
    if not content:
        return err(400, "反馈内容不能为空")
    if len(content) > 2000:
        return err(400, "反馈内容不能超过 2000 字")
    cat = (body.category or "其他").strip()
    if cat not in FEEDBACK_CATEGORIES:
        cat = "其他"
    fb = {"id": random_id(), "userId": user["id"], "username": user.get("username", "?"),
          "category": cat, "content": content, "status": "pending",
          "reply": None, "createdAt": now_iso(), "handledAt": None, "handler": None}
    feedback_coll.insert(fb)
    add_audit(user, "提交反馈", fb["username"], f"[{cat}] {content[:40]}")
    return JSONResponse({"feedback": fb}, status_code=201)


@app.get("/api/me/feedback")
def api_feedback_mine(request: Request):
    user, e = require_auth(request)
    if e:
        return e
    items = [f for f in feedback_coll.all() if f.get("userId") == user["id"]]
    items.sort(key=lambda f: f.get("createdAt", ""), reverse=True)
    return JSONResponse({"feedback": items})


@app.get("/api/admin/feedback")
def api_admin_feedback(request: Request, limit: int = 200):
    admin, e = require_admin(request)
    if e:
        return e
    items = feedback_coll.all()
    items.sort(key=lambda f: f.get("createdAt", ""), reverse=True)
    return JSONResponse({"feedback": items[:max(1, min(limit, 500))]})


@app.post("/api/admin/feedback/{fb_id}/handle")
def api_admin_feedback_handle(fb_id: str, body: FeedbackHandleIn, request: Request):
    admin, e = require_admin(request)
    if e:
        return e
    fb = feedback_coll.get(fb_id)
    if not fb:
        return err(404, "反馈不存在")
    reply = (body.reply or "").strip()
    patch = {"status": "done", "handledAt": now_iso(), "handler": admin.get("username", "?")}
    if reply:
        patch["reply"] = reply[:1000]
    updated = feedback_coll.update(fb_id, patch)
    add_audit(admin, "处理反馈", fb.get("username", "?"), reply or "标记为已处理")
    return JSONResponse({"feedback": updated})


# ---------------- 天气 / 导出 ----------------
@app.get("/api/weather")
def api_weather(city: str, request: Request):
    user, e = require_auth(request)
    if e:
        return e
    from tools import get_weather
    result = get_weather(city)
    if "error" in result:
        return err(404 if "未找到" in result["error"] else 503, result["error"])
    daily = []
    for d in result.get("daily", []):
        desc, icon = _wmo(d.get("weathercode", -1))
        daily.append({"date": d["date"], "desc": desc, "icon": icon,
                      "tmax": d["tmax"], "tmin": d["tmin"]})
    cur_desc, cur_icon = _wmo(result.get("weathercode", -1))
    return JSONResponse({
        "city": result["city"], "country": "",
        "current": {"temp": result["temp"], "wind": result.get("windspeed"),
                    "desc": cur_desc, "icon": cur_icon},
        "daily": daily[:5], "source": "Open-Meteo",
    })


def _xml_escape(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))


def build_docx(title, content):
    lines = content.splitlines()
    paragraphs = []
    for line in lines:
        text = line.rstrip()
        if not text.strip():
            paragraphs.append("<w:p/>")
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", text)
        if m:
            size = max(2, 6 - len(m.group(1)))
            paragraphs.append(
                '<w:p><w:pPr><w:pBdr><w:bottom w:val="single" w:sz="6" w:space="1" w:color="CCCCCC"/></w:pBdr></w:pPr>'
                f'<w:r><w:rPr><w:b/><w:sz w:val="{size * 2}"/></w:rPr><w:t xml:space="preserve">{_xml_escape(m.group(2))}</w:t></w:r></w:p>')
            continue
        paragraphs.append(f'<w:p><w:r><w:t xml:space="preserve">{_xml_escape(text)}</w:t></w:r></w:p>')
    body = "".join(paragraphs)
    title_para = (f'<w:p><w:r><w:rPr><w:b/><w:sz w:val="36"/></w:rPr>'
                  f'<w:t xml:space="preserve">{_xml_escape(title)}</w:t></w:r></w:p>')
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                f'<w:body>{title_para}{body}'
                '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/></w:sectPr></w:body></w:document>')
    content_types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                     '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                     '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                     '<Default Extension="xml" ContentType="application/xml"/>'
                     '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                     '</Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            '</Relationships>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", document)
    return buf.getvalue()


PDF_FONT_CANDIDATES = [r"C:\Windows\Fonts\simhei.ttf", r"C:\Windows\Fonts\msyh.ttc",
                       r"C:\Windows\Fonts\msyh.ttf", r"C:\Windows\Fonts\simsun.ttc",
                       r"C:\Windows\Fonts\simfang.ttf"]


def build_pdf(title, content):
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
            m = re.match(r"^(#{1,6})\s+(.*)$", text)
            if m:
                pdf.set_font("cjk", size=14)
                pdf.multi_cell(width, 9, m.group(2))
                pdf.set_font("cjk", size=11)
            else:
                pdf.multi_cell(width, 7, text)
        return bytes(pdf.output())
    except Exception:
        return None


@app.post("/api/export")
def api_export(body: ExportIn, request: Request):
    user, e = require_auth(request)
    if e:
        return e
    fmt = body.format.lower()
    if fmt == "markdown":
        fmt = "md"
    title = (body.title or "旅行计划").strip() or "旅行计划"
    content = (body.content or "").strip()
    if not content:
        return err(400, "没有可导出的内容")
    if fmt not in ("md", "txt", "docx", "pdf"):
        return err(400, "不支持的导出格式")
    base = re.sub(r'[\\/:*?"<>|]', "_", title)[:40] or "旅行计划"
    if fmt == "md":
        data = (f"# {title}\n\n{content}\n").encode("utf-8")
        mime = "text/markdown; charset=utf-8"
        fname = f"{base}.md"
    elif fmt == "txt":
        data = (f"{title}\n{'=' * len(title)}\n\n{content}\n").encode("utf-8")
        mime = "text/plain; charset=utf-8"
        fname = f"{base}.txt"
    elif fmt == "docx":
        data = build_docx(title, content)
        mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        fname = f"{base}.docx"
    else:
        data = build_pdf(title, content)
        if data is None:
            return err(400, "无法生成 PDF：缺少中文字体或 fpdf2（可导出 Word/文本）")
        mime = "application/pdf"
        fname = f"{base}.pdf"
    headers = {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"}
    return Response(data, media_type=mime, headers=headers)


# ---------------- 管理员 ----------------
@app.post("/api/admin/register")
def api_admin_register(body: AdminIn, request: Request):
    admin, e = require_admin(request)
    if e:
        return e
    msg = validate_credentials(body.username, body.password)
    if msg:
        return err(400, msg)
    if find_user_by_username(body.username):
        return err(409, "用户名已被占用")
    user = {"id": random_id(), "username": body.username,
            "email": (body.email or "").strip() or None,
            "passwordHash": hash_password(body.password), "role": "admin",
            "status": "active", "createdAt": now_iso()}
    users_coll.insert(user)
    add_audit(admin, "新增管理员", body.username, "由管理员创建")
    return JSONResponse({"user": sanitize_user(user)}, status_code=201)


@app.get("/api/admin/users")
def api_admin_users(request: Request):
    admin, e = require_admin(request)
    if e:
        return e
    safe_users = []
    for u in users_coll.all():
        su = sanitize_user(u)
        su.setdefault("status", "active")
        safe_users.append(su)
    return JSONResponse({"users": safe_users})


@app.patch("/api/admin/users/{user_id}/role")
def api_admin_role(user_id: str, body: RoleIn, request: Request):
    admin, e = require_admin(request)
    if e:
        return e
    target = users_coll.get(user_id)
    if not target:
        return err(404, "用户不存在")
    if body.role not in ("user", "admin"):
        return err(400, "角色只能是 user 或 admin")
    if user_id == admin["id"]:
        return err(400, "不能修改自己的角色")
    updated = users_coll.update(user_id, {"role": body.role})
    add_audit(admin, "修改角色", target.get("username", user_id),
              f"{target.get('role')} -> {body.role}")
    return JSONResponse({"user": sanitize_user(updated)})


@app.delete("/api/admin/users/{user_id}")
def api_admin_delete(user_id: str, request: Request):
    admin, e = require_admin(request)
    if e:
        return e
    target = users_coll.get(user_id)
    if not target:
        return err(404, "用户不存在")
    if user_id == admin["id"]:
        return err(400, "不能删除自己")
    for s in sessions_coll.all():
        if s.get("userId") == user_id:
            sessions_coll.remove(s["id"])
    users_coll.remove(user_id)
    add_audit(admin, "删除用户", target.get("username", user_id), "同时清理其会话")
    return JSONResponse({"ok": True})


@app.patch("/api/admin/users/{user_id}/status")
def api_admin_user_status(user_id: str, body: StatusIn, request: Request):
    admin, e = require_admin(request)
    if e:
        return e
    target = users_coll.get(user_id)
    if not target:
        return err(404, "用户不存在")
    if body.status not in ("active", "banned"):
        return err(400, "状态只能是 active 或 banned")
    if user_id == admin["id"]:
        return err(400, "不能封禁自己")
    if body.status == "banned":
        ban_until = None
        detail = "永久封禁"
        if body.banDays and body.banDays > 0:
            ban_until = (datetime.now(timezone.utc) + timedelta(days=body.banDays)).isoformat().replace("+00:00", "Z")
            detail = f"封禁 {body.banDays} 天，至 {ban_until[:10]}"
        users_coll.update(user_id, {"status": "banned", "banUntil": ban_until})
        for s in sessions_coll.all():
            if s.get("userId") == user_id:
                sessions_coll.remove(s["id"])
        add_audit(admin, "封禁账号", target.get("username", user_id), detail)
    else:
        users_coll.update(user_id, {"status": "active", "banUntil": None})
        add_audit(admin, "解封账号", target.get("username", user_id), "管理员解除封禁")
    return JSONResponse({"user": sanitize_user(users_coll.get(user_id))})


@app.post("/api/admin/users/{user_id}/reset-password")
def api_admin_reset_password(user_id: str, body: ResetPasswordIn, request: Request):
    admin, e = require_admin(request)
    if e:
        return e
    target = users_coll.get(user_id)
    if not target:
        return err(404, "用户不存在")
    if user_id == admin["id"]:
        return err(400, "不能重置自己的密码，请在个人中心修改")
    new_pwd = (body.newPassword or "").strip()
    if new_pwd and len(new_pwd) < 6:
        return err(400, "新密码至少 6 位")
    if not new_pwd:
        new_pwd = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(8))
    users_coll.update(user_id, {"passwordHash": hash_password(new_pwd)})
    # 使该用户所有旧会话失效
    for s in sessions_coll.all():
        if s.get("userId") == user_id:
            sessions_coll.remove(s["id"])
    add_audit(admin, "重置密码", target.get("username", user_id), "管理员重置用户密码")
    return JSONResponse({"ok": True, "newPassword": new_pwd})


@app.get("/api/admin/audit")
def api_admin_audit(request: Request, limit: int = 200):
    admin, e = require_admin(request)
    if e:
        return e
    logs = audit_coll.all()
    logs.sort(key=lambda x: x.get("createdAt", ""), reverse=True)
    return JSONResponse({"audit": logs[:max(1, min(limit, 500))]})


@app.delete("/api/admin/audit")
def api_admin_audit_clear(request: Request):
    admin, e = require_admin(request)
    if e:
        return e
    audit_coll.save([])
    add_audit(admin, "清空审计日志", "-", "管理员清空全部日志")
    return JSONResponse({"ok": True})

# ---------------- 静态前端 ----------------
@app.get("/")
def index_page():
    # Vue 3 版为唯一前端页面
    resp = FileResponse(str(PUBLIC_DIR / "vue-app" / "index.html"))
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/{path:path}")
def static_fallback(path: str):
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
    resp = FileResponse(str(target))
    resp.headers["Cache-Control"] = "no-store"
    return resp



# ---------------- 启动 ----------------
if __name__ == "__main__":
    import uvicorn
    print(f"[travel-planner-fastapi] 后端 API 已启动: http://{HOST}:{PORT}")
    print(f"[travel-planner-fastapi] 前端页面(Vue3): http://{HOST}:{PORT}/")
    print(f"[travel-planner-fastapi] AI 对话: {'已启用（' + OPENAI_MODEL + ' @ ' + OPENAI_BASE_URL + '）' if OPENAI_API_KEY else '未配置（请设置 OPENAI_API_KEY）'}")
    uvicorn.run(app, host=HOST, port=PORT)
