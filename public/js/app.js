'use strict';

/* ---------- 全局状态 ---------- */
const state = {
  user: null,          // 当前登录用户
  chatEnabled: false,  // AI 服务是否已配置
  model: '',           // 当前使用的模型
  messages: [],        // 对话历史 [{role, content}]
  streaming: false,    // 是否正在生成回复
  prefs: [],           // 当前用户保存的偏好
  activeView: 'chat',  // 当前视图（chat / admin）
  conversationId: null, // 当前会话 id（服务端自动保存）
  conversations: [],   // 历史会话列表
  editingPrefId: null, // 正在编辑的偏好 id
  favorites: [],      // 收藏的旅行计划
};

const CATEGORIES = ['美食', '预算', '交通', '住宿', '目的地', '游玩', '其他'];

/* ---------- DOM 工具 ---------- */
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

function showToast(message, type = 'info') {
  const toast = $('#toast');
  toast.textContent = message;
  toast.className = 'toast' + (type === 'error' ? ' error' : '');
  toast.hidden = false;
  clearTimeout(showToast._timer);
  showToast._timer = setTimeout(() => { toast.hidden = true; }, 2600);
}

function setHint(el, message, ok = false) {
  el.textContent = message || '';
  el.className = 'hint' + (ok ? ' ok' : '');
}

function esc(str) {
  return String(str ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

function fmtDateTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleString('zh-CN', { hour12: false });
}

/* ---------- API 封装（多账号：每个标签页独立令牌） ---------- */
let sessionToken = sessionStorage.getItem('tp_token') || '';

function saveSession(token) {
  sessionToken = token || '';
  if (token) sessionStorage.setItem('tp_token', token);
  else sessionStorage.removeItem('tp_token');
}

async function api(method, url, body) {
  const options = { method, headers: {} };
  if (sessionToken) options.headers['X-Session-Token'] = sessionToken;
  if (body !== undefined) {
    options.headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(body);
  }
  const res = await fetch(url, options);
  let data = null;
  try { data = await res.json(); } catch { /* ignore */ }
  if (!res.ok) {
    const err = new Error((data && data.error) || `请求失败 (${res.status})`);
    err.status = res.status;
    throw err;
  }
  return data;
}

/* ---------- 视图切换 ---------- */
const AVATARS = ['🧑‍✈️', '🧕', '👨‍💻', '👩‍🎨', '🧑‍🌾', '🏄', '🧗', '🚴', '🧳', '🎒', '🦁', '🐼', '🐧', '🦊', '🌸', '🌊'];

function showView(name) {
  state.activeView = name;
  ['auth', 'chat', 'admin', 'profile'].forEach((v) => {
    const el = $(`#view-${v}`);
    if (el) el.hidden = v !== name;
  });
  const nav = $('#topnav');
  if (!state.user) {
    nav.innerHTML = '';
    return;
  }
  const roleLabel = state.user.role === 'admin' ? '管理员' : '用户';
  const tabs = `
    <button class="btn small ${name === 'chat' ? 'active' : ''}" data-view="chat">💬 智能对话</button>
    <button class="btn small ${name === 'profile' ? 'active' : ''}" data-view="profile">👤 个人中心</button>
    ${state.user.role === 'admin' ? `<button class="btn small ${name === 'admin' ? 'active' : ''}" data-view="admin">👥 用户管理</button>` : ''}`;
  nav.innerHTML = `
    ${tabs}
    <span class="user-chip">${esc(state.user.avatar || '🧑')} ${esc(state.user.nickname || state.user.username)}（${roleLabel}）</span>
    <button class="btn small" id="nav-logout">退出登录</button>`;
  nav.querySelectorAll('[data-view]').forEach((btn) => {
    btn.addEventListener('click', () => switchView(btn.dataset.view));
  });
  $('#nav-logout').addEventListener('click', logout);
}

async function switchView(name) {
  showView(name);
  if (name === 'chat') {
    await loadPrefs();
  } else if (name === 'profile') {
    await loadProfile();
  } else if (name === 'admin') {
    await loadAdminUsers();
  }
}

function renderNav() {
  if (!state.user) return showView('auth');
  showView(state.activeView || 'chat');
}

/* ---------- 认证 ---------- */
let authMode = 'login';

function initAuth() {
  $$('#auth-tabs .tab').forEach((tab) => {
    tab.addEventListener('click', () => {
      authMode = tab.dataset.mode;
      $$('#auth-tabs .tab').forEach((t) => t.classList.toggle('active', t === tab));
      $('#email-field').hidden = authMode !== 'register';
      $('#auth-submit').textContent = authMode === 'login' ? '登录' : '注册';
      setHint($('#auth-hint'), '');
    });
  });

  $('#auth-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const username = $('#username').value.trim();
    const password = $('#password').value;
    const email = $('#email').value.trim();
    const hint = $('#auth-hint');
    const btn = $('#auth-submit');
    btn.disabled = true;
    try {
      if (authMode === 'login') {
        const data = await api('POST', '/api/auth/login', { username, password });
        state.user = data.user;
        saveSession(data.token);
        showToast(`欢迎回来，${data.user.username}！`);
      } else {
        const data = await api('POST', '/api/auth/register', { username, password, email });
        state.user = data.user;
        saveSession(data.token);
        showToast(`注册成功，欢迎 ${data.user.username}！`);
      }
      $('#auth-form').reset();
      setHint(hint, '');
      state.activeView = 'chat';
      state.conversationId = null;
      state.conversations = [];
      await boot();
    } catch (err) {
      setHint(hint, err.message);
    } finally {
      btn.disabled = false;
    }
  });
}

function resetChatView() {
  const body = $('#chat-body');
  if (body) body.innerHTML = '';
  addWelcome();
  state.messages = [];
  state.conversationId = null;
  state.conversations = [];
  scrollChatToBottom();
}

async function logout() {
  try { await api('POST', '/api/auth/logout'); } catch { /* ignore */ }
  state.user = null;
  saveSession('');
  state.messages = [];
  state.prefs = [];
  state.guides = [];
  state.favorites = [];
  state.conversationId = null;
  state.conversations = [];
  state.editingPrefId = null;
  lastWeather = null;
  state.activeView = 'chat';
  closeHistory();
  closeWeather();
  const body = $('#chat-body');
  if (body) body.innerHTML = '';
  renderNav();
  showToast('已退出登录');
}

/* ---------- AI 状态横幅 ---------- */
async function loadConfig() {
  try {
    const data = await api('GET', '/api/config');
    state.chatEnabled = data.chatEnabled;
    state.model = data.model || '';
    const banner = $('#ai-banner');
    const text = $('#ai-banner-text');
    banner.hidden = false;
    if (data.chatEnabled) {
      banner.className = 'ai-banner ok';
      text.textContent = `✅ AI 服务已启用（模型：${data.model}），开始对话吧！`;
    } else {
      banner.className = 'ai-banner';
      text.textContent = '⚠️ AI 服务未配置：请设置环境变量 OPENAI_API_KEY 后重启服务（可配合 OPENAI_BASE_URL / TP_OPENAI_MODEL）。';
    }
  } catch { /* 忽略 */ }
}

/* ---------- 偏好（向量数据库） ---------- */
async function loadPrefs() {
  try {
    const data = await api('GET', '/api/preferences');
    state.prefs = data.preferences;
    renderPrefs();
  } catch (err) {
    showToast(err.message, 'error');
  }
}

function starStr(w) {
  w = Math.max(1, Math.min(5, Number(w) || 3));
  return '★'.repeat(w) + '☆'.repeat(5 - w);
}

function renderPrefs() {
  const list = $('#pref-list');
  if (!list) return;
  if (!state.prefs.length) {
    list.innerHTML = '<div class="pref-empty">还没有偏好，先添加一条试试 ✨</div>';
    return;
  }
  list.innerHTML = state.prefs.map((p) => {
    if (state.editingPrefId === p.id) {
      return `
        <div class="pref-item editing" data-id="${p.id}">
          <div class="pref-edit-form">
            <select data-edit="category">${CATEGORIES.map((c) => `<option value="${c}" ${c === p.category ? 'selected' : ''}>${esc(c)}</option>`).join('')}</select>
            <select data-edit="weight">${[1, 2, 3, 4, 5].map((w) => `<option value="${w}" ${Number(p.weight) === w ? 'selected' : ''}>${starStr(w)}</option>`).join('')}</select>
            <button class="btn small primary" data-action="save-edit">保存</button>
            <button class="btn small" data-action="cancel-edit">取消</button>
          </div>
        </div>`;
    }
    return `
      <div class="pref-item" data-id="${p.id}">
        <div class="pref-main">
          <span class="pref-cat">${esc(p.category || '其他')}</span>
          <span class="text">${esc(p.text)}</span>
          <span class="pref-weight" title="重要度 ${p.weight}/5">${starStr(p.weight)}</span>
        </div>
        <div class="pref-ops">
          <button class="mini" title="编辑分类/重要度" data-action="edit">✏️</button>
          <button class="del" title="删除" data-action="del">×</button>
        </div>
      </div>`;
  }).join('');
}

function initPrefs() {
  $('#pref-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const input = $('#pref-input');
    const text = input.value.trim();
    if (!text) return;
    const hint = $('#pref-hint');
    const btn = e.target.querySelector('button[type=submit]');
    btn.disabled = true;
    try {
      await api('POST', '/api/preferences', {
        text,
        category: $('#pref-category').value,
        weight: Number($('#pref-weight').value),
      });
      input.value = '';
      setHint(hint, '已保存，对话时 AI 将按分类与重要度检索参考', true);
      await loadPrefs();
    } catch (err) {
      setHint(hint, err.message);
    } finally {
      btn.disabled = false;
    }
  });
  $('#pref-list').addEventListener('click', async (e) => {
    const btn = e.target.closest('button[data-action]');
    if (!btn) return;
    const item = btn.closest('.pref-item');
    if (!item) return;
    const id = item.dataset.id;
    const action = btn.dataset.action;
    try {
      if (action === 'del') {
        await api('DELETE', `/api/preferences/${id}`);
        await loadPrefs();
      } else if (action === 'edit') {
        state.editingPrefId = id;
        renderPrefs();
      } else if (action === 'cancel-edit') {
        state.editingPrefId = null;
        renderPrefs();
      } else if (action === 'save-edit') {
        const cat = item.querySelector('[data-edit="category"]').value;
        const w = Number(item.querySelector('[data-edit="weight"]').value);
        await api('PATCH', `/api/preferences/${id}`, { category: cat, weight: w });
        state.editingPrefId = null;
        showToast('已更新偏好');
        await loadPrefs();
      }
    } catch (err) {
      showToast(err.message, 'error');
    }
  });

  // 文件上传（PDF / TXT）读取并提取偏好
  const fileInput = $('#pref-file');
  $('#btn-upload-pref').addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const status = $('#pref-upload-status');
    const btn = $('#btn-upload-pref');
    btn.disabled = true;
    status.textContent = '正在读取并提取偏好…';
    try {
      if (file.size > 3 * 1024 * 1024) throw new Error('文件不能超过 3MB');
      const buf = new Uint8Array(await file.arrayBuffer());
      status.textContent = '正在交给 AI 提取…';
      const data = await api('POST', '/api/preferences/import', {
        filename: file.name,
        contentBase64: bufToBase64(buf),
      });
      status.textContent = '';
      const tip = data.method === 'chunk'
        ? '（AI 智能提取暂不可用，已按段落提取，不影响使用）'
        : '（AI 智能提取）';
      showToast(`已提取 ${data.total} 条偏好 ${tip}`);
      await loadPrefs();
    } catch (err) {
      status.textContent = '';
      showToast(err.message, 'error');
    } finally {
      btn.disabled = false;
      fileInput.value = '';
    }
  });
}

function bufToBase64(buf) {
  let binary = '';
  const chunkSize = 0x8000;
  for (let i = 0; i < buf.length; i += chunkSize) {
    binary += String.fromCharCode.apply(null, buf.subarray(i, i + chunkSize));
  }
  return btoa(binary);
}

/* ---------- Markdown 轻量渲染 ---------- */
function inlineMd(s) {
  return s
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>');
}

function renderMd(text) {
  const lines = esc(text).split('\n');
  let html = '';
  let listType = null;
  let tableRows = [];
  const sepRe = /^:?-{2,}:?$/;
  const closeList = () => {
    if (listType) { html += `</${listType}>`; listType = null; }
  };
  const flushTable = () => {
    if (!tableRows.length) return;
    const firstIsSep = sepRe.test((tableRows[0][0] || '').trim().replace(/\s/g, ''));
    const header = firstIsSep ? [] : tableRows[0];
    const body = tableRows.slice(1);
    html += '<div class="md-table-wrap"><table>';
    if (header.length) {
      html += '<thead><tr>' + header.map((cell) => `<th>${inlineMd(cell.trim())}</th>`).join('') + '</tr></thead>';
    }
    html += '<tbody>' + body.map((row) => '<tr>' + row.map((cell) => `<td>${inlineMd(cell.trim())}</td>`).join('') + '</tr>').join('') + '</tbody>';
    html += '</table></div>';
    tableRows = [];
  };
  for (const raw of lines) {
    const line = raw.trimEnd();
    const trimmed = line.trim();
    if (trimmed.startsWith('|') && trimmed.endsWith('|')) {
      closeList();
      const cells = trimmed.slice(1, -1).split('|').map((s) => s.trim());
      if (cells.every((s) => sepRe.test(s.replace(/\s/g, '')))) continue; // 分隔行
      tableRows.push(cells);
      continue;
    }
    flushTable();
    const h = line.match(/^(#{1,4})\s+(.*)$/);
    const ul = line.match(/^[-*]\s+(.*)$/);
    const ol = line.match(/^\d+[.)]\s+(.*)$/);
    if (h) {
      closeList();
      html += `<h${h[1].length}>${inlineMd(h[2])}</h${h[1].length}>`;
    } else if (ul) {
      if (listType !== 'ul') { closeList(); html += '<ul>'; listType = 'ul'; }
      html += `<li>${inlineMd(ul[1])}</li>`;
    } else if (ol) {
      if (listType !== 'ol') { closeList(); html += '<ol>'; listType = 'ol'; }
      html += `<li>${inlineMd(ol[1])}</li>`;
    } else if (!trimmed) {
      closeList();
    } else {
      closeList();
      html += `<p>${inlineMd(line)}</p>`;
    }
  }
  flushTable();
  closeList();
  return html;
}

/* ---------- 聊天界面 ---------- */
const SUGGESTIONS = [
  '帮我规划 5 天 4 晚云南大理之旅，预算 5000，从上海出发',
  '北京 3 日游攻略，含美食、交通和住宿',
  '适合带父母去的国内景点推荐',
  '暑假带孩子亲子游，预算 8000，求推荐',
  '穷游成都 4 天，怎么安排最划算',
];

function scrollChatToBottom() {
  const body = $('#chat-body');
  body.scrollTop = body.scrollHeight;
}

function addWelcome() {
  $('#chat-body').insertAdjacentHTML('beforeend', `
    <div class="chat-welcome" id="chat-welcome">
      <span class="welcome-emoji">🧳</span>
      <p class="welcome-title">你好，我是你的 AI 旅行规划师 👋</p>
      <p class="muted small">告诉我你的旅行需求，我会参考你保存的偏好，为你定制多方位旅行计划。</p>
      <div class="features">
        <span>📍 目的地</span><span>🗓 行程</span><span>🚄 交通</span>
        <span>🏨 住宿</span><span>🍜 美食</span><span>💰 预算</span><span>⚠️ 注意事项</span>
      </div>
    </div>`);
}

function renderMessage(role, content, msgId) {
  if (role === 'user') {
    $('#chat-body').insertAdjacentHTML('beforeend', `
      <div class="msg user" data-msg="${msgId || ''}">
        <div class="avatar">🙂</div>
        <div class="bubble">${esc(content)}</div>
      </div>`);
  } else {
    $('#chat-body').insertAdjacentHTML('beforeend', `
      <div class="msg assistant" data-msg="${msgId || ''}">
        <div class="avatar">🤖</div>
        <div class="msg-col">
          <div class="bubble md">${renderMd(content)}</div>
          <div class="msg-actions">
            <button class="mini" data-act="copy" title="复制">📋</button>
            <button class="mini" data-act="export" title="导出">⬇️</button>
            <button class="mini" data-act="fav" title="收藏到灵感库">⭐</button>
          </div>
        </div>
      </div>`);
  }
}

function renderTyping() {
  $('#chat-body').insertAdjacentHTML('beforeend', `
    <div class="msg assistant" id="typing-msg">
      <div class="avatar">🤖</div>
      <div class="bubble"><span class="typing"><span></span><span></span><span></span></span></div>
    </div>`);
  scrollChatToBottom();
}

function removeTyping() {
  const el = $('#typing-msg');
  if (el) el.remove();
}

function renderSuggestions() {
  const box = $('#chat-suggestions');
  box.innerHTML = SUGGESTIONS.map((s) => `<button type="button" class="chip" data-prompt="${esc(s)}">${esc(s)}</button>`).join('');
}

function showChatError(message) {
  removeTyping();
  $('#chat-body').insertAdjacentHTML('beforeend', `
    <div class="msg error">
      <div class="avatar">⚠️</div>
      <div class="bubble">${esc(message)}</div>
    </div>`);
  scrollChatToBottom();
}

async function sendChat() {
  if (state.streaming) return;
  const input = $('#chat-input');
  const text = input.value.trim();
  if (!text) return;
  if (!state.chatEnabled) {
    showToast('AI 服务未配置，请先设置 OPENAI_API_KEY', 'error');
    return;
  }

  state.messages.push({ role: 'user', content: text });
  input.value = '';
  input.style.height = 'auto';
  renderMessage('user', text);
  scrollChatToBottom();
  renderTyping();
  state.streaming = true;
  $('#chat-send').disabled = true;

  let assistantText = '';

  try {
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ messages: state.messages, conversationId: state.conversationId }),
    });

    if (!res.ok) {
      let msg = `请求失败 (${res.status})`;
      try { const data = await res.json(); if (data.error) msg = data.error; } catch { /* ignore */ }
      throw new Error(msg);
    }
    if (!res.body) throw new Error('浏览器不支持流式响应');

    removeTyping();
    const bubbleId = 'msg-' + Date.now();
    $('#chat-body').insertAdjacentHTML('beforeend', `
      <div class="msg assistant" data-msg="${bubbleId}">
        <div class="avatar">🤖</div>
        <div class="msg-col">
          <div class="bubble md"></div>
          <div class="msg-actions">
            <button class="mini" data-act="copy" title="复制">📋</button>
            <button class="mini" data-act="export" title="导出">⬇️</button>
            <button class="mini" data-act="fav" title="收藏到灵感库">⭐</button>
          </div>
        </div>
      </div>`);
    scrollChatToBottom();

    const reader = res.body.getReader();
    const decoder = new TextDecoder('utf-8');
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buffer.indexOf('\n\n')) !== -1) {
        const block = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        for (const line of block.split('\n')) {
          if (!line.startsWith('data:')) continue;
          const raw = line.slice(5).trim();
          if (!raw) continue;
          let evt;
          try { evt = JSON.parse(raw); } catch { continue; }
          if (evt.type === 'delta') {
            assistantText += evt.content;
            const bubble = document.querySelector(`.msg[data-msg="${bubbleId}"] .bubble`);
            if (bubble) bubble.innerHTML = renderMd(assistantText);
            scrollChatToBottom();
          } else if (evt.type === 'tool') {
            $('#chat-body').insertAdjacentHTML('beforeend', `<div class="tool-chip">🔧 ${esc(evt.summary)}</div>`);
            scrollChatToBottom();
          } else if (evt.type === 'done') {
            if (evt.conversationId) state.conversationId = evt.conversationId;
          } else if (evt.type === 'error') {
            throw new Error(evt.message || '对话失败');
          }
        }
      }
    }

    if (assistantText) {
      state.messages.push({ role: 'assistant', content: assistantText });
    } else {
      showChatError('AI 没有返回内容，请重试');
      state.messages.pop();
    }
  } catch (err) {
    removeTyping();
    showChatError(err.message);
    state.messages.pop();
  } finally {
    state.streaming = false;
    $('#chat-send').disabled = false;
    scrollChatToBottom();
  }
}

function initChat() {
  renderSuggestions();

  $('#chat-form').addEventListener('submit', (e) => {
    e.preventDefault();
    sendChat();
  });

  const input = $('#chat-input');
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendChat();
    }
  });
  input.addEventListener('input', () => {
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 140) + 'px';
  });

  $('#chat-suggestions').addEventListener('click', (e) => {
    const chip = e.target.closest('.chip');
    if (!chip) return;
    $('#chat-input').value = chip.dataset.prompt;
    $('#chat-input').focus();
  });

  $('#btn-clear-chat').addEventListener('click', () => {
    if (!confirm('确定清空当前对话？')) return;
    state.messages = [];
    state.conversationId = null;
    $('#chat-body').innerHTML = '';
    addWelcome();
  });

  // 消息操作：复制 / 导出单条
  $('#chat-body').addEventListener('click', async (e) => {
    const btn = e.target.closest('button[data-act]');
    if (!btn) return;
    const msgEl = btn.closest('.msg');
    const bubble = msgEl && msgEl.querySelector('.bubble');
    const text = bubble ? bubble.innerText.trim() : '';
    if (!text) return;
    if (btn.dataset.act === 'copy') {
      await copyText(text);
    } else if (btn.dataset.act === 'export') {
      try {
        const title = (state.messages.find((m) => m.role === 'user') || {}).content || '旅行计划';
        await downloadExport('md', title.slice(0, 20), text);
        showToast('已导出');
      } catch (err) {
        showToast(err.message, 'error');
      }
    } else if (btn.dataset.act === 'fav') {
      try {
        const title = (state.messages.find((m) => m.role === 'user') || {}).content || '旅行计划';
        await api('POST', '/api/favorites', { title: title.slice(0, 40), content: text });
        showToast('已收藏到灵感库 ⭐');
      } catch (err) { showToast(err.message, 'error'); }
    }
  });

  // 历史会话
  $('#btn-history').addEventListener('click', async () => {
    const panel = $('#history-panel');
    if (!panel.hidden) return closeHistory();
    await loadHistory();
    panel.hidden = false;
  });
  $('#btn-history-close').addEventListener('click', closeHistory);
  $('#history-list').addEventListener('click', async (e) => {
    const item = e.target.closest('.history-item');
    if (!item) return;
    const id = item.dataset.id;
    if (e.target.closest('[data-action="del"]')) {
      try {
        await api('DELETE', `/api/conversations/${id}`);
        if (state.conversationId === id) { state.conversationId = null; }
        await loadHistory();
      } catch (err) { showToast(err.message, 'error'); }
      return;
    }
    try {
      await loadConversation(id);
    } catch (err) { showToast(err.message, 'error'); }
  });

  // 导出（全部对话）
  $('#btn-export').addEventListener('click', (e) => {
    e.stopPropagation();
    const menu = $('#export-menu');
    menu.hidden = !menu.hidden;
  });
  $('#export-menu').addEventListener('click', async (e) => {
    const btn = e.target.closest('button[data-fmt]');
    if (!btn) return;
    $('#export-menu').hidden = true;
    if (!state.messages.length) { showToast('还没有对话内容可导出', 'error'); return; }
    try {
      await exportConversation(btn.dataset.fmt);
      showToast('导出成功');
    } catch (err) {
      showToast(err.message, 'error');
    }
  });
  document.addEventListener('click', (e) => {
    if (!e.target.closest('.export-wrap')) $('#export-menu').hidden = true;
  });

  addWelcome();
  scrollChatToBottom();
}

/* ---------- 灵感库（收藏） ---------- */
async function loadFavorites() {
  try {
    const data = await api('GET', '/api/favorites');
    state.favorites = data.favorites;
    const list = $('#favs-list');
    if (!state.favorites.length) {
      list.innerHTML = '<div class="pref-empty">还没有收藏，AI 回复右下角点 ⭐ 即可收藏</div>';
      return;
    }
    list.innerHTML = state.favorites.map((f) => `
      <div class="history-item" data-id="${f.id}">
        <div class="history-title">⭐ ${esc(f.title)}</div>
        <div class="history-meta">${f.content.slice(0, 40)}… · ${fmtDateTime(f.createdAt)}</div>
        <button class="del" title="删除" data-action="del-fav">×</button>
      </div>`).join('');
  } catch (err) { showToast(err.message, 'error'); }
}

function closeFavs() {
  const panel = $('#favs-panel');
  if (panel) panel.hidden = true;
}

function initFavs() {
  $('#btn-favs').addEventListener('click', async () => {
    const panel = $('#favs-panel');
    if (!panel.hidden) return closeFavs();
    await loadFavorites();
    panel.hidden = false;
  });
  $('#btn-favs-close').addEventListener('click', closeFavs);
  $('#favs-list').addEventListener('click', async (e) => {
    const item = e.target.closest('.history-item');
    if (!item) return;
    const id = item.dataset.id;
    if (e.target.closest('[data-action="del-fav"]')) {
      try {
        await api('DELETE', `/api/favorites/${id}`);
        await loadFavorites();
      } catch (err) { showToast(err.message, 'error'); }
      return;
    }
    const fav = state.favorites.find((f) => f.id === id);
    if (!fav) return;
    closeFavs();
    state.messages.push({ role: 'assistant', content: fav.content });
    renderMessage('assistant', fav.content);
    scrollChatToBottom();
  });
}

/* ---------- 历史会话 / 导出 ---------- */
function renderAllMessages() {
  $('#chat-body').innerHTML = '';
  for (const m of state.messages) {
    renderMessage(m.role, m.content, 'm-' + Date.now() + Math.random());
  }
  scrollChatToBottom();
}

function buildConversationMarkdown() {
  const lines = ['# 旅行规划对话', '', `> 用户：${state.user ? state.user.username : ''} · 导出时间：${new Date().toLocaleString('zh-CN')}`, ''];
  for (const m of state.messages) {
    lines.push(m.role === 'user' ? '## 🙋 用户' : '## 🤖 AI', '', m.content, '');
  }
  return lines.join('\\n');
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch (err) {
    const ta = document.createElement('textarea');
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    ta.remove();
  }
  showToast('已复制到剪贴板');
}

async function downloadExport(fmt, title, content) {
  const res = await fetch('/api/export', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ format: fmt, title, content }),
  });
  if (!res.ok) {
    let msg = `导出失败 (${res.status})`;
    try { const d = await res.json(); if (d.error) msg = d.error; } catch { /* ignore */ }
    throw new Error(msg);
  }
  const blob = await res.blob();
  const cd = res.headers.get('Content-Disposition') || '';
  const m = cd.match(/filename\*=UTF-8''([^;]+)/);
  const fname = m ? decodeURIComponent(m[1]) : `旅行计划.${fmt}`;
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = fname;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

async function exportConversation(fmt) {
  const title = (state.messages.find((m) => m.role === 'user') || {}).content || '旅行计划';
  await downloadExport(fmt, title.slice(0, 30), buildConversationMarkdown());
}

async function loadHistory() {
  const data = await api('GET', '/api/conversations');
  state.conversations = data.conversations;
  const list = $('#history-list');
  if (!state.conversations.length) {
    list.innerHTML = '<div class="pref-empty">暂无历史对话</div>';
    return;
  }
  list.innerHTML = state.conversations.map((c) => `
    <div class="history-item" data-id="${c.id}">
      <div class="history-title">${esc(c.title)}</div>
      <div class="history-meta">${c.messageCount} 条 · ${fmtDateTime(c.updatedAt)}</div>
      <button class="del" title="删除" data-action="del">×</button>
    </div>`).join('');
}

function closeHistory() {
  const panel = $('#history-panel');
  if (panel) panel.hidden = true;
}

async function loadConversation(id) {
  const data = await api('GET', `/api/conversations/${id}`);
  state.messages = data.conversation.messages || [];
  state.conversationId = id;
  renderAllMessages();
  closeHistory();
  showToast('已载入历史对话');
}

/* ---------- 知识库标签页（偏好 / 攻略） ---------- */
function initKbTabs() {
  $$('.kb-tab').forEach((tab) => {
    tab.addEventListener('click', () => {
      $$('.kb-tab').forEach((t) => t.classList.toggle('active', t === tab));
      const target = tab.dataset.tab;
      $('#kb-prefs').hidden = target !== 'prefs';
      $('#kb-guides').hidden = target !== 'guides';
    });
  });
}

/* ---------- 攻略知识库（RAG） ---------- */
async function loadGuides() {
  try {
    const data = await api('GET', '/api/guides');
    state.guides = data.guides;
    renderGuides();
  } catch (err) { showToast(err.message, 'error'); }
}

function renderGuides() {
  const box = $('#guide-list');
  if (!box) return;
  if (!state.guides || !state.guides.length) {
    box.innerHTML = '<div class="pref-empty">还没有攻略文档，上传后 AI 可检索引用</div>';
    return;
  }
  box.innerHTML = state.guides.map((g) => `
    <div class="guide-item" data-id="${g.id}">
      <div class="guide-name">📄 ${esc(g.filename)}</div>
      <div class="guide-meta">${g.chunks} 个片段</div>
      <button class="del" data-action="del-guide">×</button>
    </div>`).join('');
}

function initGuides() {
  $('#btn-upload-guide').addEventListener('click', () => $('#guide-file').click());
  $('#guide-file').addEventListener('change', async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const btn = $('#btn-upload-guide');
    btn.disabled = true;
    try {
      if (file.size > 3 * 1024 * 1024) throw new Error('文件不能超过 3MB');
      const buf = new Uint8Array(await file.arrayBuffer());
      const data = await api('POST', '/api/guides', { filename: file.name, contentBase64: bufToBase64(buf) });
      showToast(`已入库：${data.guide.filename}（${data.chunks} 个片段）`);
      await loadGuides();
    } catch (err) {
      showToast(err.message, 'error');
    } finally {
      btn.disabled = false;
      e.target.value = '';
    }
  });
  $('#guide-list').addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-action="del-guide"]');
    if (!btn) return;
    const item = btn.closest('.guide-item');
    try {
      await api('DELETE', `/api/guides/${item.dataset.id}`);
      await loadGuides();
    } catch (err) { showToast(err.message, 'error'); }
  });
}

/* ---------- 多 Agent 协作 ---------- */
function initAgents() {
  $('#btn-agents').addEventListener('click', async () => {
    if (state.streaming) return;
    const input = $('#chat-input');
    const req = input.value.trim() || (state.messages.filter((m) => m.role === 'user').pop() || {}).content || '帮我规划一次 3 天旅行';
    const cityMatch = req.match(/(北京|上海|广州|深圳|成都|杭州|重庆|西安|厦门|青岛|三亚|大理|丽江|桂林|南京|长沙|武汉|苏州|香港|东京|巴黎)/);
    const city = cityMatch ? cityMatch[1] : '';
    renderMessage('user', `【多Agent】${req}`);
    renderTyping();
    state.streaming = true;
    $('#chat-send').disabled = true;
    try {
      const data = await api('POST', '/api/agents/plan', { request: req, city: city || null });
      removeTyping();
      const agentNames = (data.agents || []).join(' + ');
      const content = `**🤖 多 Agent 协作结果**（${agentNames}）\n\n${data.merged}`;
      renderMessage('assistant', content);
      state.messages.push({ role: 'user', content: req });
      state.messages.push({ role: 'assistant', content: content });
      scrollChatToBottom();
    } catch (err) {
      removeTyping();
      showChatError(err.message);
    } finally {
      state.streaming = false;
      $('#chat-send').disabled = false;
    }
  });
}

/* ---------- 实时天气 ---------- */
let lastWeather = null;

function closeWeather() {
  const panel = $('#weather-panel');
  if (panel) panel.hidden = true;
}

function renderWeather(data) {
  lastWeather = data;
  const box = $('#weather-result');
  const cur = data.current;
  const chips = data.daily.map((d) => `
    <div class="w-day">
      <span class="w-date">${d.date.slice(5)}</span>
      <span class="w-icon">${d.icon}</span>
      <span class="w-desc">${d.desc}</span>
      <span class="w-temp">${d.tmin}~${d.tmax}°</span>
    </div>`).join('');
  box.innerHTML = `
    <div class="w-current">
      <span class="w-big-icon">${cur.icon}</span>
      <div>
        <div class="w-city">${esc(data.city)}${data.country ? ` · ${esc(data.country)}` : ''}</div>
        <div class="w-now">${cur.temp}°C ${esc(cur.desc)}</div>
        <div class="w-meta">风速 ${cur.wind} km/h · 数据源 ${esc(data.source)}</div>
      </div>
    </div>
    <div class="w-days">${chips}</div>
    <button type="button" class="btn primary small" id="btn-weather-send">🤖 结合天气帮我规划</button>`;
  $('#btn-weather-send').addEventListener('click', sendWeatherToChat);
}

async function queryWeather(city) {
  const hint = $('#weather-hint');
  const btn = $('#weather-form button[type=submit]');
  btn.disabled = true;
  hint.textContent = '正在查询天气…';
  hint.className = 'hint';
  try {
    const data = await api('GET', `/api/weather?city=${encodeURIComponent(city)}`);
    hint.textContent = '';
    renderWeather(data);
  } catch (err) {
    hint.textContent = err.message;
    hint.className = 'hint';
  } finally {
    btn.disabled = false;
  }
}

function sendWeatherToChat() {
  if (!lastWeather) return;
  const cur = lastWeather.current;
  const daily = lastWeather.daily.slice(0, 3).map((d) => `${d.date.slice(5)} ${d.desc} ${d.tmin}~${d.tmax}°`).join('；');
  const msg = `请结合 ${lastWeather.city} 的实时天气规划行程：今天 ${cur.desc} ${cur.temp}°C；未来几天：${daily}。`;
  closeWeather();
  const input = $('#chat-input');
  input.value = msg;
  input.style.height = 'auto';
  sendChat();
}

function initWeather() {
  $('#btn-weather').addEventListener('click', () => {
    const panel = $('#weather-panel');
    if (!panel.hidden) return closeWeather();
    panel.hidden = false;
    $('#weather-city').focus();
  });
  $('#btn-weather-close').addEventListener('click', closeWeather);
  $('#weather-form').addEventListener('submit', (e) => {
    e.preventDefault();
    const city = $('#weather-city').value.trim();
    if (city) queryWeather(city);
  });
  $('#weather-city').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      const city = $('#weather-city').value.trim();
      if (city) queryWeather(city);
    }
  });
}

/* ---------- 个人中心 ---------- */
async function loadProfile() {
  try {
    const data = await api('GET', '/api/me');
    const u = data.user;
    state.user = { ...state.user, nickname: u.nickname, avatar: u.avatar, email: u.email };
    $('#profile-avatar').textContent = u.avatar || '🧑‍✈️';
    $('#profile-name').textContent = u.nickname || u.username;
    $('#profile-meta').textContent = `@${u.username} · ${u.role === 'admin' ? '管理员' : '普通用户'} · 注册于 ${fmtDateTime(u.createdAt)}`;
    $('#profile-nickname').value = u.nickname || '';
    $('#profile-email').value = u.email || '';
    $('#stat-prefs').textContent = data.stats.preferences;
    $('#stat-convs').textContent = data.stats.conversations;
    $('#stat-role').textContent = u.role === 'admin' ? '管理员' : '用户';
    renderAvatarPicker(u.avatar || '🧑‍✈️');
    setHint($('#profile-hint'), '');
  } catch (err) {
    showToast(err.message, 'error');
  }
}

function renderAvatarPicker(selected) {
  const box = $('#avatar-picker');
  box.innerHTML = AVATARS.map((a) =>
    `<button type="button" class="avatar-opt ${a === selected ? 'active' : ''}" data-avatar="${a}">${a}</button>`).join('');
  box.querySelectorAll('.avatar-opt').forEach((btn) => {
    btn.addEventListener('click', () => {
      box.querySelectorAll('.avatar-opt').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
    });
  });
}

function initProfile() {
  $('#profile-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const hint = $('#profile-hint');
    const btn = e.target.querySelector('button[type=submit]');
    btn.disabled = true;
    try {
      const activeAvatar = $('#avatar-picker .avatar-opt.active');
      const data = await api('PATCH', '/api/me', {
        nickname: $('#profile-nickname').value.trim(),
        email: $('#profile-email').value.trim(),
        avatar: activeAvatar ? activeAvatar.dataset.avatar : null,
      });
      state.user = { ...state.user, ...data.user };
      await loadProfile();
      setHint(hint, '资料已保存 ✓', true);
      showToast('资料已保存');
      renderNav();
    } catch (err) {
      setHint(hint, err.message);
    } finally {
      btn.disabled = false;
    }
  });

  $('#btn-reset-request').addEventListener('click', async () => {
    const hint = $('#reset-req-hint');
    if (!confirm('确定向管理员提交密码重置申请？批准后你的旧会话将失效。')) return;
    try {
      await api('POST', '/api/me/reset-request', { reason: '个人中心申请' });
      setHint(hint, '已提交申请，等待管理员处理 ✓', true);
      showToast('已提交重置申请');
    } catch (err) {
      setHint(hint, err.message);
    }
  });

  $('#password-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const hint = $('#pwd-hint');
    const btn = e.target.querySelector('button[type=submit]');
    const oldPwd = $('#pwd-old').value;
    const newPwd = $('#pwd-new').value;
    const confirm = $('#pwd-confirm').value;
    if (newPwd !== confirm) {
      setHint(hint, '两次输入的新密码不一致');
      return;
    }
    btn.disabled = true;
    try {
      await api('POST', '/api/me/password', { oldPassword: oldPwd, newPassword: newPwd });
      $('#password-form').reset();
      setHint(hint, '密码已修改 ✓', true);
      showToast('密码已修改');
    } catch (err) {
      setHint(hint, err.message);
    } finally {
      btn.disabled = false;
    }
  });
}

/* ---------- 管理员：重置申请 ---------- */
async function loadResetRequests() {
  try {
    const data = await api('GET', '/api/admin/reset-requests');
    const reqs = data.requests || [];
    const tbody = $('#reset-request-table');
    const pending = reqs.filter((r) => r.status === 'pending').length;
    const tab = document.querySelector('.admin-tabs [data-admin-tab="reset"]');
    if (tab) tab.textContent = pending ? `🔑 重置申请 (${pending})` : '🔑 重置申请';
    if (!reqs.length) {
      tbody.innerHTML = '<tr><td colspan="5" class="pref-empty">暂无重置申请</td></tr>';
      return;
    }
    tbody.innerHTML = reqs.map((r) => `
      <tr>
        <td>${fmtDateTime(r.createdAt)}</td>
        <td>${esc(r.username)}</td>
        <td>${esc(r.reason || '未填写')}</td>
        <td>${r.status === 'pending' ? '<span class="badge banned">待处理</span>'
            : r.status === 'done' ? '<span class="badge active">已批准</span>' : '<span class="badge user">已拒绝</span>'}</td>
        <td>
          ${r.status === 'pending' ? `
            <button class="btn small" data-req-action="approve" data-id="${r.id}">✓ 批准（生成新密码）</button>
            <button class="btn small danger" data-req-action="reject" data-id="${r.id}">✕ 拒绝</button>` : '—'}
        </td>
      </tr>`).join('');
  } catch (err) { showToast(err.message, 'error'); }
}

/* ---------- 管理员：审计日志 ---------- */
async function loadAudit() {
  try {
    const data = await api('GET', '/api/admin/audit');
    const tbody = $('#audit-table');
    if (!data.audit.length) {
      tbody.innerHTML = '<tr><td colspan="5" class="pref-empty">暂无操作记录</td></tr>';
      return;
    }
    tbody.innerHTML = data.audit.map((a) => `
      <tr>
        <td>${fmtDateTime(a.createdAt)}</td>
        <td>${esc(a.actorName)} <span class="badge ${a.actorRole}">${a.actorRole === 'admin' ? '管理员' : '用户'}</span></td>
        <td>${esc(a.action)}</td>
        <td>${esc(a.target)}</td>
        <td>${esc(a.detail || '—')}</td>
      </tr>`).join('');
  } catch (err) { showToast(err.message, 'error'); }
}

function initAdminTabs() {
  $$('.admin-tabs .kb-tab').forEach((tab) => {
    tab.addEventListener('click', async () => {
      $$('.admin-tabs .kb-tab').forEach((t) => t.classList.toggle('active', t === tab));
      const target = tab.dataset.adminTab;
      $('#admin-users-wrap').hidden = target !== 'users';
      $('#admin-reset-wrap').hidden = target !== 'reset';
      $('#admin-audit-wrap').hidden = target !== 'audit';
      if (target === 'audit') await loadAudit();
      if (target === 'reset') await loadResetRequests();
    });
  });
  $('#reset-request-table').addEventListener('click', async (e) => {
    const btn = e.target.closest('button[data-req-action]');
    if (!btn) return;
    const id = btn.dataset.id;
    try {
      if (btn.dataset.reqAction === 'approve') {
        if (!confirm('批准该申请并生成新密码？（用户旧会话将失效）')) return;
        const data = await api('POST', `/api/admin/reset-requests/${id}/approve`);
        showToast(`已批准，新密码：${data.newPassword}`);
        alert(`已为用户 ${data.username} 生成新密码：${data.newPassword}\n请告知用户。`);
      } else if (btn.dataset.reqAction === 'reject') {
        if (!confirm('确定拒绝该申请？')) return;
        await api('POST', `/api/admin/reset-requests/${id}/reject`);
        showToast('已拒绝该申请');
      }
      await loadResetRequests();
    } catch (err) { showToast(err.message, 'error'); }
  });
  $('#btn-clear-audit').addEventListener('click', async () => {
    if (!confirm('确定清空全部审计日志？')) return;
    try {
      await api('DELETE', '/api/admin/audit');
      showToast('日志已清空');
      await loadAudit();
    } catch (err) { showToast(err.message, 'error'); }
  });
}

/* ---------- 管理员 ---------- */
async function loadAdminUsers() {
  const data = await api('GET', '/api/admin/users');
  state.users = data.users;
  const tbody = $('#user-table');
  tbody.innerHTML = state.users.map((u) => `
    <tr>
      <td>${esc(u.username)}${u.id === state.user.id ? ' <span class="muted small">(我)</span>' : ''}</td>
      <td>${esc(u.email || '—')}</td>
      <td><span class="badge ${u.role}">${u.role === 'admin' ? '管理员' : '用户'}</span></td>
      <td><span class="badge ${u.status === 'banned' ? 'banned' : 'active'}">${u.status === 'banned' ? (u.banUntil ? `🚫 已封禁·至 ${(u.banUntil || '').slice(5, 10)}` : '🚫 已封禁') : '正常'}</span></td>
      <td>${fmtDateTime(u.createdAt)}</td>
      <td>
        ${u.id !== state.user.id ? `
          <button class="btn small" data-action="role" data-id="${u.id}" data-role="${u.role === 'admin' ? 'user' : 'admin'}">
            ${u.role === 'admin' ? '降为用户' : '设为管理员'}
          </button>
          <button class="btn small" data-action="reset-pwd" data-id="${u.id}">🔑 重置密码</button>
          <button class="btn small ${u.status === 'banned' ? '' : 'danger'}" data-action="toggle-ban" data-id="${u.id}" data-status="${u.status === 'banned' ? 'active' : 'banned'}">
            ${u.status === 'banned' ? '解封' : '封禁'}
          </button>
          <button class="btn small danger" data-action="del-user" data-id="${u.id}">删除</button>` : '—'}
      </td>
    </tr>`).join('');
}

function initAdmin() {
  $('#btn-new-admin').addEventListener('click', () => {
    $('#admin-form-wrap').hidden = false;
    $('#admin-username').focus();
    setHint($('#admin-hint'), '');
  });
  $('#admin-cancel').addEventListener('click', () => {
    $('#admin-form-wrap').hidden = true;
    setHint($('#admin-hint'), '');
  });
  $('#admin-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const hint = $('#admin-hint');
    const btn = e.target.querySelector('button[type=submit]');
    btn.disabled = true;
    try {
      await api('POST', '/api/admin/register', {
        username: $('#admin-username').value.trim(),
        password: $('#admin-password').value,
        email: $('#admin-email').value.trim(),
      });
      $('#admin-form').reset();
      $('#admin-form-wrap').hidden = true;
      setHint(hint, '');
      showToast('管理员已创建');
      await loadAdminUsers();
    } catch (err) {
      setHint(hint, err.message);
    } finally {
      btn.disabled = false;
    }
  });
  $('#user-table').addEventListener('click', async (e) => {
    const btn = e.target.closest('button[data-action]');
    if (!btn) return;
    const id = btn.dataset.id;
    try {
      if (btn.dataset.action === 'role') {
        const target = state.users.find((u) => u.id === id);
        const newRole = target.role === 'admin' ? 'user' : 'admin';
        if (!confirm(`确定将 ${target.username} 设为${newRole === 'admin' ? '管理员' : '普通用户'}？`)) return;
        await api('PATCH', `/api/admin/users/${id}/role`, { role: newRole });
        showToast('角色已更新');
      } else if (btn.dataset.action === 'del-user') {
        const target = state.users.find((u) => u.id === id);
        if (!confirm(`确定删除用户 ${target.username}？`)) return;
        await api('DELETE', `/api/admin/users/${id}`);
        showToast('用户已删除');
      } else if (btn.dataset.action === 'toggle-ban') {
        const target = state.users.find((u) => u.id === id);
        const ban = btn.dataset.status === 'banned';
        if (ban) {
          const days = prompt(`封禁用户 ${target.username}？\n\n请输入封禁天数（留空 = 永久封禁）：`);
          if (days === null) return;
          const banDays = days.trim() === '' ? null : Number(days);
          if (banDays !== null && (!Number.isFinite(banDays) || banDays <= 0)) { showToast('请输入有效的封禁天数', 'error'); return; }
          await api('PATCH', `/api/admin/users/${id}/status`, { status: 'banned', banDays });
          showToast(banDays ? `已封禁 ${banDays} 天（到期自动解封）` : '已永久封禁');
        } else {
          if (!confirm(`确定解封用户 ${target.username}？`)) return;
          await api('PATCH', `/api/admin/users/${id}/status`, { status: 'active' });
          showToast('已解封该账号');
        }
      } else if (btn.dataset.action === 'reset-pwd') {
        const target = state.users.find((u) => u.id === id);
        const input = prompt(`为 ${target.username} 设置新密码（至少 6 位）；留空则自动生成随机密码：`);
        if (input === null) return;
        if (input && input.length < 6) { showToast('新密码至少 6 位', 'error'); return; }
        const data = await api('POST', `/api/admin/users/${id}/reset-password`, { newPassword: input || null });
        showToast(`已重置，新密码：${data.newPassword}`);
        alert(`已为 ${target.username} 重置密码\n新密码：${data.newPassword}\n（请告知用户，其旧会话已全部失效）`);
      }
      await loadAdminUsers();
    } catch (err) {
      showToast(err.message, 'error');
    }
  });
}

/* ---------- 启动 ---------- */
async function boot() {
  renderNav();
  if (!state.user) return;
  await loadConfig();
  if (state.activeView === 'admin') {
    try { await loadAdminUsers(); } catch (err) { if (err.status === 401) handleExpired(); }
  } else {
    resetChatView();
    await loadPrefs();
    await loadGuides();
  }
}

function handleExpired() {
  state.user = null;
  state.prefs = [];
  renderNav();
  showToast('登录已过期，请重新登录', 'error');
}

async function init() {
  initAuth();
  initChat();
  initPrefs();
  initGuides();
  initKbTabs();
  initWeather();
  initFavs();
  initAgents();
  initProfile();
  initAdmin();
  initAdminTabs();
  // 只认本标签页保存的令牌（sessionStorage 按标签页隔离），新标签页默认显示登录页
  if (sessionToken) {
    try {
      const data = await api('GET', '/api/auth/me');
      state.user = data.user;
      if (data.token) saveSession(data.token);
    } catch {
      saveSession('');
    }
  }
  await boot();
}

document.addEventListener('DOMContentLoaded', init);



