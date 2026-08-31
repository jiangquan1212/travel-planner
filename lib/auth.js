'use strict';

/**
 * 认证工具：密码哈希（scrypt）、会话令牌、签名 Cookie。
 * 使用 Node 内置 crypto，零第三方依赖。
 */

const crypto = require('crypto');

const SESSION_COOKIE = 'tp_session';
const SESSION_MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000; // 7 天
// 生产环境请通过环境变量 TP_SECRET 覆盖此默认值
const SECRET = process.env.TP_SECRET || 'travel-planner-dev-secret-please-change-in-prod';

// ---------- 密码哈希 ----------

function hashPassword(password) {
  const salt = crypto.randomBytes(16).toString('hex');
  const hash = crypto.scryptSync(String(password), salt, 64).toString('hex');
  return `${salt}:${hash}`;
}

function verifyPassword(password, stored) {
  if (!stored || typeof stored !== 'string') return false;
  const parts = stored.split(':');
  if (parts.length !== 2) return false;
  const [salt, hash] = parts;
  if (!salt || !hash) return false;
  try {
    const test = crypto.scryptSync(String(password), salt, 64);
    const expected = Buffer.from(hash, 'hex');
    return test.length === expected.length && crypto.timingSafeEqual(test, expected);
  } catch {
    return false;
  }
}

// ---------- 会话令牌与 Cookie ----------

function createSessionToken() {
  return crypto.randomBytes(32).toString('hex');
}

function sign(value) {
  return crypto.createHmac('sha256', SECRET).update(value).digest('base64url');
}

function buildSessionCookie(token, maxAgeMs = SESSION_MAX_AGE_MS) {
  const payload = `${token}.${sign(token)}`;
  const maxAgeSec = Math.floor(maxAgeMs / 1000);
  return `${SESSION_COOKIE}=${payload}; HttpOnly; Path=/; Max-Age=${maxAgeSec}; SameSite=Lax`;
}

function buildClearCookie() {
  return `${SESSION_COOKIE}=; HttpOnly; Path=/; Max-Age=0; SameSite=Lax`;
}

/**
 * 从 Cookie 头解析出有效的会话令牌（校验 HMAC 签名）。
 * @param {string|null} cookieHeader
 * @returns {string|null}
 */
function parseSessionToken(cookieHeader) {
  if (!cookieHeader) return null;
  const match = cookieHeader.match(/(?:^|;\s*)tp_session=([^;]+)/);
  if (!match) return null;
  const payload = match[1];
  const dot = payload.lastIndexOf('.');
  if (dot <= 0 || dot === payload.length - 1) return null;
  const token = payload.slice(0, dot);
  const sig = payload.slice(dot + 1);
  const expected = sign(token);
  const a = Buffer.from(sig);
  const b = Buffer.from(expected);
  if (a.length !== b.length || !crypto.timingSafeEqual(a, b)) return null;
  return token;
}

// ---------- 其他 ----------

function randomId() {
  return crypto.randomBytes(16).toString('hex');
}

function sanitizeUser(user) {
  if (!user) return null;
  const { passwordHash, ...safe } = user;
  return safe;
}

module.exports = {
  SESSION_COOKIE,
  SESSION_MAX_AGE_MS,
  hashPassword,
  verifyPassword,
  createSessionToken,
  buildSessionCookie,
  buildClearCookie,
  parseSessionToken,
  randomId,
  sanitizeUser,
};
