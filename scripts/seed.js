'use strict';

/**
 * 初始化演示数据：默认管理员 admin/admin123、普通用户 user/user123
 * 并给演示用户添加几条旅行偏好（存入向量数据库）
 * 用法: npm run seed
 */

const { JsonStore } = require('../lib/store');
const { VectorStore } = require('../lib/vector');
const { hashPassword, randomId } = require('../lib/auth');

const store = new JsonStore();
const vectorStore = new VectorStore();

function ensureUser(username, password, role) {
  const exists = store.find('users', (u) => u.username.toLowerCase() === username.toLowerCase());
  if (exists) {
    console.log(`[seed] ${username} 已存在，跳过`);
    return exists;
  }
  const user = {
    id: randomId(),
    username,
    email: null,
    passwordHash: hashPassword(password),
    role,
    createdAt: new Date().toISOString(),
  };
  store.insert('users', user);
  console.log(`[seed] 已创建 ${role}: ${username} / ${password}`);
  return user;
}

function ensurePref(user, text) {
  const exists = vectorStore.list(user.id).some((p) => p.text === text);
  if (exists) {
    console.log(`[seed] 偏好已存在，跳过: ${text}`);
    return;
  }
  const result = vectorStore.add(user.id, text);
  if (result.error) {
    console.log(`[seed] 偏好添加失败: ${text} (${result.error})`);
    return;
  }
  console.log(`[seed] 已为 ${user.username} 添加偏好: ${text}`);
}

const admin = ensureUser('admin', 'admin123', 'admin');
const user = ensureUser('user', 'user123', 'user');

ensurePref(user, '喜欢美食和当地特色小吃');
ensurePref(user, '偏好经济型住宿，注重性价比');
ensurePref(user, '喜欢自然风光，不喜欢人多的热门景点');

console.log('[seed] 完成 ✔');
