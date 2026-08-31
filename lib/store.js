'use strict';

/**
 * 轻量 JSON 文件存储（零依赖）。
 * 每个集合对应 data/ 下的一个 JSON 文件，写入时先写临时文件再原子重命名。
 */

const fs = require('fs');
const path = require('path');

const DATA_DIR = path.join(__dirname, '..', 'data');
const COLLECTIONS = ['users', 'sessions'];

class JsonStore {
  constructor() {
    fs.mkdirSync(DATA_DIR, { recursive: true });
    this.files = {};
    this.data = {};
    for (const key of COLLECTIONS) {
      this.files[key] = path.join(DATA_DIR, `${key}.json`);
      this.data[key] = this._load(key);
    }
  }

  _load(key) {
    const file = this.files[key];
    if (!fs.existsSync(file)) return [];
    try {
      const parsed = JSON.parse(fs.readFileSync(file, 'utf8'));
      return Array.isArray(parsed) ? parsed : [];
    } catch (err) {
      console.error(`[store] 无法解析 ${file}，已重置为空集合: ${err.message}`);
      return [];
    }
  }

  _save(key) {
    const file = this.files[key];
    const tmp = `${file}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify(this.data[key], null, 2), 'utf8');
    fs.renameSync(tmp, file);
  }

  all(key) {
    return this.data[key];
  }

  find(key, predicate) {
    return this.data[key].find(predicate) || null;
  }

  filter(key, predicate) {
    return this.data[key].filter(predicate);
  }

  getById(key, id) {
    return this.find(key, (r) => r.id === id);
  }

  insert(key, record) {
    this.data[key].push(record);
    this._save(key);
    return record;
  }

  update(key, id, patch) {
    const idx = this.data[key].findIndex((r) => r.id === id);
    if (idx === -1) return null;
    this.data[key][idx] = { ...this.data[key][idx], ...patch, id };
    this._save(key);
    return this.data[key][idx];
  }

  remove(key, id) {
    const idx = this.data[key].findIndex((r) => r.id === id);
    if (idx === -1) return false;
    this.data[key].splice(idx, 1);
    this._save(key);
    return true;
  }
}

module.exports = { JsonStore, DATA_DIR };
