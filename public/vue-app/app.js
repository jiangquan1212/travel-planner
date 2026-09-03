/* AI 旅行规划师 · Vue 3 前端
   里程碑1：登录/注册 + 智能对话 + 偏好/攻略知识库
   里程碑2：会话历史（新建/切换/删除）+ 灵感库收藏 + 导出(MD/Word/PDF) + 个人中心
   使用 Vue 3 全局构建，接口与后端完全一致。 */
'use strict';

const { createApp } = Vue;

const SUGGESTIONS = [
  '帮我规划 5 天 4 晚云南大理之旅，预算 5000，从上海出发',
  '北京 3 日游攻略，含美食、交通和住宿',
  '适合带父母去的国内景点推荐',
  '暑假带孩子亲子游，预算 8000，求推荐',
  '穷游成都 4 天，怎么安排最划算',
];

const AVATARS = ['🧳', '✈️', '🌊', '⛰️', '🏔️', '🏖️', '🌴', '🏜️', '🍜', '📷', '🎒', '🚴', '😎', '🐼'];
const FEEDBACK_CATS = ['建议', '问题反馈', '功能需求', '其他'];

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function md(text) {
  const lines = esc(text).split('\n');
  let html = '';
  let list = null;
  const close = () => { if (list) { html += '</' + list + '>'; list = null; } };
  const inline = (s) => s.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>').replace(/`([^`]+)`/g, '<code>$1</code>');
  for (const raw of lines) {
    const line = raw.trimEnd();
    const h = line.match(/^(#{1,4})\s+(.*)$/);
    const ul = line.match(/^[-*]\s+(.*)$/);
    const ol = line.match(/^\d+[.)]\s+(.*)$/);
    if (h) { close(); html += `<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`; }
    else if (ul) { if (list !== 'ul') { close(); html += '<ul>'; list = 'ul'; } html += `<li>${inline(ul[1])}</li>`; }
    else if (ol) { if (list !== 'ol') { close(); html += '<ol>'; list = 'ol'; } html += `<li>${inline(ol[1])}</li>`; }
    else if (!line.trim()) { close(); }
    else { close(); html += `<p>${inline(line)}</p>`; }
  }
  close();
  return html;
}

function bufToB64(buf) {
  let b = '';
  for (let i = 0; i < buf.length; i += 0x8000) b += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
  return btoa(b);
}

createApp({
  data() {
    return {
      user: null,
      chatEnabled: false, model: '',
      authMode: 'login', username: '', password: '', regEmail: '', authHint: '', authBusy: false,
      view: 'chat', kbTab: 'kb',
      messages: [], input: '', sending: false, convId: null, convs: [],
      prefs: [], newPref: '', prefHint: '', prefBusy: false,
      guides: [], guideBusy: false,
      favs: [], favHint: '', exportIdx: -1,
      profile: null, stats: null, profHint: '', profBusy: false,
      nickname: '', avatar: '', profEmail: '', oldPwd: '', newPwd: '', pwdHint: '',
      memory: [], memHint: '', resetHint: '',
      // 管理后台（仅 admin）
      adminTab: 'users', users: [], resetReqs: [], audit: [], adminFeedback: [], adminHint: '',
      adminNew: { show: false, username: '', password: '', email: '', hint: '', busy: false },
      // 天气 / 多 Agent 快捷工具
      weather: { open: false, city: '', hint: '', busy: false, data: null },
      agentBusy: false,
      // 意见反馈
      feedbackCats: FEEDBACK_CATS,
      feedback: { category: '建议', content: '', hint: '', busy: false },
      myFeedback: [],
      avatars: AVATARS,
      suggestions: SUGGESTIONS,
      chatBody: null,
    };
  },
  computed: {
    canSend() { return this.input.trim() && !this.sending && this.user; },
    autoTitle() {
      const u = this.messages.find((m) => m.role === 'user');
      return u ? String(u.content).slice(0, 28) : '新对话';
    },
    pendingResetCount() {
      return this.resetReqs.filter((r) => r.status === 'pending').length;
    },
    pendingFeedbackCount() {
      return this.adminFeedback.filter((f) => f.status === 'pending').length;
    },
  },
  mounted() {
    this.$nextTick(() => { this.chatBody = this.$refs.chatBody; });
    this.boot();
  },
  methods: {
    async api(method, url, body) {
      const headers = {};
      const token = sessionStorage.getItem('tp_token');
      if (token) headers['X-Session-Token'] = token;
      if (body !== undefined) headers['Content-Type'] = 'application/json';
      const res = await fetch(url, { method, headers, body: body !== undefined ? JSON.stringify(body) : undefined });
      let data = null;
      try { data = await res.json(); } catch (e) { /* ignore */ }
      if (!res.ok) throw new Error((data && data.error) || `请求失败(${res.status})`);
      return data;
    },
    async boot() {
      const token = sessionStorage.getItem('tp_token');
      if (token) {
        try {
          const d = await this.api('GET', '/api/auth/me');
          this.user = d.user;
          if (d.token) sessionStorage.setItem('tp_token', d.token);
        } catch (e) { sessionStorage.removeItem('tp_token'); }
      }
      if (this.user) await this.loadAll();
    },
    async loadAll() {
      try {
        const cfg = await this.api('GET', '/api/config');
        this.chatEnabled = cfg.chatEnabled; this.model = cfg.model;
      } catch (e) { /* ignore */ }
      await Promise.all([this.loadPrefs(), this.loadGuides(), this.loadConvs(), this.loadFavs()]);
    },
    async doAuth() {
      this.authHint = ''; this.authBusy = true;
      try {
        const url = this.authMode === 'login' ? '/api/auth/login' : '/api/auth/register';
        const d = await this.api('POST', url, { username: this.username, password: this.password, email: this.regEmail });
        this.user = d.user;
        sessionStorage.setItem('tp_token', d.token);
        this.password = ''; this.regEmail = '';
        this.view = 'chat'; this.kbTab = 'kb';
        await this.loadAll();
      } catch (e) { this.authHint = e.message; }
      finally { this.authBusy = false; }
    },
    async logout() {
      try { await this.api('POST', '/api/auth/logout'); } catch (e) { /* ignore */ }
      sessionStorage.removeItem('tp_token');
      this.user = null; this.messages = []; this.prefs = []; this.guides = [];
      this.convs = []; this.favs = []; this.memory = []; this.profile = null;
      this.view = 'chat'; this.kbTab = 'kb'; this.convId = null;
    },
    scrollDown() {
      this.$nextTick(() => { if (this.chatBody) this.chatBody.scrollTop = this.chatBody.scrollHeight; });
    },
    // ---------- 会话历史 ----------
    async loadConvs() {
      try { const d = await this.api('GET', '/api/conversations'); this.convs = d.conversations; } catch (e) { /* ignore */ }
    },
    newChat() {
      this.messages = []; this.convId = null; this.view = 'chat'; this.kbTab = 'hist';
      this.exportIdx = -1; this.$nextTick(() => this.scrollDown());
    },
    async openConv(cid) {
      try {
        const d = await this.api('GET', '/api/conversations/' + cid);
        const conv = d.conversation || {};
        this.messages = (conv.messages || []).filter((m) => m.role === 'user' || m.role === 'assistant');
        this.convId = conv.id; this.view = 'chat'; this.kbTab = 'hist'; this.exportIdx = -1;
        this.$nextTick(() => this.scrollDown());
      } catch (e) { this.favHint = e.message; }
    },
    async delConv(cid) {
      try {
        await this.api('DELETE', '/api/conversations/' + cid);
        if (this.convId === cid) { this.convId = null; this.messages = []; }
        await this.loadConvs();
      } catch (e) { this.favHint = e.message; }
    },
    async sendMsg() {
      const text = this.input.trim();
      if (!text || this.sending) return;
      const created = !this.convId;
      this.messages.push({ role: 'user', content: text });
      this.input = ''; this.sending = true;
      this.scrollDown();
      try {
        const res = await fetch('/api/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-Session-Token': sessionStorage.getItem('tp_token') || '' },
          body: JSON.stringify({
            messages: this.messages.filter((m) => m.role !== 'tool'),
            conversationId: this.convId || undefined,
          }),
        });
        if (!res.ok) { let e = `请求失败(${res.status})`; try { const j = await res.json(); if (j.error) e = j.error; } catch (x) {} throw new Error(e); }
        if (!res.body) throw new Error('浏览器不支持流式');
        const reader = res.body.getReader();
        const dec = new TextDecoder();
        let buf = '';
        let assistant = '';
        const done = () => { if (assistant) this.messages.push({ role: 'assistant', content: assistant }); };
        while (true) {
          const { done: d, value } = await reader.read();
          if (d) break;
          buf += dec.decode(value, { stream: true });
          let i;
          while ((i = buf.indexOf('\n\n')) !== -1) {
            const block = buf.slice(0, i); buf = buf.slice(i + 2);
            for (const line of block.split('\n')) {
              if (!line.startsWith('data:')) continue;
              const raw = line.slice(5).trim(); if (!raw) continue;
              let ev; try { ev = JSON.parse(raw); } catch (e) { continue; }
              if (ev.type === 'tool') this.messages.push({ role: 'tool', content: ev.summary });
              else if (ev.type === 'delta') assistant += ev.content;
              else if (ev.type === 'done') { if (ev.conversationId) this.convId = ev.conversationId; }
              else if (ev.type === 'error') throw new Error(ev.message || '对话失败');
            }
            this.scrollDown();
          }
        }
        done();
        if (created && this.convId) await this.loadConvs();
      } catch (e) {
        this.messages.push({ role: 'assistant', content: '⚠️ ' + e.message });
      }
      this.sending = false;
      this.scrollDown();
    },
    // ---------- 知识库 ----------
    async loadPrefs() {
      try { const d = await this.api('GET', '/api/preferences'); this.prefs = d.preferences; } catch (e) { /* ignore */ }
    },
    async addPref() {
      const t = this.newPref.trim(); if (!t) return;
      try { await this.api('POST', '/api/preferences', { text: t, category: '其他', weight: 3 }); this.newPref = ''; await this.loadPrefs(); }
      catch (e) { this.prefHint = e.message; }
    },
    async delPref(id) { try { await this.api('DELETE', '/api/preferences/' + id); await this.loadPrefs(); } catch (e) { this.prefHint = e.message; } },
    async onPrefFile(e) {
      const f = e.target.files[0]; if (!f) return;
      this.prefBusy = true; this.prefHint = '';
      try {
        const buf = new Uint8Array(await f.arrayBuffer());
        const d = await this.api('POST', '/api/preferences/import', { filename: f.name, contentBase64: bufToB64(buf) });
        this.prefHint = `已导入 ${d.total} 条偏好（${d.method === 'ai' ? 'AI 提取' : '自动分句'}）`;
        await this.loadPrefs();
      } catch (err) { this.prefHint = err.message; }
      finally { this.prefBusy = false; e.target.value = ''; }
    },
    async loadGuides() {
      try { const d = await this.api('GET', '/api/guides'); this.guides = d.guides; } catch (e) { /* ignore */ }
    },
    async onGuideFile(e) {
      const f = e.target.files[0]; if (!f) return;
      this.guideBusy = true;
      try {
        const buf = new Uint8Array(await f.arrayBuffer());
        await this.api('POST', '/api/guides', { filename: f.name, contentBase64: bufToB64(buf) });
        await this.loadGuides();
      } catch (e) { this.prefHint = e.message; }
      finally { this.guideBusy = false; e.target.value = ''; }
    },
    async delGuide(id) { try { await this.api('DELETE', '/api/guides/' + id); await this.loadGuides(); } catch (e) { this.prefHint = e.message; } },
    // ---------- 灵感库 / 导出 ----------
    async loadFavs() {
      try { const d = await this.api('GET', '/api/favorites'); this.favs = d.favorites; } catch (e) { /* ignore */ }
    },
    async favContent(content, title) {
      content = String(content || '').trim();
      if (!content) return;
      try {
        await this.api('POST', '/api/favorites', { title: String(title || '旅行灵感').slice(0, 40), content: content.slice(0, 6000) });
        this.favHint = '已收藏到灵感库 💡';
        await this.loadFavs();
      } catch (e) { this.favHint = e.message; }
    },
    async delFav(id) { try { await this.api('DELETE', '/api/favorites/' + id); await this.loadFavs(); } catch (e) { this.favHint = e.message; } },
    async exportContent(content, title, fmt) {
      content = String(content || '').trim();
      if (!content) return;
      try {
        const res = await fetch('/api/export', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-Session-Token': sessionStorage.getItem('tp_token') || '' },
          body: JSON.stringify({ format: fmt, title: String(title || '旅行计划').slice(0, 40), content }),
        });
        if (!res.ok) {
          let e = `导出失败(${res.status})`;
          try { const j = await res.json(); if (j.error) e = j.error; } catch (x) {}
          throw new Error(e);
        }
        const blob = await res.blob();
        const cd = res.headers.get('Content-Disposition') || '';
        let fname = '';
        const m = cd.match(/filename\*=UTF-8''([^;]+)/i);
        if (m) { try { fname = decodeURIComponent(m[1]); } catch (x) {} }
        if (!fname) fname = '旅行计划.' + (fmt === 'markdown' ? 'md' : fmt);
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob); a.download = fname;
        document.body.appendChild(a); a.click(); a.remove();
        setTimeout(() => URL.revokeObjectURL(a.href), 4000);
        this.favHint = '已导出：' + fname;
      } catch (e) { this.favHint = e.message; }
      this.exportIdx = -1;
    },
    // ---------- 个人中心 ----------
    openView(v) {
      this.view = v;
      if (v === 'favs') this.loadFavs();
      if (v === 'profile') { this.loadProfile(); this.loadMemory(); this.loadMyFeedback(); }
      if (v === 'admin') { this.adminTab = 'users'; this.adminHint = ''; this.loadAdminUsers(); }
    },
    async loadProfile() {
      try {
        const d = await this.api('GET', '/api/me');
        this.profile = d.user; this.stats = d.stats;
        this.nickname = d.user.nickname || ''; this.avatar = d.user.avatar || ''; this.profEmail = d.user.email || '';
      } catch (e) { this.profHint = e.message; }
    },
    async saveProfile() {
      this.profBusy = true; this.profHint = '';
      try {
        const d = await this.api('PATCH', '/api/me', { nickname: this.nickname, avatar: this.avatar, email: this.profEmail });
        this.user = d.user; this.profile = d.user; this.profHint = '✅ 资料已保存';
      } catch (e) { this.profHint = e.message; }
      finally { this.profBusy = false; }
    },
    async changePwd() {
      this.pwdHint = '';
      try {
        await this.api('POST', '/api/me/password', { oldPassword: this.oldPwd, newPassword: this.newPwd });
        this.pwdHint = '✅ 密码已修改'; this.oldPwd = ''; this.newPwd = '';
      } catch (e) { this.pwdHint = e.message; }
    },
    async loadMemory() {
      try { const d = await this.api('GET', '/api/me/memory'); this.memory = d.memory; } catch (e) { /* ignore */ }
    },
    async delMemory(id) {
      try { await this.api('DELETE', '/api/me/memory/' + id); await this.loadMemory(); } catch (e) { this.memHint = e.message; }
    },
    async askReset() {
      this.resetHint = '';
      try {
        await this.api('POST', '/api/me/reset-request', { reason: '个人中心申请重置密码' });
        this.resetHint = '✅ 申请已提交，等待管理员处理（管理端会生成新密码）';
      } catch (e) { this.resetHint = e.message; }
    },
    // ---------- 意见反馈 ----------
    async loadMyFeedback() {
      try { const d = await this.api('GET', '/api/me/feedback'); this.myFeedback = d.feedback || []; } catch (e) { /* ignore */ }
    },
    async sendFeedback() {
      const content = this.feedback.content.trim();
      if (!content) { this.feedback.hint = '请先填写反馈内容'; return; }
      this.feedback.busy = true; this.feedback.hint = '';
      try {
        await this.api('POST', '/api/me/feedback', { content, category: this.feedback.category });
        this.feedback.content = '';
        this.feedback.hint = '✅ 已提交，管理员会尽快处理';
        await this.loadMyFeedback();
      } catch (e) { this.feedback.hint = e.message; }
      finally { this.feedback.busy = false; }
    },
    async loadAdminFeedback() {
      try { const d = await this.api('GET', '/api/admin/feedback'); this.adminFeedback = d.feedback || []; } catch (e) { this.adminHint = e.message; }
    },
    async adminFeedbackDone(f) {
      const reply = window.prompt(`处理反馈（来自 ${f.username} · ${f.category}）：\n可填写回复给用户，留空 = 仅标记已处理`, '');
      if (reply === null) return;
      try {
        await this.api('POST', `/api/admin/feedback/${f.id}/handle`, { reply: reply.trim() || null });
        this.adminHint = reply.trim() ? '已回复并处理' : '已标记为已处理';
        await this.loadAdminFeedback();
      } catch (e) { this.adminHint = e.message; }
    },
    // ---------- 管理后台 ----------
    async loadAdminUsers() {
      try { const d = await this.api('GET', '/api/admin/users'); this.users = d.users; } catch (e) { this.adminHint = e.message; }
    },
    async loadResetReqs() {
      try { const d = await this.api('GET', '/api/admin/reset-requests'); this.resetReqs = d.requests || []; } catch (e) { this.adminHint = e.message; }
    },
    async loadAudit() {
      try { const d = await this.api('GET', '/api/admin/audit'); this.audit = d.audit || []; } catch (e) { this.adminHint = e.message; }
    },
    async adminCreate() {
      this.adminNew.busy = true; this.adminNew.hint = '';
      try {
        await this.api('POST', '/api/admin/register', {
          username: this.adminNew.username.trim(),
          password: this.adminNew.password,
          email: this.adminNew.email.trim(),
        });
        this.adminNew = { show: false, username: '', password: '', email: '', hint: '✅ 管理员已创建', busy: false };
        await this.loadAdminUsers();
      } catch (e) { this.adminNew.hint = e.message; }
      finally { this.adminNew.busy = false; }
    },
    async adminRole(u) {
      const nr = u.role === 'admin' ? 'user' : 'admin';
      if (!window.confirm(`确定将 ${u.username} 设为${nr === 'admin' ? '管理员' : '普通用户'}？`)) return;
      try { await this.api('PATCH', `/api/admin/users/${u.id}/role`, { role: nr }); this.adminHint = '角色已更新'; await this.loadAdminUsers(); }
      catch (e) { this.adminHint = e.message; }
    },
    async adminDel(u) {
      if (!window.confirm(`确定删除用户 ${u.username}？该操作不可恢复。`)) return;
      try { await this.api('DELETE', `/api/admin/users/${u.id}`); this.adminHint = '用户已删除'; await this.loadAdminUsers(); }
      catch (e) { this.adminHint = e.message; }
    },
    async adminBan(u) {
      if (u.status === 'banned') {
        if (!window.confirm(`确定解封用户 ${u.username}？`)) return;
        try { await this.api('PATCH', `/api/admin/users/${u.id}/status`, { status: 'active' }); this.adminHint = '已解封'; await this.loadAdminUsers(); }
        catch (e) { this.adminHint = e.message; }
        return;
      }
      const days = window.prompt(`封禁用户 ${u.username}？\n请输入封禁天数（留空 = 永久封禁）：`, '');
      if (days === null) return;
      const banDays = days.trim() === '' ? null : Number(days);
      if (banDays !== null && (!Number.isFinite(banDays) || banDays <= 0)) { this.adminHint = '请输入有效的封禁天数'; return; }
      try {
        await this.api('PATCH', `/api/admin/users/${u.id}/status`, { status: 'banned', banDays });
        this.adminHint = banDays ? `已封禁 ${banDays} 天` : '已永久封禁';
        await this.loadAdminUsers();
      } catch (e) { this.adminHint = e.message; }
    },
    async adminResetPwd(u) {
      const input = window.prompt(`为 ${u.username} 设置新密码（至少 6 位）；留空则自动生成随机密码：`, '');
      if (input === null) return;
      if (input && input.length < 6) { this.adminHint = '新密码至少 6 位'; return; }
      try {
        const d = await this.api('POST', `/api/admin/users/${u.id}/reset-password`, { newPassword: input || null });
        window.alert(`已为 ${u.username} 重置密码\n新密码：${d.newPassword}\n（其旧会话已全部失效，请告知用户）`);
        await this.loadAdminUsers();
      } catch (e) { this.adminHint = e.message; }
    },
    async adminApprove(r) {
      if (!window.confirm('批准该申请并生成新密码？（用户旧会话将失效）')) return;
      try {
        const d = await this.api('POST', `/api/admin/reset-requests/${r.id}/approve`);
        window.alert(`已为用户 ${d.username} 生成新密码：${d.newPassword}\n请告知用户。`);
        await this.loadResetReqs();
      } catch (e) { this.adminHint = e.message; }
    },
    async adminReject(r) {
      if (!window.confirm('确定拒绝该申请？')) return;
      try { await this.api('POST', `/api/admin/reset-requests/${r.id}/reject`); await this.loadResetReqs(); }
      catch (e) { this.adminHint = e.message; }
    },
    async adminClearAudit() {
      if (!window.confirm('确定清空全部审计日志？')) return;
      try { await this.api('DELETE', '/api/admin/audit'); this.adminHint = '日志已清空'; await this.loadAudit(); }
      catch (e) { this.adminHint = e.message; }
    },
    // ---------- 天气 / 多 Agent ----------
    async queryWeather() {
      const city = this.weather.city.trim();
      if (!city) return;
      this.weather.busy = true; this.weather.hint = '正在查询天气…'; this.weather.data = null;
      try {
        const d = await this.api('GET', '/api/weather?city=' + encodeURIComponent(city));
        this.weather.data = d; this.weather.hint = '';
      } catch (e) { this.weather.hint = e.message; }
      finally { this.weather.busy = false; }
    },
    sendWeatherToChat() {
      const d = this.weather.data;
      if (!d) return;
      const cur = d.current;
      const daily = (d.daily || []).slice(0, 3).map((x) => `${x.date.slice(5)} ${x.desc} ${x.tmin}~${x.tmax}°`).join('；');
      this.input = `请结合 ${d.city} 的实时天气规划行程：今天 ${cur.desc} ${cur.temp}°C；未来几天：${daily}。`;
      this.weather.open = false;
      this.sendMsg();
    },
    async runAgents() {
      if (this.agentBusy) return;
      const lastUser = [...this.messages].reverse().find((m) => m.role === 'user');
      const req = this.input.trim() || (lastUser ? lastUser.content : '') || '帮我规划一次 3 天旅行';
      this.input = '';
      this.messages.push({ role: 'user', content: `【多Agent】${req}` });
      this.agentBusy = true;
      this.scrollDown();
      try {
        const cityMatch = req.match(/(北京|上海|广州|深圳|成都|杭州|重庆|西安|厦门|青岛|三亚|大理|丽江|桂林|南京|长沙|武汉|苏州|香港|东京|巴黎)/);
        const d = await this.api('POST', '/api/agents/plan', { request: req, city: cityMatch ? cityMatch[1] : null });
        const names = (d.agents || []).join(' + ');
        const content = `**🤖 多 Agent 协作结果**（${names}）\n\n${d.merged}`;
        this.messages.push({ role: 'assistant', content });
      } catch (e) {
        this.messages.push({ role: 'assistant', content: '⚠️ ' + e.message });
      }
      this.agentBusy = false;
      this.scrollDown();
    },
    // ---------- 工具 ----------
    pick(s) { this.input = s; },
    fmtDate(s) { return String(s || '').slice(0, 16).replace('T', ' '); },
    md: md,
    esc: esc,
    catName(c) { return c || '其他'; },
  },
  template: `
  <div>
    <!-- ===== 登录 / 注册 ===== -->
    <div v-if="!user" class="auth-wrap">
      <div class="auth-hero">🏔️ ✈️ 🌊</div>
      <div class="card auth-card">
        <h1>AI 旅行规划师 <span style="font-size:12px;color:#6b7280">(Vue 3)</span></h1>
        <p class="muted tagline">告诉 AI 你的旅行偏好和预算，自动生成多方位旅行计划</p>
        <div class="tabs">
          <button class="tab" :class="{active: authMode==='login'}" @click="authMode='login';authHint=''">登录</button>
          <button class="tab" :class="{active: authMode==='register'}" @click="authMode='register';authHint=''">注册</button>
        </div>
        <form @submit.prevent="doAuth">
          <div class="field"><label>用户名</label><input v-model="username" placeholder="3-20 位字母数字下划线" /></div>
          <div class="field" v-if="authMode==='register'"><label>邮箱（可选）</label><input v-model="regEmail" type="email" placeholder="you@example.com" /></div>
          <div class="field"><label>密码</label><input v-model="password" type="password" placeholder="至少 6 位" /></div>
          <button class="btn primary" style="width:100%" :disabled="authBusy">{{ authMode==='login' ? '登录' : '注册' }}</button>
          <p class="hint" style="text-align:center">{{ authHint }}</p>
        </form>
      </div>
    </div>

    <!-- ===== 主界面 ===== -->
    <div v-else>
      <div class="vue-top">
        <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
          <div class="brand">✈️ AI 旅行规划师 <span style="font-size:12px">Vue 3</span></div>
          <nav class="vue-nav">
            <button :class="{active: view==='chat'}" @click="view='chat'">💬 对话</button>
            <button :class="{active: view==='favs'}" @click="openView('favs')">💡 灵感库</button>
            <button :class="{active: view==='profile'}" @click="openView('profile')">👤 个人中心</button>
            <button v-if="user.role==='admin'" :class="{active: view==='admin'}" @click="openView('admin')">🛠 管理</button>
          </nav>
        </div>
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
          <span class="muted small">👤 {{ user.nickname || user.username }}（{{ user.role==='admin' ? '管理员' : '用户' }}）</span>
          <button class="btn small" @click="logout">退出</button>
        </div>
      </div>

      <!-- -------- 对话视图 -------- -->
      <div v-if="view==='chat'" class="vue-body">
        <aside>
          <div class="card" style="padding:16px">
            <div class="kb-tabbar">
              <button :class="{active: kbTab==='kb'}" @click="kbTab='kb'">📚 知识库</button>
              <button :class="{active: kbTab==='hist'}" @click="kbTab='hist'">🕘 历史</button>
            </div>

            <!-- 知识库 -->
            <div v-if="kbTab==='kb'">
              <h3 style="margin:0 0 10px">我的知识库</h3>
              <div class="field" style="margin-bottom:8px"><input v-model="newPref" placeholder="添加旅行偏好，如：喜欢美食" @keyup.enter="addPref" /></div>
              <div style="display:flex;gap:8px">
                <button class="btn primary small" @click="addPref">添加偏好</button>
                <label class="btn small" style="cursor:pointer;margin:0">{{ prefBusy ? '导入中…' : '📄 上传 PDF/TXT' }}
                  <input type="file" accept=".txt,.text,.md,.pdf,text/plain,application/pdf" hidden @change="onPrefFile" />
                </label>
              </div>
              <p class="hint">{{ prefHint }}</p>
              <div v-if="prefs.length" style="margin-top:10px">
                <div v-for="p in prefs" :key="p.id" class="pref-item-v">
                  <span>{{ p.text }} <span style="color:#f59e0b">★{{ p.weight||3 }}</span></span>
                  <button class="mini" @click="delPref(p.id)">×</button>
                </div>
              </div>
              <div style="margin-top:16px;border-top:1px dashed var(--border);padding-top:12px">
                <label class="btn small" style="cursor:pointer">📤 上传攻略<input type="file" accept=".txt,.md,.pdf" hidden @change="onGuideFile" /></label>
                <div v-for="g in guides" :key="g.id" class="pref-item-v">
                  <span>📄 {{ g.filename }}（{{ g.chunks }}）</span>
                  <button class="mini" @click="delGuide(g.id)">×</button>
                </div>
              </div>
            </div>

            <!-- 历史对话 -->
            <div v-else>
              <button class="btn primary small" style="width:100%;margin-bottom:8px" @click="newChat">＋ 新对话</button>
              <div v-if="!convs.length" class="empty-tip">暂无历史对话</div>
              <div v-for="c in convs" :key="c.id" class="hist-item" :class="{active: c.id===convId}" @click="openConv(c.id)">
                <div style="flex:1;min-width:0">
                  <div class="hist-title">{{ c.title }}</div>
                  <div class="muted small">{{ fmtDate(c.updatedAt) }} · {{ c.messageCount }} 条</div>
                </div>
                <button class="mini" @click.stop="delConv(c.id)" title="删除">×</button>
              </div>
            </div>
          </div>
        </aside>

        <div class="card chat-card" style="min-height:520px">
          <div class="card-head">
            <h2>💬 {{ autoTitle }} <span v-if="model" class="muted small">· {{ model }}</span></h2>
            <div class="head-actions">
              <button class="btn small" @click="weather.open = !weather.open">🌤 天气</button>
              <button class="btn small" :disabled="agentBusy" @click="runAgents">{{ agentBusy ? '规划中…' : '🧠 多Agent' }}</button>
              <button class="btn small" @click="newChat">＋ 新对话</button>
            </div>
          </div>
          <p class="hint" style="margin:0 0 6px">{{ favHint }}</p>
          <div v-if="weather.open" class="card" style="padding:12px;margin-bottom:10px;background:#f0f9ff;border:1px solid #bae6fd">
            <div style="display:flex;gap:8px;align-items:center">
              <input v-model="weather.city" placeholder="输入城市，如 成都" style="flex:1;padding:8px 12px;border:1.5px solid #bae6fd;border-radius:10px;font-size:13px" @keyup.enter="queryWeather" />
              <button class="btn primary small" :disabled="weather.busy" @click="queryWeather">{{ weather.busy ? '查询中…' : '查询' }}</button>
              <button class="mini" @click="weather.open=false">关闭</button>
            </div>
            <p class="hint">{{ weather.hint }}</p>
            <div v-if="weather.data">
              <div class="w-current">
                <span class="w-big-icon">{{ weather.data.current.icon }}</span>
                <div>
                  <div class="w-city">{{ weather.data.city }}</div>
                  <div class="w-now">{{ weather.data.current.temp }}°C {{ weather.data.current.desc }}</div>
                  <div class="w-meta">风速 {{ weather.data.current.wind }} km/h · {{ weather.data.source }}</div>
                </div>
              </div>
              <div class="w-days">
                <div v-for="x in weather.data.daily" :key="x.date" class="w-day">
                  <span class="w-date">{{ x.date.slice(5) }}</span>
                  <span class="w-icon">{{ x.icon }}</span>
                  <span class="w-desc">{{ x.desc }}</span>
                  <span class="w-temp">{{ x.tmin }}~{{ x.tmax }}°</span>
                </div>
              </div>
              <button class="btn primary small" style="margin-top:8px" @click="sendWeatherToChat">🤖 结合天气帮我规划</button>
            </div>
          </div>
          <div class="chat-scroll" ref="chatBody">
            <div class="chat-welcome" v-if="!messages.length">
              <p class="welcome-title">你好，我是你的 AI 旅行规划师 👋</p>
              <p class="muted small">参考你的偏好与攻略知识库，为你定制多方位旅行计划（Vue 3）。</p>
            </div>
            <div v-for="(m,idx) in messages" :key="idx" class="vue-msg" :class="m.role">
              <div class="who">{{ m.role==='user' ? '🙂' : (m.role==='tool' ? '🔧' : '🤖') }}</div>
              <div>
                <div v-if="m.role==='tool'" class="toolchip">{{ m.content }}</div>
                <div v-else class="vue-bubble" v-html="m.role==='assistant' ? md(m.content) : esc(m.content)"></div>
                <div v-if="m.role==='assistant'" class="msg-acts">
                  <button class="mini" @click="favContent(m.content, autoTitle)">⭐ 收藏</button>
                  <button class="mini" @click="exportIdx = (exportIdx===idx ? -1 : idx)">📥 导出</button>
                  <template v-if="exportIdx===idx">
                    <button class="mini" @click="exportContent(m.content, autoTitle, 'md')">MD</button>
                    <button class="mini" @click="exportContent(m.content, autoTitle, 'docx')">Word</button>
                    <button class="mini" @click="exportContent(m.content, autoTitle, 'pdf')">PDF</button>
                  </template>
                </div>
              </div>
            </div>
            <div v-if="sending" class="vue-msg assistant"><div class="who">🤖</div><div class="vue-bubble">思考中…</div></div>
          </div>
          <div style="display:flex;flex-wrap:wrap;gap:6px;padding:8px 0">
            <button v-for="s in suggestions" :key="s" class="chip" @click="pick(s)">{{ s }}</button>
          </div>
          <form @submit.prevent="sendMsg" style="display:flex;gap:8px">
            <input v-model="input" placeholder="输入你的旅行需求…" style="flex:1;padding:11px 14px;border:1.5px solid var(--border);border-radius:12px;font-size:14px" />
            <button class="btn primary" :disabled="!canSend">发送</button>
          </form>
        </div>
      </div>

      <!-- -------- 灵感库 -------- -->
      <div v-else-if="view==='favs'" class="vue-page">
        <div class="vue-head-row">
          <h2>💡 灵感库</h2>
          <span class="muted small">收藏 AI 推荐的行程片段，可随时导出</span>
        </div>
        <p class="hint">{{ favHint }}</p>
        <div v-if="!favs.length" class="card"><div class="empty-tip">还没有收藏，去对话里点「⭐ 收藏」吧</div></div>
        <div class="fav-grid">
          <div v-for="f in favs" :key="f.id" class="card fav-card">
            <div class="fav-head">
              <strong>📌 {{ f.title }}</strong>
              <span class="muted small">{{ fmtDate(f.createdAt) }}</span>
            </div>
            <div class="fav-body">{{ f.content }}</div>
            <div class="fav-acts">
              <button class="btn small" @click="exportContent(f.content, f.title, 'md')">导出 MD</button>
              <button class="btn small" @click="exportContent(f.content, f.title, 'docx')">Word</button>
              <button class="btn small" @click="exportContent(f.content, f.title, 'pdf')">PDF</button>
              <button class="btn small danger" @click="delFav(f.id)">删除</button>
            </div>
          </div>
        </div>
      </div>

      <!-- -------- 个人中心 -------- -->
      <div v-else-if="view==='profile'" class="vue-page">
        <div class="vue-head-row">
          <h2>👤 个人中心</h2>
          <span class="muted small">{{ user.username }} · 资料 / 密码 / 记忆</span>
        </div>
        <div class="stat-row">
          <span class="stat-chip">⭐ 偏好 {{ stats ? stats.preferences : prefs.length }}</span>
          <span class="stat-chip">💬 对话 {{ stats ? stats.conversations : convs.length }}</span>
          <span class="stat-chip">💡 收藏 {{ favs.length }}</span>
          <span class="stat-chip">🧠 记忆 {{ memory.length }}</span>
        </div>
        <div class="vue-grid2">
          <div class="v-card">
            <h3 style="margin-top:0">基本资料</h3>
            <div class="field"><label>昵称</label><input v-model="nickname" placeholder="怎么称呼你？" /></div>
            <div class="field">
              <label>头像（点选）</label>
              <div class="avatar-pick">
                <button type="button" v-for="a in avatars" :key="a" class="avatar-opt" :class="{active: avatar===a}" @click="avatar=a">{{ a }}</button>
                <button type="button" class="avatar-opt avatar-clear" :class="{active: !avatar}" @click="avatar=''">无</button>
              </div>
            </div>
            <div class="field"><label>邮箱</label><input v-model="profEmail" type="email" placeholder="you@example.com" /></div>
            <button class="btn primary small" :disabled="profBusy" @click="saveProfile">保存资料</button>
            <p class="hint">{{ profHint }}</p>
          </div>
          <div class="v-card">
            <h3 style="margin-top:0">修改密码</h3>
            <div class="field"><label>原密码</label><input v-model="oldPwd" type="password" placeholder="输入当前密码" /></div>
            <div class="field"><label>新密码（至少 6 位）</label><input v-model="newPwd" type="password" placeholder="输入新密码" /></div>
            <button class="btn primary small" @click="changePwd">确认修改</button>
            <p class="hint">{{ pwdHint }}</p>
          </div>
        </div>
        <div class="v-card" style="margin-top:14px">
          <h3 style="margin-top:0">🧠 长期记忆</h3>
          <p class="muted small">聊天中 AI 会自动沉淀目的地 / 预算 / 偏好等信息，让后续对话更懂你。</p>
          <div v-if="!memory.length" class="empty-tip">还没有记忆，多聊几次就会自动生成</div>
          <div v-for="m in memory" :key="m.id" class="mem-item">
            <span>🧠 {{ m.text }} <span class="muted small">{{ fmtDate(m.createdAt) }}</span></span>
            <button class="mini" @click="delMemory(m.id)">×</button>
          </div>
          <p class="hint">{{ memHint }}</p>
          <hr style="border:none;border-top:1px dashed var(--border);margin:14px 0" />
          <h4 style="margin:0 0 6px">忘记密码？</h4>
          <p class="muted small">提交申请后由管理员在后台批准并生成新密码。</p>
          <button class="btn small" @click="askReset">🔑 申请重置密码</button>
          <p class="hint">{{ resetHint }}</p>
        </div>
        <div class="v-card" style="margin-top:14px">
          <h3 style="margin-top:0">📮 意见反馈</h3>
          <p class="muted small">把使用中遇到的问题、建议或想加的功能发给管理员。</p>
          <div style="display:flex;gap:10px;align-items:center;margin-bottom:8px;flex-wrap:wrap">
            <label class="muted small">类型</label>
            <select v-model="feedback.category" style="padding:7px 10px;border:1.5px solid var(--border);border-radius:10px;font-size:13px">
              <option v-for="c in feedbackCats" :key="c" :value="c">{{ c }}</option>
            </select>
          </div>
          <textarea v-model="feedback.content" rows="3" maxlength="2000" placeholder="说说你的建议或遇到的问题…"
            style="width:100%;box-sizing:border-box;padding:10px 12px;border:1.5px solid var(--border);border-radius:10px;font-size:13px;font-family:inherit"></textarea>
          <div style="display:flex;gap:8px;align-items:center;margin-top:8px">
            <button class="btn primary small" :disabled="feedback.busy" @click="sendFeedback">{{ feedback.busy ? '提交中…' : '提交反馈' }}</button>
            <span class="hint" style="margin:0">{{ feedback.hint }}</span>
          </div>
          <div v-if="myFeedback.length" style="margin-top:12px;border-top:1px dashed var(--border);padding-top:10px">
            <h4 style="margin:0 0 8px">我提交过的反馈</h4>
            <div v-for="f in myFeedback" :key="f.id" class="mem-item" style="background:#f8fafc;border-color:var(--border)">
              <div style="flex:1;min-width:0">
                <div>
                  <span class="badge" :class="f.category==='问题反馈' ? 'banned' : 'user'">{{ f.category }}</span>
                  <span class="badge" :class="f.status==='done' ? 'active' : 'banned'">{{ f.status==='done' ? '已处理' : '待处理' }}</span>
                  <span class="muted small">{{ fmtDate(f.createdAt) }}</span>
                </div>
                <div style="margin-top:4px;white-space:pre-wrap">{{ f.content }}</div>
                <div v-if="f.reply" style="margin-top:4px;color:#065f46;background:#ecfdf5;border:1px solid #a7f3d0;border-radius:8px;padding:6px 8px">
                  <b>管理员回复：</b>{{ f.reply }}
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- -------- 管理后台 -------- -->
      <div v-else-if="view==='admin'" class="vue-page">
        <div class="vue-head-row">
          <h2>🛠 管理后台</h2>
          <span class="muted small">用户管理 / 重置申请 / 审计日志（仅管理员可见）</span>
        </div>
        <div class="kb-tabbar" style="max-width:460px">
          <button :class="{active: adminTab==='users'}" @click="adminTab='users';loadAdminUsers()">👥 用户管理</button>
          <button :class="{active: adminTab==='reset'}" @click="adminTab='reset';loadResetReqs()">🔑 重置申请{{ pendingResetCount ? ' (' + pendingResetCount + ')' : '' }}</button>
          <button :class="{active: adminTab==='feedback'}" @click="adminTab='feedback';loadAdminFeedback()">📮 反馈{{ pendingFeedbackCount ? ' (' + pendingFeedbackCount + ')' : '' }}</button>
          <button :class="{active: adminTab==='audit'}" @click="adminTab='audit';loadAudit()">📋 审计日志</button>
        </div>
        <p class="hint">{{ adminHint }}</p>

        <div v-if="adminTab==='users'" class="card" style="padding:0;overflow:hidden">
          <div class="card-head">
            <h2>👥 用户管理</h2>
            <button class="btn primary small" @click="adminNew.show = !adminNew.show">{{ adminNew.show ? '取消' : '+ 新增管理员' }}</button>
          </div>
          <div v-if="adminNew.show" style="padding:14px;border-bottom:1px solid var(--border)">
            <h3 style="margin:0 0 10px">新增管理员账号</h3>
            <div class="grid">
              <div class="field"><label>用户名 *</label><input v-model="adminNew.username" placeholder="3-20 位字母数字下划线" /></div>
              <div class="field"><label>密码 *</label><input v-model="adminNew.password" type="password" placeholder="至少 6 位" /></div>
              <div class="field"><label>邮箱（可选）</label><input v-model="adminNew.email" type="email" placeholder="you@example.com" /></div>
            </div>
            <button class="btn primary small" :disabled="adminNew.busy" @click="adminCreate">{{ adminNew.busy ? '创建中…' : '创建' }}</button>
            <p class="hint">{{ adminNew.hint }}</p>
          </div>
          <div class="table-wrap">
            <table>
              <thead><tr><th>用户名</th><th>邮箱</th><th>角色</th><th>状态</th><th>注册时间</th><th>操作</th></tr></thead>
              <tbody>
                <tr v-for="u in users" :key="u.id">
                  <td>{{ u.username }}<span v-if="u.id===user.id" class="muted small">（我）</span></td>
                  <td>{{ u.email || '—' }}</td>
                  <td><span class="badge" :class="u.role">{{ u.role==='admin' ? '管理员' : '用户' }}</span></td>
                  <td><span class="badge" :class="u.status==='banned' ? 'banned' : 'active'">{{ u.status==='banned' ? (u.banUntil ? '🚫 至 ' + (u.banUntil || '').slice(0,10) : '🚫 封禁') : '正常' }}</span></td>
                  <td>{{ fmtDate(u.createdAt) }}</td>
                  <td style="white-space:nowrap">
                    <template v-if="u.id!==user.id">
                      <button class="btn small" @click="adminRole(u)">{{ u.role==='admin' ? '降为用户' : '设为管理员' }}</button>
                      <button class="btn small" @click="adminResetPwd(u)">🔑 重置密码</button>
                      <button class="btn small" :class="u.status==='banned' ? '' : 'danger'" @click="adminBan(u)">{{ u.status==='banned' ? '解封' : '封禁' }}</button>
                      <button class="btn small danger" @click="adminDel(u)">删除</button>
                    </template>
                    <span v-else>—</span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <div v-else-if="adminTab==='reset'" class="card" style="padding:0;overflow:hidden">
          <div class="card-head"><h2>🔑 密码重置申请</h2></div>
          <div class="table-wrap">
            <table>
              <thead><tr><th>时间</th><th>申请人</th><th>原因</th><th>状态</th><th>操作</th></tr></thead>
              <tbody>
                <tr v-for="r in resetReqs" :key="r.id">
                  <td>{{ fmtDate(r.createdAt) }}</td>
                  <td>{{ r.username }}</td>
                  <td>{{ r.reason || '—' }}</td>
                  <td><span class="badge" :class="r.status==='pending' ? 'banned' : (r.status==='done' ? 'active' : 'user')">{{ r.status==='pending' ? '待处理' : (r.status==='done' ? '已批准' : '已拒绝') }}</span></td>
                  <td style="white-space:nowrap">
                    <template v-if="r.status==='pending'">
                      <button class="btn small" @click="adminApprove(r)">✓ 批准（生成新密码）</button>
                      <button class="btn small danger" @click="adminReject(r)">✕ 拒绝</button>
                    </template>
                    <span v-else>—</span>
                  </td>
                </tr>
                <tr v-if="!resetReqs.length"><td colspan="5" class="pref-empty">暂无重置申请</td></tr>
              </tbody>
            </table>
          </div>
        </div>

        <div v-else-if="adminTab==='feedback'" class="card" style="padding:0;overflow:hidden">
          <div class="card-head"><h2>📮 意见反馈 <span class="muted small">（{{ adminFeedback.length }} 条 · 待处理 {{ pendingFeedbackCount }}）</span></h2></div>
          <div class="table-wrap">
            <table>
              <thead><tr><th>时间</th><th>用户</th><th>类型</th><th>内容</th><th>状态 / 管理员回复</th><th>操作</th></tr></thead>
              <tbody>
                <tr v-for="f in adminFeedback" :key="f.id">
                  <td style="white-space:nowrap">{{ fmtDate(f.createdAt) }}</td>
                  <td>{{ f.username }}</td>
                  <td><span class="badge" :class="f.category==='问题反馈' ? 'banned' : 'user'">{{ f.category }}</span></td>
                  <td style="max-width:360px;white-space:pre-wrap">{{ f.content }}</td>
                  <td>
                    <span class="badge" :class="f.status==='done' ? 'active' : 'banned'">{{ f.status==='done' ? '已处理' : '待处理' }}</span>
                    <div v-if="f.reply" style="margin-top:4px;color:#065f46;white-space:pre-wrap">↩ {{ f.handler }}：{{ f.reply }}</div>
                    <div v-else-if="f.status==='done'" class="muted small">{{ f.handledAt ? fmtDate(f.handledAt) : '' }}</div>
                  </td>
                  <td style="white-space:nowrap">
                    <button class="btn small" @click="adminFeedbackDone(f)" :disabled="f.status==='done'">{{ f.status==='done' ? '已处理' : '回复并处理' }}</button>
                  </td>
                </tr>
                <tr v-if="!adminFeedback.length"><td colspan="6" class="pref-empty">还没有收到反馈</td></tr>
              </tbody>
            </table>
          </div>
        </div>

        <div v-else class="card" style="padding:0;overflow:hidden">
          <div class="card-head">
            <h2>📋 审计日志</h2>
            <button class="btn small danger" @click="adminClearAudit">🗑 清空日志</button>
          </div>
          <div class="table-wrap">
            <table>
              <thead><tr><th>时间</th><th>操作人</th><th>操作</th><th>对象</th><th>说明</th></tr></thead>
              <tbody>
                <tr v-for="a in audit" :key="a.id">
                  <td>{{ fmtDate(a.createdAt) }}</td>
                  <td>{{ a.actorName }} <span class="badge" :class="a.actorRole">{{ a.actorRole==='admin' ? '管理员' : '用户' }}</span></td>
                  <td>{{ a.action }}</td>
                  <td>{{ a.target }}</td>
                  <td>{{ a.detail || '—' }}</td>
                </tr>
                <tr v-if="!audit.length"><td colspan="5" class="pref-empty">暂无操作记录</td></tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  </div>`,
}).mount('#app');
