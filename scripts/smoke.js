'use strict';

/**
 * 冒烟测试：注册/登录/权限/AI 对话接口。
 * 会自动启动服务器于随机端口，测试完成后关闭。
 * 用法: npm test
 */

const http = require('http');

const HOST = '127.0.0.1';
let server;
let failures = 0;

function request(method, port, path, body, cookieHeader) {
  return new Promise((resolve, reject) => {
    const payload = body ? JSON.stringify(body) : null;
    const headers = {};
    if (payload) headers['Content-Type'] = 'application/json';
    if (cookieHeader) headers.Cookie = cookieHeader;
    const req = http.request(
      { host: HOST, port, path, method, headers },
      (res) => {
        let raw = '';
        res.on('data', (c) => (raw += c));
        res.on('end', () => {
          let data = null;
          try { data = JSON.parse(raw); } catch { /* ignore */ }
          const setCookie = res.headers['set-cookie'] || [];
          resolve({ status: res.statusCode, data, setCookie });
        });
      }
    );
    req.on('error', reject);
    if (payload) req.write(payload);
    req.end();
  });
}

function check(name, cond, extra = '') {
  if (cond) {
    console.log(`  ✔ ${name}`);
  } else {
    failures++;
    console.error(`  ✘ ${name} ${extra}`);
  }
}

function extractSession(setCookie) {
  const line = (setCookie[0] || '').split(';')[0];
  return line || '';
}

/** 读取 SSE 流式响应，返回 { status, events, errorBody } */
function sseRequest(port, path, body, cookieHeader) {
  return new Promise((resolve, reject) => {
    const payload = JSON.stringify(body);
    const req = http.request(
      {
        host: HOST, port, path, method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Cookie: cookieHeader || '',
          Accept: 'text/event-stream',
        },
      },
      (res) => {
        let raw = '';
        res.on('data', (c) => (raw += c));
        res.on('end', () => {
          if (res.statusCode !== 200) {
            let data = null;
            try { data = JSON.parse(raw); } catch { /* ignore */ }
            return resolve({ status: res.statusCode, data, events: [] });
          }
          const events = [];
          for (const block of raw.split('\n\n')) {
            for (const line of block.split('\n')) {
              if (!line.startsWith('data:')) continue;
              const val = line.slice(5).trim();
              if (!val) continue;
              try { events.push(JSON.parse(val)); } catch { /* ignore */ }
            }
          }
          resolve({ status: res.statusCode, events, data: null });
        });
      }
    );
    req.on('error', reject);
    req.write(payload);
    req.end();
  });
}

async function run() {
  server = require('../server').server;
  await new Promise((resolve) => server.listen(0, HOST, resolve));
  const port = server.address().port;
  console.log(`[smoke] 服务器已启动于 http://${HOST}:${port}\n`);

  const uname = `smoke_${Date.now().toString(36)}`;

  // 1. 注册
  console.log('用户注册');
  let r = await request('POST', port, '/api/auth/register', { username: uname, password: 'pass123' });
  check('注册成功 201', r.status === 201, `got ${r.status}`);
  let userCookie = extractSession(r.setCookie);
  check('注册后已建立会话', userCookie.startsWith('tp_session='));
  const uid = r.data && r.data.user && r.data.user.id;

  // 2. 登录
  console.log('登录');
  r = await request('POST', port, '/api/auth/login', { username: uname, password: 'pass123' });
  check('登录成功 200', r.status === 200, `got ${r.status}`);
  userCookie = extractSession(r.setCookie);
  r = await request('POST', port, '/api/auth/login', { username: uname, password: 'wrong-pass' });
  check('错误密码返回 401', r.status === 401, `got ${r.status}`);

  // 3. me
  console.log('会话');
  r = await request('GET', port, '/api/auth/me', null, userCookie);
  check('me 返回当前用户', r.status === 200 && r.data.user.username === uname, `got ${r.status}`);
  r = await request('GET', port, '/api/auth/me');
  check('未登录访问 me 返回 401', r.status === 401, `got ${r.status}`);

  // 4. 权限
  console.log('权限');
  r = await request('GET', port, '/api/admin/users', null, userCookie);
  check('普通用户访问管理员接口返回 403', r.status === 403, `got ${r.status}`);

  // 5. 用户偏好（向量数据库）
  console.log('用户偏好（向量库）');
  r = await request('GET', port, '/api/preferences');
  check('未登录访问偏好返回 401', r.status === 401, `got ${r.status}`);
  r = await request('POST', port, '/api/preferences', { text: '喜欢美食' }, userCookie);
  check('添加偏好 201', r.status === 201 && r.data.preference.text === '喜欢美食', `got ${r.status}`);
  const prefId = r.data && r.data.preference && r.data.preference.id;
  r = await request('POST', port, '/api/preferences', { text: '喜欢美食' }, userCookie);
  check('重复偏好返回 400', r.status === 400, `got ${r.status}`);
  r = await request('GET', port, '/api/preferences', null, userCookie);
  check('列表包含偏好', r.status === 200 && r.data.preferences.some((p) => p.id === prefId), `got ${r.status}`);
  r = await request('DELETE', port, `/api/preferences/${prefId}`, null, userCookie);
  check('删除偏好 200', r.status === 200, `got ${r.status}`);
  r = await request('GET', port, '/api/preferences', null, userCookie);
  check('删除后列表中已移除', r.status === 200 && !r.data.preferences.some((p) => p.id === prefId), `got ${r.status}`);

  // 6. 向量检索单元测试
  console.log('向量检索（单元）');
  {
    const fs2 = require('fs');
    const os = require('os');
    const tmpFile = require('path').join(os.tmpdir(), `vector-test-${Date.now()}.json`);
    const { VectorStore } = require('../lib/vector');
    const vs = new VectorStore(tmpFile);
    vs.add('u1', '喜欢美食和当地特色小吃');
    vs.add('u1', '偏好穷游，预算有限');
    vs.add('u1', '喜欢历史古迹');
    const hit1 = vs.search('u1', '美食之旅', 2);
    check('中文分词检索命中"美食"', hit1.length > 0 && hit1[0].text.includes('美食'), JSON.stringify(hit1));
    const hit2 = vs.search('u1', '怎么省钱穷玩', 2);
    check('相似度排序返回"穷游"', hit2.length > 0 && hit2[0].text.includes('穷游'), JSON.stringify(hit2));
    vs.remove('u1', vs.list('u1')[0].id);
    check('删除后数量减少', vs.list('u1').length === 2, `len=${vs.list('u1').length}`);
    fs2.rmSync(tmpFile, { force: true });
  }

  // 6.5 文件导入（TXT / PDF）
  console.log('文件导入（TXT / PDF）');
  {
    const zlib2 = require('zlib');
    const { extractPdfText } = require('../lib/pdf');
    const miniContent = Buffer.from('BT /F1 12 Tf 72 720 Td (I love food and travel) Tj ET', 'binary');
    const miniPdf = Buffer.from(`%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >> endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >> endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >> endobj
4 0 obj
<< /Length ${miniContent.length} /Filter /FlateDecode >>
stream
` + zlib2.deflateSync(miniContent).toString('binary') + `
endstream
endobj
5 0 obj
<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj
trailer << /Root 1 0 R >>
%%EOF`, 'binary');
    const pdfText = extractPdfText(miniPdf);
    check('PDF 提取器读取文本', pdfText.includes('I love food and travel'), JSON.stringify(pdfText));

    const txt = '我喜欢美食和当地小吃\n偏好穷游，预算有限\n喜欢历史古迹和博物馆';
    const b64 = Buffer.from(txt, 'utf8').toString('base64');
    r = await request('POST', port, '/api/preferences/import', { filename: 'prefs.txt', contentBase64: b64 }, userCookie);
    check('TXT 导入返回 201', r.status === 201, `got ${r.status} ${JSON.stringify(r.data)}`);
    check('TXT 导入提取出偏好', r.status === 201 && Array.isArray(r.data.added) && r.data.added.length > 0, JSON.stringify(r.data));
    r = await request('GET', port, '/api/preferences', null, userCookie);
    check('偏好库已包含导入内容', r.status === 200 && r.data.preferences.some((p) => p.text.includes('美食')), JSON.stringify(r.data.preferences.map((p) => p.text)));

    const pdfB64 = miniPdf.toString('base64');
    r = await request('POST', port, '/api/preferences/import', { filename: 'travel.pdf', contentBase64: pdfB64 }, userCookie);
    check('PDF 导入返回 201（含提取文本）', r.status === 201 && Array.isArray(r.data.added), `got ${r.status} ${JSON.stringify(r.data)}`);

    r = await request('POST', port, '/api/preferences/import', { filename: 'x.exe', contentBase64: b64 }, userCookie);
    check('不支持的文件类型返回 400', r.status === 400, `got ${r.status}`);
  }

  // 7. AI 对话接口
  console.log('AI 对话');
  r = await sseRequest(port, '/api/chat', { messages: [{ role: 'user', content: '你好' }] });
  check('未登录访问 /api/chat 返回 401', r.status === 401, `got ${r.status}`);

  r = await sseRequest(port, '/api/chat', { messages: [{ role: 'user', content: '帮我规划 3 天北京游' }] }, userCookie);
  const errEvent = r.events.find((e) => e.type === 'error');
  if (process.env.OPENAI_API_KEY) {
    check('已配置 Key 时对话返回内容', r.status === 200 && r.events.some((e) => e.type === 'delta'), `events=${r.events.map((e) => e.type).join(',')}`);
  } else {
    check('未配置 Key 时返回 503 提示（SSE error）', r.status === 200 && errEvent && /OPENAI_API_KEY/.test(errEvent.message || ''), JSON.stringify(errEvent || r));
  }

  r = await sseRequest(port, '/api/chat', { messages: [] }, userCookie);
  check('空消息返回错误提示', r.status === 200 && r.events.some((e) => e.type === 'error'), `events=${r.events.map((e) => e.type).join(',')}`);

  r = await request('GET', port, '/api/config', null, userCookie);
  check('config 返回 AI 状态', r.status === 200 && typeof r.data.chatEnabled === 'boolean', `got ${r.status}`);

  // 8. 注销
  console.log('注销');
  r = await request('POST', port, '/api/auth/logout', null, userCookie);
  check('注销成功 200', r.status === 200, `got ${r.status}`);
  r = await request('GET', port, '/api/auth/me', null, userCookie);
  check('注销后会话失效 401', r.status === 401, `got ${r.status}`);

  // 9. 管理员
  console.log('管理员');
  const adminCookie = extractSession((await request('POST', port, '/api/auth/login', { username: 'admin', password: 'admin123' })).setCookie);
  if (adminCookie) {
    r = await request('GET', port, '/api/admin/users', null, adminCookie);
    check('管理员查看用户列表 200', r.status === 200 && Array.isArray(r.data.users), `got ${r.status}`);
  } else {
    failures++;
    console.error('  ✘ 管理员 admin/admin123 登录失败（请先运行 npm run seed）');
  }

  // 8.5 清理测试用户（避免污染用户管理列表）
  console.log('清理测试用户');
  if (adminCookie && uid) {
    const fresh = await request('POST', port, '/api/auth/login', { username: uname, password: 'pass123' });
    const freshCookie = extractSession(fresh.setCookie);
    if (freshCookie) {
      r = await request('GET', port, '/api/preferences', null, freshCookie);
      if (r.status === 200) {
        for (const p of r.data.preferences) {
          await request('DELETE', port, `/api/preferences/${p.id}`, null, freshCookie);
        }
      }
    }
    r = await request('DELETE', port, `/api/admin/users/${uid}`, null, adminCookie);
    check('测试用户已清理', r.status === 200, `got ${r.status}`);
  }

  // 8. 静态页面
  console.log('静态前端');
  await new Promise((resolve, reject) => {
    http.get({ host: HOST, port, path: '/' }, (res) => {
      let html = '';
      res.on('data', (c) => (html += c));
      res.on('end', () => {
        check('首页返回 200', res.statusCode === 200, `got ${res.statusCode}`);
        check('首页包含聊天界面', html.includes('AI 旅行规划师'));
        resolve();
      });
    }).on('error', reject);
  });

  await new Promise((resolve) => server.close(resolve));
  console.log(`\n[smoke] ${failures === 0 ? '全部通过 ✔' : `${failures} 项失败 ✘`}`);
  process.exit(failures === 0 ? 0 : 1);
}

run().catch((err) => {
  console.error('[smoke] 运行出错:', err);
  if (server) server.close(() => process.exit(1));
  else process.exit(1);
});





