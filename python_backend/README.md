# Travel Planner — FastAPI 版（课程升级）

对应课程要求：**FastAPI + Pydantic 校验 / RAG 攻略知识库 / Function Calling 多工具并行 / 多 Agent 协作 / Redis 缓存 / Docker 部署 / Leaflet 地图**。

## 启动

```bash
# 安装依赖
pip install -r python_backend\requirements.txt

# 启动 FastAPI 后端（前端由它托管）
python python_backend\main.py
# 或 PyCharm：Run Configuration → Script path 指向 python_backend\main.py
```

浏览器打开 http://127.0.0.1:3000/（演示账号 admin/admin123、user/user123）。

## 与课程要求的对应

| 课程要求 | 实现 |
| ---- | ---- |
| FastAPI 后端 + Pydantic 参数验证 | `python_backend/main.py`，所有请求体用 Pydantic 模型校验 |
| RAG 旅行攻略知识库（W7） | `guide_store.py` + `/api/guides`：上传 TXT/PDF/MD → 分块 → TF-IDF 向量化 → 对话时检索引用 |
| Function Calling 多工具并行（W5） | `tools.py`：get_weather（真实）/ search_flights / search_hotels / search_attractions，工具并行执行 |
| 多 Agent 协作（W11） | `agents.py` + `/api/agents/plan`：天气/预算/行程 Agent 并行 + 总协调者汇总 |
| 地图集成（W6） | 前端 Leaflet + `/api/geocode` 地理编码，聊天地图面板显示目的地 |
| Redis 缓存（W13） | `cache.py`：天气/地理编码缓存，配置 `REDIS_URL` 用 Redis，否则内存兜底 |
| Docker（W14） | `Dockerfile` + `docker-compose.yml`（app + Redis，数据卷持久化） |

## 功能一览

- 登录/注册/个人中心（昵称/头像/改密码/统计）
- 智能对话：DeepSeek 流式输出 + Function Calling 自动调用天气/航班/酒店/景点工具（前端显示 🔧 工具徽章）
- 偏好向量库（分类+重要度）与 RAG 攻略知识库双路检索
- 多 Agent 一键规划（🧠 按钮）
- 旅行计划导出（Word/PDF/Markdown/文本）、实时天气、对话历史、Leaflet 地图
- 管理员用户管理

## 说明

- 航班/酒店为确定性模拟数据（演示工具调用链路）；天气与地理编码为真实数据
- Redis 可选：不配置 `REDIS_URL` 时自动回退内存缓存，功能不受影响
- 旧 Flask 版保留在 `python_backend/app.py`，可继续使用

### 真实数据
天气使用 Open-Meteo（真实）。酒店/景点接入高德 POI（`AMAP_KEY`，免费申请），航班可配置第三方接口（`FLIGHT_API_URL/KEY`）；未配置时自动回退内置演示数据并标注 source。

### RAG 重排与评测
检索加入 RRF 混合重排（向量 + BM25），运行评测：python python_backend/eval_rag.py（Recall@1 90%→95%）。

### 多 Agent 真实工具 + 长期记忆
多 Agent 并行预取天气/航班/酒店/景点工具；对话自动沉淀长期记忆并在后续注入。接口：/api/me/memory。
