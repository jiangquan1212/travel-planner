'use strict';

/**
 * travel-planner 后端服务器（零第三方依赖）
 * - 提供静态前端 (public/)
 * - 用户/管理员登录认证
 * - AI 智能对话生成多方位旅游计划（DeepSeek / OpenAI 兼容 Chat Completions API，流式）
 */

const http = require('http');
const fs = require('fs');
const path = require('path');

// ---------- 加载项目根目录 .env（零依赖） ----------
const ENV_FILE = path.join(__dirname, '.env');
if (fs.existsSync(ENV_FILE)) {
  for (const rawLine of fs.readFileSync(ENV_FILE, 'utf8').split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith('#')) continue;
    const eq = line.indexOf('=');
    if (eq <= 0) continue;
    const key = line.slice(0, eq).trim();
    const value = line.slice(eq + 1).trim().replace(/^['"]|['"]$/g, '');
    if (key && process.env[key] === undefined) process.env[key] = value;
  }
}

const { JsonStore } = require('./lib/store');
const { VectorStore } = require('./lib/vector');
const { extractPdfText } = require('./lib/pdf');
const {
  hashPassword,
  verifyPassword,
  createSessionToken,
  buildSessionCookie,
  buildClearCookie,
  parseSessionToken,
  randomId,
  sanitizeUser,
  SESSION_MAX_AGE_MS,
} = require('./lib/auth');

const PORT = Number(process.env.PORT) || 3000;
const HOST = process.env.HOST || '127.0.0.1';
const PUBLIC_DIR = path.join(__dirname, 'public');

// ---------- AI 对话配置（DeepSeek，OpenAI 兼容） ----------
const OPENAI_API_KEY = process.env.OPENAI_API_KEY || '';
const OPENAI_BASE_URL = (process.env.OPENAI_BASE_URL || 'https://api.deepseek.com').replace(/\/+$/, '');
const OPENAI_MODEL = process.env.TP_OPENAI_MODEL || process.env.OPENAI_MODEL || 'deepseek-chat';
const MAX_HISTORY_MESSAGES = 20;

const SYSTEM_PROMPT = `你是"旅行规划师"，一位经验丰富的资深旅游顾问，擅长为不同人群定制多方位旅行计划。

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
- 不要编造真实的价格区间之外过于精确的信息，给出区间即可`;

const store = new JsonStore();
const vectorStore = new VectorStore();

// ---------- 工具函数 ----------

function now() {
  return new Date().toISOString();
}

function sendJson(res, status, body) {
  const payload = JSON.stringify(body);
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Cache-Control': 'no-store',
  });
  res.end(payload);
}

function sendError(res, status, message) {
  sendJson(res, status, { error: message });
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    let raw = '';
    req.on('data', (chunk) => {
      raw += chunk;
      if (raw.length > 10e6) {
        reject(new Error('请求体过大'));
        req.destroy();
      }
    });
    req.on('end', () => {
      if (!raw) return resolve({});
      try {
        resolve(JSON.parse(raw));
      } catch {
        reject(new Error('请求体不是有效的 JSON'));
      }
    });
    req.on('error', reject);
  });
}

// ---------- 会话与权限 ----------

function getSessionUser(req) {
  const token = parseSessionToken(req.headers.cookie || '');
  if (!token) return null;
  const session = store.find('sessions', (s) => s.token === token);
  if (!session) return null;
  if (session.expiresAt && new Date(session.expiresAt).getTime() < Date.now()) {
    store.remove('sessions', session.id);
    return null;
  }
  const user = store.getById('users', session.userId);
  if (!user) return null;
  return user;
}

function requireAuth(req, res) {
  const user = getSessionUser(req);
  if (!user) {
    sendError(res, 401, '未登录或会话已过期');
    return null;
  }
  return user;
}

function requireAdmin(req, res) {
  const user = requireAuth(req, res);
  if (!user) return null;
  if (user.role !== 'admin') {
    sendError(res, 403, '需要管理员权限');
    return null;
  }
  return user;
}

function findUserByUsername(username) {
  return store.find('users', (u) => u.username.toLowerCase() === String(username).toLowerCase());
}

function validateCredentials(body) {
  const username = String(body.username || '').trim();
  const password = String(body.password || '');
  if (!/^[A-Za-z0-9_]{3,20}$/.test(username)) {
    return { error: '用户名需为 3-20 位字母、数字或下划线' };
  }
  if (password.length < 6) {
    return { error: '密码至少 6 位' };
  }
  return { username, password };
}

function createSessionForUser(user) {
  const token = createSessionToken();
  const session = {
    id: randomId(),
    token,
    userId: user.id,
    createdAt: now(),
    expiresAt: new Date(Date.now() + SESSION_MAX_AGE_MS).toISOString(),
  };
  store.insert('sessions', session);
  return session;
}

// ---------- AI 对话 ----------

function chatEnabled() {
  return Boolean(OPENAI_API_KEY);
}

function decodeTextBuffer(buf) {
  try { return new TextDecoder('utf-8', { fatal: true }).decode(buf); } catch { /* 非 UTF-8 */ }
  try { return new TextDecoder('gbk').decode(buf); } catch { /* 无 GBK 支持 */ }
  return buf.toString('latin1');
}

function chunkText(text, maxLen = 200, maxItems = 10) {
  const candidates = text
    .split(/\r?\n+/)
    .flatMap((line) => {
      // 先按句号/分号/感叹/问号切，保留编号前缀（如 "1. "）
      const sentences = line.split(/(?<=[。；;！!？?])/).map((s) => s.trim()).filter(Boolean);
      return sentences.length ? sentences : [line.trim()];
    })
    .filter((s) => s.length > 0);
  const chunks = [];
  for (const p of candidates) {
    if (chunks.length >= maxItems) break;
    if (p.length <= maxLen) { chunks.push(p); continue; }
    // 超长段落：按 200 字硬切
    for (let i = 0; i < p.length; i += maxLen) {
      if (chunks.length >= maxItems) break;
      chunks.push(p.slice(i, i + maxLen));
    }
  }
  return chunks.slice(0, maxItems);
}

async function llmComplete(messages) {
  if (!chatEnabled()) throw new Error('AI 未配置');
  const res = await fetch(`${OPENAI_BASE_URL}/chat/completions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${OPENAI_API_KEY}` },
    body: JSON.stringify({ model: OPENAI_MODEL, stream: false, messages, temperature: 0.2, max_tokens: 1000 }),
    signal: AbortSignal.timeout(60000),
  });
  if (!res.ok) throw new Error(`AI 服务返回 HTTP ${res.status}`);
  const data = await res.json();
  return (data.choices && data.choices[0] && data.choices[0].message && data.choices[0].message.content) || '';
}

async function extractPreferencesFromText(text) {
  const content = await llmComplete([
    {
      role: 'system',
      content: '你是旅行偏好提取助手。从用户上传的旅行资料中提取明确的旅行偏好（美食、住宿、预算、目的地、出行方式、游玩风格等）。只输出 JSON 字符串数组，例如 ["偏好辣味美食","喜欢海边"]，每项是一条简短独立的旅行偏好（不超过 40 字），提取 1-15 条；没有明确偏好时输出 []。不要输出对象数组或任何其它内容。',
    },
    { role: 'user', content: text.slice(0, 20000) },
  ]);
  const m = content.match(/\[[\s\S]*\]/);
  const parsed = JSON.parse(m ? m[0] : content);
  if (!Array.isArray(parsed)) return [];
  return parsed
    .map((x) => (typeof x === 'string' ? x : x && typeof x === 'object' ? Object.values(x)[0] : ''))
    .map((s) => String(s || '').trim())
    .filter(Boolean);
}

function sseSend(res, payload) {
  res.write(`data: ${JSON.stringify(payload)}\n\n`);
}

async function streamChat(req, res, user, messages) {
  res.writeHead(200, {
    'Content-Type': 'text/event-stream; charset=utf-8',
    'Cache-Control': 'no-store',
    Connection: 'keep-alive',
    'X-Accel-Buffering': 'no',
  });

  const history = Array.isArray(messages) ? messages.slice(-MAX_HISTORY_MESSAGES) : [];
  const cleanHistory = history
    .filter((m) => m && (m.role === 'user' || m.role === 'assistant') && typeof m.content === 'string' && m.content.trim())
    .map((m) => ({ role: m.role, content: m.content.slice(0, 8000) }));

  if (!cleanHistory.length) {
    sseSend(res, { type: 'error', message: '消息不能为空' });
    return res.end();
  }

  if (!chatEnabled()) {
    sseSend(res, {
      type: 'error',
      message: 'AI 服务未配置：请设置环境变量 OPENAI_API_KEY（或项目根目录 .env）后重启服务，可配合 OPENAI_BASE_URL / TP_OPENAI_MODEL',
    });
    return res.end();
  }

  // 向量检索用户偏好并注入系统提示（RAG）
  let systemPrompt = SYSTEM_PROMPT;
  const lastUserMsg = [...cleanHistory].reverse().find((m) => m.role === 'user');
  if (lastUserMsg) {
    const related = vectorStore.search(user.id, lastUserMsg.content, 3);
    if (related.length) {
      systemPrompt += `\n\n【用户已保存的旅行偏好，按与当前问题的相关度从高到低排列】\n${related.map((p) => `- ${p.text}`).join('\n')}\n请在回答时优先参考这些偏好。`;
    }
  }

  const payload = {

    model: OPENAI_MODEL,
    stream: true,
    messages: [{ role: 'system', content: systemPrompt }, ...cleanHistory],
    temperature: 0.7,
  };

  let upstream;
  try {
    upstream = await fetch(`${OPENAI_BASE_URL}/chat/completions`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${OPENAI_API_KEY}`,
      },
      body: JSON.stringify(payload),
      signal: AbortSignal.timeout(120000),
    });
  } catch (err) {
    sseSend(res, { type: 'error', message: `无法连接 AI 服务：${err.message}` });
    return res.end();
  }

  if (!upstream.ok || !upstream.body) {
    let detail = `AI 服务返回错误（HTTP ${upstream.status}）`;
    try {
      const data = await upstream.json();
      if (data && data.error) {
        detail += `：${data.error.message || JSON.stringify(data.error)}`;
      }
    } catch { /* ignore */ }
    sseSend(res, { type: 'error', message: detail });
    return res.end();
  }

  req.on('close', () => {
    upstream.body.cancel().catch(() => {});
  });

  let buffer = '';
  const decoder = new TextDecoder('utf-8');
  try {
    for await (const chunk of upstream.body) {
      buffer += decoder.decode(chunk, { stream: true });
      let idx;
      while ((idx = buffer.indexOf('\n')) !== -1) {
        const line = buffer.slice(0, idx).trim();
        buffer = buffer.slice(idx + 1);
        if (!line.startsWith('data:')) continue;
        const data = line.slice(5).trim();
        if (data === '[DONE]') {
          sseSend(res, { type: 'done' });
          return res.end();
        }
        try {
          const parsed = JSON.parse(data);
          const delta = parsed.choices && parsed.choices[0] && parsed.choices[0].delta;
          if (delta && typeof delta.content === 'string' && delta.content) {
            sseSend(res, { type: 'delta', content: delta.content });
          }
        } catch { /* 忽略无法解析的块 */ }
      }
    }
    sseSend(res, { type: 'done' });
    res.end();
  } catch (err) {
    if (res.writableEnded) return;
    sseSend(res, { type: 'error', message: `对话中断：${err.message}` });
    res.end();
  }
}

// ---------- 静态文件服务 ----------

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.gif': 'image/gif',
  '.svg': 'image/svg+xml',
  '.ico': 'image/x-icon',
  '.txt': 'text/plain; charset=utf-8',
};

function serveStatic(req, res) {
  let urlPath;
  try {
    urlPath = decodeURIComponent(new URL(req.url, 'http://localhost').pathname);
  } catch {
    return sendError(res, 400, '无效的 URL');
  }
  if (urlPath === '/' || urlPath === '') urlPath = '/index.html';
  const filePath = path.normalize(path.join(PUBLIC_DIR, urlPath));
  if (!filePath.startsWith(PUBLIC_DIR)) return sendError(res, 403, '禁止访问');
  fs.readFile(filePath, (err, data) => {
    if (err) return sendError(res, 404, '资源不存在');
    const ext = path.extname(filePath).toLowerCase();
    res.writeHead(200, {
      'Content-Type': MIME[ext] || 'application/octet-stream',
      'Cache-Control': 'no-cache',
    });
    res.end(data);
  });
}

// ---------- API 路由 ----------

const routes = {};

function route(method, pattern, handler) {
  routes[`${method} ${pattern}`] = handler;
}

route('POST', '/api/auth/register', async (req, res) => {
  const body = await readBody(req);
  const { error, username, password } = validateCredentials(body);
  if (error) return sendError(res, 400, error);
  if (findUserByUsername(username)) return sendError(res, 409, '用户名已被占用');
  const email = String(body.email || '').trim();
  if (email && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
    return sendError(res, 400, '邮箱格式不正确');
  }
  const user = {
    id: randomId(),
    username,
    email: email || null,
    passwordHash: hashPassword(password),
    role: 'user',
    createdAt: now(),
  };
  store.insert('users', user);
  const session = createSessionForUser(user);
  res.setHeader('Set-Cookie', buildSessionCookie(session.token));
  sendJson(res, 201, { user: sanitizeUser(user) });
});

route('POST', '/api/auth/login', async (req, res) => {
  const body = await readBody(req);
  const username = String(body.username || '').trim();
  const password = String(body.password || '');
  const user = findUserByUsername(username);
  if (!user || !verifyPassword(password, user.passwordHash)) {
    return sendError(res, 401, '用户名或密码错误');
  }
  const session = createSessionForUser(user);
  res.setHeader('Set-Cookie', buildSessionCookie(session.token));
  sendJson(res, 200, { user: sanitizeUser(user) });
});

route('POST', '/api/auth/logout', (req, res) => {
  const token = parseSessionToken(req.headers.cookie || '');
  if (token) {
    const session = store.find('sessions', (s) => s.token === token);
    if (session) store.remove('sessions', session.id);
  }
  res.setHeader('Set-Cookie', buildClearCookie());
  sendJson(res, 200, { ok: true });
});

route('GET', '/api/auth/me', (req, res) => {
  const user = requireAuth(req, res);
  if (!user) return;
  sendJson(res, 200, { user: sanitizeUser(user) });
});

// --- AI 对话 ---

route('GET', '/api/config', (req, res) => {
  const user = requireAuth(req, res);
  if (!user) return;
  sendJson(res, 200, { chatEnabled: chatEnabled(), model: OPENAI_MODEL, baseUrl: OPENAI_BASE_URL });
});

route('POST', '/api/chat', async (req, res) => {
  const user = requireAuth(req, res);
  if (!user) return;
  const body = await readBody(req);
  return streamChat(req, res, user, body.messages);
});

// --- 管理员接口 ---

route('POST', '/api/admin/register', async (req, res) => {
  const admin = requireAdmin(req, res);
  if (!admin) return;
  const body = await readBody(req);
  const { error, username, password } = validateCredentials(body);
  if (error) return sendError(res, 400, error);
  if (findUserByUsername(username)) return sendError(res, 409, '用户名已被占用');
  const target = {
    id: randomId(),
    username,
    email: String(body.email || '').trim() || null,
    passwordHash: hashPassword(password),
    role: 'admin',
    createdAt: now(),
  };
  store.insert('users', target);
  sendJson(res, 201, { user: sanitizeUser(target) });
});

route('GET', '/api/admin/users', (req, res) => {
  const admin = requireAdmin(req, res);
  if (!admin) return;
  const users = store.all('users').map(sanitizeUser);
  sendJson(res, 200, { users });
});

route('PATCH', '/api/admin/users/:id/role', async (req, res) => {
  const admin = requireAdmin(req, res);
  if (!admin) return;
  const target = store.getById('users', req.params.id);
  if (!target) return sendError(res, 404, '用户不存在');
  const body = await readBody(req);
  const role = body.role;
  if (role !== 'user' && role !== 'admin') return sendError(res, 400, '角色只能是 user 或 admin');
  if (target.id === admin.id) return sendError(res, 400, '不能修改自己的角色');
  const updated = store.update('users', target.id, { role });
  sendJson(res, 200, { user: sanitizeUser(updated) });
});

route('DELETE', '/api/admin/users/:id', (req, res) => {
  const admin = requireAdmin(req, res);
  if (!admin) return;
  const target = store.getById('users', req.params.id);
  if (!target) return sendError(res, 404, '用户不存在');
  if (target.id === admin.id) return sendError(res, 400, '不能删除自己');
  store.filter('sessions', (s) => s.userId === target.id).forEach((s) => store.remove('sessions', s.id));
  store.remove('users', target.id);
  sendJson(res, 200, { ok: true });
});

// --- 用户偏好（向量数据库） ---

route('GET', '/api/preferences', (req, res) => {
  const user = requireAuth(req, res);
  if (!user) return;
  sendJson(res, 200, { preferences: vectorStore.list(user.id) });
});

route('POST', '/api/preferences', async (req, res) => {
  const user = requireAuth(req, res);
  if (!user) return;
  const body = await readBody(req);
  const result = vectorStore.add(user.id, body.text);
  if (result.error) return sendError(res, 400, result.error);
  sendJson(res, 201, { preference: result.doc });
});

route('DELETE', '/api/preferences/:id', (req, res) => {
  const user = requireAuth(req, res);
  if (!user) return;
  const ok = vectorStore.remove(user.id, req.params.id);
  if (!ok) return sendError(res, 404, '偏好不存在');
  sendJson(res, 200, { ok: true });
});

route('POST', '/api/preferences/import', async (req, res) => {
  const user = requireAuth(req, res);
  if (!user) return;
  const body = await readBody(req);
  const filename = String(body.filename || '').trim();
  const b64 = String(body.contentBase64 || '');
  if (!filename || !b64) return sendError(res, 400, '缺少文件名或文件内容');
  let buf;
  try { buf = Buffer.from(b64, 'base64'); } catch { return sendError(res, 400, '文件内容编码无效'); }
  if (!buf.length) return sendError(res, 400, '文件为空');
  if (buf.length > 3 * 1024 * 1024) return sendError(res, 400, '文件不能超过 3MB');

  const ext = path.extname(filename).toLowerCase();
  let text = '';
  if (ext === '.txt' || ext === '.text' || ext === '.md') text = decodeTextBuffer(buf);
  else if (ext === '.pdf') text = extractPdfText(buf);
  else return sendError(res, 400, '仅支持 PDF 或 TXT 文件');

  text = text.replace(/\r\n/g, '\n').trim();
  if (!text) return sendError(res, 400, '未能从文件中读取到文本（扫描件/图片型 PDF 可能无法提取）');
  if (text.length > 50000) text = text.slice(0, 50000);

  // 先用 AI 提取偏好，失败则按句分段兜底
  let method = 'ai';
  let items = null;
  try { items = await extractPreferencesFromText(text); } catch (err) { console.error('[import] AI 提取失败:', err.message); items = null; }
  if (!Array.isArray(items) || !items.length) {
    method = 'chunk';
    items = chunkText(text);
  }

  const added = [];
  let skipped = 0;
  for (const item of items) {
    const s = String(item || '').trim().slice(0, 200);
    if (!s) continue;
    const r = vectorStore.add(user.id, s);
    if (r.doc) added.push(r.doc); else skipped++;
  }
  sendJson(res, 201, {
    added,
    method,
    skipped,
    total: added.length,
    filename,
    textPreview: text.slice(0, 120),
  });
});

// ---------- 路由分发 ----------

async function handleApi(req, res, url) {
  const method = req.method.toUpperCase();
  const pathname = url.pathname;
  for (const key of Object.keys(routes)) {
    const [m, pattern] = key.split(' ');
    if (m !== method) continue;
    const match = matchPath(pattern, pathname);
    if (!match) continue;
    req.params = match;
    try {
      return await routes[key](req, res);
    } catch (err) {
      return sendError(res, 400, err.message || '请求处理失败');
    }
  }
  sendError(res, 404, '接口不存在');
}

function matchPath(pattern, pathname) {
  const parts = pattern.split('/').filter(Boolean);
  const values = pathname.split('/').filter(Boolean);
  if (parts.length !== values.length) return null;
  const params = {};
  for (let i = 0; i < parts.length; i++) {
    if (parts[i].startsWith(':')) {
      params[parts[i].slice(1)] = values[i];
    } else if (parts[i] !== values[i]) {
      return null;
    }
  }
  return params;
}

// ---------- 服务器 ----------

const server = http.createServer(async (req, res) => {
  let url;
  try {
    url = new URL(req.url, `http://${req.headers.host || 'localhost'}`);
  } catch {
    return sendError(res, 400, '无效的 URL');
  }
  if (url.pathname.startsWith('/api/')) {
    return handleApi(req, res, url);
  }
  if (req.method === 'GET' || req.method === 'HEAD') {
    return serveStatic(req, res);
  }
  sendError(res, 405, '方法不允许');
});

server.listen(PORT, HOST, () => {
  console.log(`[travel-planner] 后端 API 已启动: http://${HOST}:${PORT}`);
  console.log(`[travel-planner] 前端页面:      http://${HOST}:${PORT}/`);
  console.log(`[travel-planner] AI 对话: ${chatEnabled() ? `已启用（${OPENAI_MODEL} @ ${OPENAI_BASE_URL}）` : '未配置（请设置 OPENAI_API_KEY）'}`);
});

server.on('error', (err) => {
  console.error('[travel-planner] 启动失败:', err.message);
  process.exit(1);
});

module.exports = { server, store, routes, chatEnabled, vectorStore };







