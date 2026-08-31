'use strict';

/**
 * 纯 Node 零依赖 PDF 文本提取器（尽力而为）
 * - 支持 FlateDecode / ASCIIHexDecode / ASCII85Decode 流
 * - 解析内容流中的 Tj / TJ / ' / " 文本操作符
 * - 通过 ToUnicode CMap 还原中文等字符编码
 * - 扫描版（图片型）PDF 无法提取文本，会返回空字符串
 */

const zlib = require('zlib');

// ---------- 小工具 ----------

function inflate(buf) {
  try { return zlib.inflateSync(buf); } catch { /* 部分 PDF 用 raw deflate */ }
  try { return zlib.inflateRawSync(buf); } catch { return buf; }
}

function decodeAsciiHex(buf) {
  let hex = buf.toString('ascii').replace(/[^0-9a-fA-F]/g, '');
  if (hex.length % 2) hex += '0';
  return Buffer.from(hex, 'hex');
}

function decodeAscii85(buf) {
  const s = buf.toString('ascii').replace(/\s/g, '').replace(/~>?$/, '');
  const out = [];
  let i = 0;
  while (i < s.length) {
    if (s[i] === 'z') { out.push(0, 0, 0, 0); i++; continue; }
    const group = s.slice(i, i + 5);
    i += 5;
    let value = 0;
    for (const ch of group) value = value * 85 + (ch.charCodeAt(0) - 33);
    out.push((value >> 24) & 0xff, (value >> 16) & 0xff, (value >> 8) & 0xff, value & 0xff);
  }
  return Buffer.from(out);
}

function decodeStream(stream, filter) {
  if (!filter) return stream;
  const name = String(filter).replace(/^\//, '');
  if (name === 'FlateDecode' || name === 'Fl') return inflate(stream);
  if (name === 'ASCIIHexDecode' || name === 'AHx') return decodeAsciiHex(stream);
  if (name === 'ASCII85Decode' || name === 'A85') return decodeAscii85(stream);
  return stream; // 其它过滤器（如 LZW）暂不支持
}

// ---------- 对象与字典解析 ----------

function tokenizeObj(body) {
  const tokens = [];
  let i = 0;
  const n = body.length;
  while (i < n) {
    const ch = body[i];
    if (/\s/.test(ch) || ch === '%') {
      if (ch === '%') { while (i < n && body[i] !== '\n') i++; }
      i++;
      continue;
    }
    if (ch === '(') {
      let depth = 0, out = '', j = i + 1;
      while (j < n) {
        const c = body[j];
        if (c === '\\') {
          const nx = body[j + 1];
          const m = { n: '\n', r: '\r', t: '\t', b: '\b', f: '\f', '(': '(', ')': ')', '\\': '\\' };
          if (nx in m) out += m[nx];
          else if (nx >= '0' && nx <= '7') {
            let oct = '', k = 0;
            while (k < 3 && body[j + 1 + k] >= '0' && body[j + 1 + k] <= '7') { oct += body[j + 1 + k]; k++; }
            out += String.fromCharCode(parseInt(oct, 8));
            j += k;
          } else out += nx || '';
          j += 2;
        } else if (c === '(') { depth++; out += c; j++; }
        else if (c === ')') { if (depth === 0) { j++; break; } depth--; out += c; j++; }
        else { out += c; j++; }
      }
      tokens.push({ type: 'string', value: out });
      i = j;
      continue;
    }
    if (ch === '<') {
      if (body[i + 1] === '<') {
        let j = i + 2;
        while (j < n && !(body[j] === '>' && body[j + 1] === '>')) j++;
        const inner = body.slice(i + 2, j).trim();
        tokens.push({ type: 'dict', value: inner });
        i = j + 2;
        continue;
      }
      let j = i + 1, hex = '';
      while (j < n && body[j] !== '>') { hex += body[j]; j++; }
      tokens.push({ type: 'hexstring', value: hex.replace(/\s/g, '') });
      i = j + 1;
      continue;
    }
    if (ch === '[') {
      let j = i + 1, depth = 1;
      while (j < n && depth) {
        if (body[j] === '[') depth++;
        if (body[j] === ']') depth--;
        j++;
      }
      tokens.push({ type: 'array', value: body.slice(i + 1, j - 1).trim() });
      i = j;
      continue;
    }
    if (ch === '/') {
      let name = '', j = i + 1;
      while (j < n && !/\s|[()<>[\]{}/%]/.test(body[j])) { name += body[j]; j++; }
      tokens.push({ type: 'name', value: name });
      i = j;
      continue;
    }
    let word = '', j = i;
    while (j < n && !/\s|[()<>[\]{}/%]/.test(body[j])) { word += body[j]; j++; }
    if (j === i) { i++; continue; } // 跳过无法识别的字符，防止死循环
    tokens.push({ type: /^-?\d+(\.\d+)?$/.test(word) ? 'number' : 'word', value: word });
    i = j;
  }
  return tokens;
}

function parseDict(dictText) {
  let t = String(dictText || '').trim();
  if (t.startsWith('<<')) t = t.slice(2);
  if (t.endsWith('>>')) t = t.slice(0, -2);
  const tokens = tokenizeObj(t);
  const dict = {};
  let i = 0;
  const n = tokens.length;
  while (i < n) {
    if (tokens[i].type !== 'name') { i++; continue; }
    const key = tokens[i].value;
    i++;
    if (i >= n) break;
    const v = tokens[i];
    if (v.type === 'name') { dict[key] = '/' + v.value; i++; }
    else if (v.type === 'number') {
      if (i + 2 < n && tokens[i + 1].type === 'number' && tokens[i + 2].type === 'word' && tokens[i + 2].value === 'R') {
        dict[key] = `${v.value} ${tokens[i + 1].value} R`;
        i += 3;
      } else { dict[key] = v.value; i++; }
    }
    else if (v.type === 'word') { dict[key] = v.value; i++; }
    else if (v.type === 'dict') { dict[key] = v.value; i++; }
    else if (v.type === 'array') { dict[key] = v.value; i++; }
    else if (v.type === 'string' || v.type === 'hexstring') { dict[key] = v.value; i++; }
    else { i++; }
  }
  return dict;
}

function parsePdf(buffer) {
  const text = buffer.toString('binary');
  const objects = new Map();
  const objRe = /(\d+)\s+\d+\s+obj([\s\S]*?)endobj/g;
  let m;
  while ((m = objRe.exec(text))) {
    const id = m[1];
    const body = m[2];
    const streamMatch = body.match(/stream\r?\n([\s\S]*?)endstream/);
    const head = streamMatch ? body.slice(0, body.indexOf('stream')) : body;
    const dict = parseDict(head);
    const stream = streamMatch ? streamMatch[1] : null;
    objects.set(id, { id, dict, stream });
  }
  return objects;
}

function resolveRef(value, objects) {
  const ref = /^(\d+)\s+0\s+R$/.exec(String(value || '').trim());
  if (!ref) return null;
  return objects.get(ref[1]) || null;
}

function resolveMaybeDict(value, objects) {
  const v = String(value || '').trim();
  if (/^(\d+)\s+\d+\s+R$/.test(v)) return resolveRef(v, objects);
  if (v.includes('/')) return { dict: parseDict(v) };
  return null;
}
function getFilter(dict) {
  const f = dict.Filter;
  if (!f) return null;
  const t = String(f).trim();
  if (t.startsWith('[')) {
    const m = t.match(/\/([A-Za-z0-9]+)/g);
    return m ? m[m.length - 1] : null;
  }
  return t;
}

function readStream(obj, objects) {
  if (!obj || obj.stream == null) return null;
  let data = Buffer.from(obj.stream, 'binary');
  const filter = getFilter(obj.dict);
  return decodeStream(data, filter);
}

// ---------- ToUnicode CMap ----------

function hexToUnicode(hex) {
  hex = hex.length % 2 ? '0' + hex : hex;
  const buf = Buffer.from(hex, 'hex');
  if (buf.length === 1) return String.fromCharCode(buf[0]); // 单字节按 Latin-1
  let s = '';
  for (let i = 0; i + 1 < buf.length; i += 2) s += String.fromCharCode((buf[i] << 8) | buf[i + 1]); // UTF-16BE
  return s;
}

function parseCmap(data) {
  const map = {};
  const text = data.toString('binary');
  let m;
  const charRe = /beginbfchar([\s\S]*?)endbfchar/g;
  while ((m = charRe.exec(text))) {
    const pairRe = /<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>/g;
    let p;
    while ((p = pairRe.exec(m[1]))) {
      map[parseInt(p[1], 16)] = hexToUnicode(p[2]);
    }
  }
  const rangeRe = /beginbfrange([\s\S]*?)endbfrange/g;
  while ((m = rangeRe.exec(text))) {
    const body = m[1];
    // <lo> <hi> <dstStart>
    let p;
    const r1 = /<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>/g;
    while ((p = r1.exec(body))) {
      const lo = parseInt(p[1], 16);
      const hi = parseInt(p[2], 16);
      const start = parseInt(p[3], 16);
      for (let c = lo; c <= hi; c++) map[c] = hexToUnicode((start + (c - lo)).toString(16));
    }
    // <lo> <hi> [<d1> <d2> ...]
    const r2 = /<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*\[([\s\S]*?)\]/g;
    while ((p = r2.exec(body))) {
      const lo = parseInt(p[1], 16);
      const dstRe = /<([0-9A-Fa-f]+)>/g;
      const dsts = [];
      let d;
      while ((d = dstRe.exec(p[3]))) dsts.push(hexToUnicode(d[1]));
      dsts.forEach((u, k) => { map[lo + k] = u; });
    }
  }
  return map;
}

// ---------- 内容流文本提取 ----------

function tokenizeContent(stream) {
  const tokens = [];
  let i = 0;
  const n = stream.length;
  while (i < n) {
    const ch = stream[i];
    if (/\s/.test(ch)) { i++; continue; }
    if (ch === '%') { while (i < n && stream[i] !== '\n') i++; continue; }
    if (ch === '(') {
      let depth = 0, out = '', j = i + 1;
      while (j < n) {
        const c = stream[j];
        if (c === '\\') {
          const nx = stream[j + 1];
          const m = { n: '\n', r: '\r', t: '\t', b: '\b', f: '\f', '(': '(', ')': ')', '\\': '\\' };
          if (nx in m) out += m[nx];
          else if (nx >= '0' && nx <= '7') {
            let oct = '', k = 0;
            while (k < 3 && stream[j + 1 + k] >= '0' && stream[j + 1 + k] <= '7') { oct += stream[j + 1 + k]; k++; }
            out += String.fromCharCode(parseInt(oct, 8));
            j += k;
          } else out += nx || '';
          j += 2;
        } else if (c === '(') { depth++; out += c; j++; }
        else if (c === ')') { if (depth === 0) { j++; break; } depth--; out += c; j++; }
        else { out += c; j++; }
      }
      tokens.push({ type: 'string', value: out });
      i = j;
      continue;
    }
    if (ch === '<') {
      if (stream[i + 1] === '<') {
        let j = i + 2;
        while (j < n && !(stream[j] === '>' && stream[j + 1] === '>')) j++;
        i = j + 2;
        tokens.push({ type: 'dict' });
        continue;
      }
      let j = i + 1, hex = '';
      while (j < n && stream[j] !== '>') { hex += stream[j]; j++; }
      i = j + 1;
      hex = hex.replace(/\s/g, '');
      if (hex.length % 2) hex += '0';
      tokens.push({ type: 'string', value: Buffer.from(hex, 'hex').toString('binary') });
      continue;
    }
    if (ch === '[') {
      const arr = [];
      let j = i + 1;
      while (j < n && stream[j] !== ']') {
        if (stream[j] === '(') {
          let depth = 0, out = '', k = j + 1;
          while (k < n) {
            const c = stream[k];
            if (c === '\\') { out += stream[k + 1] || ''; k += 2; continue; }
            if (c === '(') depth++;
            if (c === ')') { if (depth === 0) { k++; break; } depth--; }
            out += c;
            k++;
          }
          arr.push({ type: 'string', value: out });
          j = k;
          continue;
        }
        if (stream[j] === '<') {
          let k = j + 1, hex = '';
          while (k < n && stream[k] !== '>') { hex += stream[k]; k++; }
          hex = hex.replace(/\s/g, '');
          if (hex.length % 2) hex += '0';
          arr.push({ type: 'string', value: Buffer.from(hex, 'hex').toString('binary') });
          j = k + 1;
          continue;
        }
        j++;
      }
      tokens.push({ type: 'array', value: arr });
      i = j + 1;
      continue;
    }
    if (ch === '/') {
      let name = '', j = i + 1;
      while (j < n && !/\s|[()<>[\]{}/%]/.test(stream[j])) { name += stream[j]; j++; }
      tokens.push({ type: 'name', value: name });
      i = j;
      continue;
    }
    let word = '', j = i;
    while (j < n && !/\s|[()<>[\]{}/%]/.test(stream[j])) { word += stream[j]; j++; }
    if (j === i) { i++; continue; } // 跳过无法识别的字符，防止死循环
    tokens.push({ type: /^-?\d+(\.\d+)?$/.test(word) ? 'number' : 'keyword', value: word });
    i = j;
  }
  return tokens;
}

function decodeString(raw, fontName, fonts) {
  const cmap = fonts[fontName] && fonts[fontName].toUnicode;
  if (cmap && Object.keys(cmap).length) {
    const twoByte = (fonts[fontName] && fonts[fontName].twoByte) || Object.keys(cmap).some((k) => Number(k) > 0xff);
    let out = '';
    if (twoByte) {
      const buf = Buffer.from(raw, 'binary');
      for (let i = 0; i + 1 < buf.length; i += 2) {
        const code = (buf[i] << 8) | buf[i + 1];
        out += cmap[code] !== undefined ? cmap[code] : '';
      }
    } else {
      for (const ch of raw) out += cmap[ch.charCodeAt(0)] !== undefined ? cmap[ch.charCodeAt(0)] : '';
    }
    return out;
  }
  const buf = Buffer.from(raw, 'binary');
  try {
    const s = new TextDecoder('utf-8', { fatal: true }).decode(buf);
    if (!s.includes('\uFFFD')) return s;
  } catch { /* 非 UTF-8 */ }
  return raw;
}

function extractFromContent(content, fonts) {
  const tokens = tokenizeContent(content.toString('binary'));
  let text = '';
  let pending = [];
  let currentFont = null;
  for (let i = 0; i < tokens.length; i++) {
    const t = tokens[i];
    if (t.type === 'string') { pending.push(t.value); continue; }
    if (t.type === 'array') { pending = t.value.filter((x) => x.type === 'string').map((x) => x.value); continue; }
    if (t.type === 'keyword') {
      if (t.value === 'Tf') {
        currentFont = i >= 2 && tokens[i - 2].type === 'name' ? tokens[i - 2].value : currentFont;
        pending = [];
        continue;
      }
      if (t.value === 'Tj' || t.value === "'" || t.value === '"' || t.value === 'TJ') {
        text += pending.map((s) => decodeString(s, currentFont, fonts)).join('');
        pending = [];
        continue;
      }
      if (t.value === 'Td' || t.value === 'TD' || t.value === 'T*' || t.value === 'Tm') {
        text += '\n';
        pending = [];
        continue;
      }
      pending = [];
    }
  }
  return text;
}

// ---------- 主入口 ----------

/**
 * 提取 PDF 文本
 * @param {Buffer} buffer
 * @returns {string} 提取到的文本（可能为空）
 */
function extractPdfText(buffer) {
  const objects = parsePdf(buffer);
  if (!objects.size) return '';

  // 找根（catalog）
  let catalog = null;
  for (const obj of objects.values()) {
    if (obj.dict.Type === '/Catalog') { catalog = obj; break; }
  }
  if (!catalog) return '';

  // 遍历页面树
  const pages = [];
  const walk = (node) => {
    if (!node) return;
    const type = node.dict.Type;
    if (type === '/Page') { pages.push(node); return; }
    const kids = node.dict.Kids;
    if (!kids) return;
    const refs = kids.match(/\d+\s+\d+\s+R/g) || [];
    for (const ref of refs) walk(resolveRef(ref, objects));
  };
  walk(resolveRef(catalog.dict.Pages, objects));

  const allText = [];
  for (const page of pages) {
    const fonts = {};
    // 字体资源（可能是引用或内联字典）
    const resources = resolveMaybeDict(page.dict.Resources, objects);
    const fontDict = resources && resources.dict.Font ? String(resources.dict.Font) : null;
    if (fontDict) {
      const fontEntries = String(fontDict).match(/(\w+)\s+\d+\s+\d+\s+R/g) || [];
      for (const entry of fontEntries) {
        const m = /^(\w+)\s+(\d+)\s+\d+\s+R$/.exec(entry);
        if (!m) continue;
        const fontObj = resolveRef(`${m[2]} 0 R`, objects);
        if (!fontObj) continue;
        fonts[m[1]] = { toUnicode: null, twoByte: fontObj.dict.Subtype === '/Type0' || fontObj.dict.Encoding === '/Identity-H' };
        const tuRef = fontObj.dict.ToUnicode;
        if (tuRef) {
          const tuObj = resolveRef(tuRef, objects);
          const data = readStream(tuObj, objects);
          if (data) fonts[m[1]].toUnicode = parseCmap(data);
        }
      }
    }

    // 内容流
    const contents = page.dict.Contents;
    let streams = [];
    if (contents) {
      if (String(contents).trim().startsWith('[')) {
        const refs = contents.match(/\d+\s+\d+\s+R/g) || [];
        for (const ref of refs) {
          const s = readStream(resolveRef(ref, objects), objects);
          if (s) streams.push(s);
        }
      } else {
        const s = readStream(resolveRef(contents, objects), objects);
        if (s) streams.push(s);
      }
    }
    let pageText = streams.map((s) => extractFromContent(s, fonts)).join('\n');
    pageText = pageText.replace(/\n{2,}/g, '\n').replace(/^\n+|\n+$/g, '');
    if (pageText) allText.push(pageText);
  }
  return allText.join('\n');
}

module.exports = { extractPdfText, parsePdf, tokenizeContent, parseDict, tokenizeObj };





