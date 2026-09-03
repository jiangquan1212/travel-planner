# Travel Planner — AI 旅行规划师（FastAPI + DeepSeek + RAG + Vue 3）

告诉 AI 你的旅行偏好和预算，它自动查询**真实天气、航班、酒店、景点**，结合你的
**个人偏好库 + 攻略知识库（RAG）** 生成多方位旅行计划。前端为 **Vue 3 单页应用**
（唯一前端，旧版页面已移除）。

## 功能总览

### 用户端
- 注册 / 登录 / 退出（会话令牌支持多标签页独立登录）
- **智能对话**：SSE 流式输出、Markdown 排版、Function Calling 多工具并行
  （天气 / 航班 / 酒店 / 景点，页面显示 🔧 工具徽章）
- **我的知识库（RAG）**：
  - ⭐ 旅行偏好：手动添加 / 删除，重要度加权
  - 📄 偏好导入：上传 PDF / TXT，AI 提取或自动分句后入库
  - 📄 攻略上传：上传攻略文档，对话时按相关度一并检索引用
  - 检索采用「向量召回 + BM25 + RRF 混合重排」
- **会话历史**：自动保存、新建 / 切换 / 删除，同一会话连续追问自动续写
- **灵感库**：收藏 AI 回答、卡片管理、导出
- **导出**：MD / TXT / Word / PDF
- **实时天气**：任意城市实时 + 未来 5 天预报，「结合天气帮我规划」（失败自动重试）
- **多 Agent 规划**：天气 / 预算 / 行程 Agent 并行规划后总协调汇总
- **个人中心**：昵称 / Emoji 头像点选 / 邮箱、修改密码、长期记忆管理、
  忘记密码可向管理员提交重置申请；偏好 / 对话 / 收藏 / 记忆统计
- **📮 意见反馈**：提交建议 / 问题 / 功能需求给管理员，可查看处理状态与管理员回复

### 管理端（管理员登录后顶部「🛠 管理」）
- 用户管理：新增管理员、设为管理员 / 降为用户、**封禁（可定时）/ 解封**、
  重置密码（可自动生成）、删除用户
- 密码重置申请：批准（自动生成新密码）/ 拒绝，待处理角标
- 📮 意见反馈：查看全部反馈、回复用户并标记已处理，待处理角标
- 审计日志：记录敏感管理操作，可一键清空

## 技术栈

| 模块 | 技术 |
| --- | --- |
| 后端 | FastAPI + Pydantic（`python_backend/main.py`） |
| AI | DeepSeek（OpenAI 兼容）/ Function Calling / 多 Agent |
| 检索 | 偏好 + 攻略向量库（`vector_store.py` / `guide_store.py`），RRF 混合重排 |
| 真实数据 | 天气 Open-Meteo（免 Key）；酒店 / 景点高德 AMAP（配置 `AMAP_KEY`）；航班可配第三方 |
| 前端 | Vue 3.5 全局构建（`public/vue-app/`，免编译、离线可用） |
| 存储 | JSON（默认）或 SQLite（`TP_DB_FILE` 启用）；Redis 缓存可选 |
| 部署 | Dockerfile + docker-compose（app + Redis），`/api/health` 健康检查 |

## 快速开始

```powershell
cd travel-planner
python -m venv .venv                     # 首次创建虚拟环境（需 Python 3.10+）
.\.venv\Scripts\python.exe -m pip install -r python_backend\requirements.txt
.\.venv\Scripts\python.exe python_backend\main.py
```

- 前端页面：<http://127.0.0.1:3000/>
- AI 需要根目录 `.env` 配置（见 `.env.example`）：

```env
OPENAI_API_KEY=sk-你的DeepSeek密钥
OPENAI_BASE_URL=https://api.deepseek.com
TP_OPENAI_MODEL=deepseek-chat
AMAP_KEY=你的高德Web服务Key   # 可选：酒店/景点用高德真实数据
```

账号：
- 普通用户：`user / user123`
- 管理员：`admin`（本机密码以你实际设置为准；忘记可用 `python_backend` 的
  `hash_password` 脚本重置，或走「忘记密码 → 管理员重置」流程）

## 项目结构

```
python_backend/        # FastAPI 后端（main.py / auth / tools / agents / vector_store / ...）
public/vue-app/        # Vue 3 唯一前端（index.html + app.js）
public/css/style.css   # 公共样式
public/vendor/vue/     # 本地 Vue 3 构建（离线可用）
data/                  # JSON 数据（users/sessions/conversations/preferences/guides/...）
outputs/               # 文档：README / 部署上线指南 / 里程碑说明 / PPT / 报告
work/                  # 开发与自动化验证脚本（Playwright/Python）
.env                   # DeepSeek / 高德等密钥（勿外传）
```

> 早期 Node 原型（`server.js` / `lib/` / `scripts/`）与 Flask 版
> （`python_backend/app.py`）为课程演进遗留代码，与当前 FastAPI + Vue 3 不冲突；
> 不需要时可自行删除，不影响运行。

## 主要接口

- 认证：`POST /api/auth/register|login|logout`、`GET /api/auth/me`
- 对话：`POST /api/chat`（SSE 流式）、`POST /api/agents/plan`（多 Agent）
- 知识库：`/api/preferences`（增删改 + `POST /api/preferences/import`）、`/api/guides`
- 会话历史：`/api/conversations`、收藏：`/api/favorites`、导出：`POST /api/export`
- 天气：`GET /api/weather?city=杭州`
- 个人：`/api/me`（GET/PATCH）、`/api/me/password`、`/api/me/memory`、
  `/api/me/reset-request`、`/api/me/feedback`（GET/POST）
- 管理：`/api/admin/users|register|reset-requests|feedback|audit`
- 健康检查：`GET /api/health`

## 测试与验证

- 后端：`python -m pytest python_backend/tests`（24 项通过）
- RAG 评测：`python python_backend/eval_rag.py`
- 前端真机浏览器验证（Playwright + Edge）：`work/vue3-check.js`、
  `work/vue3-m2-check.js`、`work/vue3-admin-check.js`、`work/vue3-tools-check.js`

## 部署

详见 `outputs/部署上线指南.md`；根目录提供 `Dockerfile` / `docker-compose.yml`
（app + Redis，支持一键 `docker compose up -d --build`）。
