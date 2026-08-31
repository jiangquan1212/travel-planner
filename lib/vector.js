'use strict';

/**
 * 轻量向量数据库（零第三方依赖）
 * - 中文/英文分词（中文按二元组，英文按单词）
 * - TF-IDF 向量化 + 余弦相似度检索
 * - 数据持久化到 data/preferences.json
 */

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const FILE = path.join(__dirname, '..', 'data', 'preferences.json');
const MAX_TEXT_LEN = 200;

function tokenize(text) {
  const s = String(text || '').toLowerCase();
  const tokens = [];
  // 英文单词 / 数字
  for (const m of s.matchAll(/[a-z0-9]+/g)) tokens.push(m[0]);
  // 中文：二元组 + 单字（提升短偏好与近义表达的重合度）
  const han = s.replace(/[^\u4e00-\u9fa5]/g, '');
  if (han.length === 1) tokens.push(han);
  for (let i = 0; i < han.length - 1; i++) tokens.push(han.slice(i, i + 2));
  for (const ch of han) tokens.push(ch);
  return tokens;
}

function cosine(a, b) {
  let dot = 0;
  let na = 0;
  let nb = 0;
  for (const [k, v] of a) {
    dot += v * (b.get(k) || 0);
    na += v * v;
  }
  for (const v of b.values()) nb += v * v;
  if (na === 0 || nb === 0) return 0;
  return dot / (Math.sqrt(na) * Math.sqrt(nb));
}

class VectorStore {
  constructor(file = FILE) {
    this.file = file;
    this.docs = [];          // [{id, userId, text, createdAt}]
    this._index = new Map(); // userId -> Map(docId -> Map(token -> weight))
    this._load();
    this._rebuild();
  }

  _load() {
    if (!fs.existsSync(this.file)) return;
    try {
      const parsed = JSON.parse(fs.readFileSync(this.file, 'utf8'));
      this.docs = Array.isArray(parsed) ? parsed : [];
    } catch (err) {
      console.error(`[vector] 无法解析 ${this.file}，已重置: ${err.message}`);
      this.docs = [];
    }
  }

  _save() {
    const tmp = `${this.file}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify(this.docs, null, 2), 'utf8');
    fs.renameSync(tmp, this.file);
  }

  _rebuild() {
    this._index = new Map();
    // 按用户分组，分别计算 TF-IDF（IDF 基于该用户的偏好集合）
    const byUser = new Map();
    for (const doc of this.docs) {
      if (!byUser.has(doc.userId)) byUser.set(doc.userId, []);
      byUser.get(doc.userId).push(doc);
    }
    for (const [userId, docs] of byUser) {
      const tokenized = docs.map((d) => tokenize(d.text));
      const df = new Map();
      for (const toks of tokenized) {
        for (const t of new Set(toks)) df.set(t, (df.get(t) || 0) + 1);
      }
      const N = docs.length;
      const idf = (t) => Math.log((N + 1) / ((df.get(t) || 0) + 1)) + 1;
      const userMap = new Map();
      tokenized.forEach((toks, i) => {
        const tf = new Map();
        for (const t of toks) tf.set(t, (tf.get(t) || 0) + 1);
        const vec = new Map();
        for (const [t, c] of tf) vec.set(t, (c / toks.length) * idf(t));
        userMap.set(docs[i].id, vec);
      });
      this._index.set(userId, userMap);
    }
  }

  _userVecs(userId) {
    return this._index.get(userId) || new Map();
  }

  add(userId, text) {
    text = String(text || '').trim();
    if (!text) return { error: '偏好内容不能为空' };
    if (text.length > MAX_TEXT_LEN) return { error: `偏好最多 ${MAX_TEXT_LEN} 字` };
    const exists = this.docs.find((d) => d.userId === userId && d.text === text);
    if (exists) return { error: '该偏好已存在' };
    const doc = {
      id: crypto.randomBytes(8).toString('hex'),
      userId,
      text,
      createdAt: new Date().toISOString(),
    };
    this.docs.push(doc);
    this._save();
    this._rebuild();
    return { doc };
  }

  remove(userId, id) {
    const idx = this.docs.findIndex((d) => d.id === id && d.userId === userId);
    if (idx === -1) return false;
    this.docs.splice(idx, 1);
    this._save();
    this._rebuild();
    return true;
  }

  list(userId) {
    return this.docs
      .filter((d) => d.userId === userId)
      .sort((a, b) => (a.createdAt < b.createdAt ? -1 : 1));
  }

  /**
   * 向量相似度检索：返回与该用户 query 最相关的 topK 条偏好（按分数降序）
   * @returns [{id, text, score}]
   */
  search(userId, query, topK = 3) {
    const qTokens = tokenize(query);
    const userVecs = this._userVecs(userId);
    if (!qTokens.length || !userVecs.size) return [];
    const qTf = new Map();
    for (const t of qTokens) qTf.set(t, (qTf.get(t) || 0) + 1);
    // 用该用户各文档的 idf 均值近似查询向量 idf
    const docs = this.docs.filter((d) => d.userId === userId);
    const df = new Map();
    for (const d of docs) {
      for (const t of new Set(tokenize(d.text))) df.set(t, (df.get(t) || 0) + 1);
    }
    const N = docs.length || 1;
    const idf = (t) => Math.log((N + 1) / ((df.get(t) || 0) + 1)) + 1;
    const qVec = new Map();
    for (const [t, c] of qTf) qVec.set(t, (c / qTokens.length) * idf(t));

    const results = [];
    for (const [docId, vec] of userVecs) {
      const score = cosine(qVec, vec);
      if (score > 0) {
        const doc = this.docs.find((d) => d.id === docId);
        results.push({ id: docId, text: doc ? doc.text : '', score: Number(score.toFixed(4)) });
      }
    }
    results.sort((a, b) => b.score - a.score);
    return results.slice(0, topK);
  }
}

module.exports = { VectorStore, tokenize, cosine };

