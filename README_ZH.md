# 金融行业信息助手

这是一个面向金融行业研究、资料检索、报告生成和多 Agent 深度研究的全栈项目。系统包含 FastAPI 后端、React/Vite 前端、PostgreSQL 元数据存储、Redis 短期上下文缓存、Milvus 向量检索、OpenAI 模型调用、Bocha 联网搜索，以及 Deep Research 多 Agent 报告生成流程。

## 功能特性

- 用户注册、登录、JWT 鉴权和会话管理。
- 基于 SSE 的流式聊天回复。
- 三种聊天搜索模式：
  - `none`：普通金融研究问答。
  - `local`：基于上传文档的本地 RAG。
  - `web`：基于 Bocha API 的联网搜索。
- 知识库创建、文件上传、文档解析、chunk 入库和检索。
- 使用 Milvus dense vector search 召回本地文档片段。
- 长期记忆：PostgreSQL 保存记忆摘要，Milvus 保存记忆向量。
- Deep Research 多 Agent 流程：
  - Architect：研究规划与大纲生成。
  - Scout：联网/本地证据搜索与事实抽取。
  - DataAnalyst：结构化数据、知识图谱和 ECharts 图表配置。
  - Wizard：运行 Python 数据分析代码，生成报告静态图表。
  - Writer：金融研究报告分章节撰写、整合和修订。
  - Critic：报告质量审核、合规检查、补搜/修订路由。
- Deep Research checkpoint 保存与恢复。
- 前端工作台展示聊天、Deep Research 进度、知识图谱、图表和最终报告。

## 技术栈

### 后端

- FastAPI
- Pydantic Settings
- SQLAlchemy Async ORM
- PostgreSQL
- Redis
- Milvus / Milvus Lite
- OpenAI Python SDK
- Alembic
- Pytest

### 前端

- React
- TypeScript
- Vite
- ECharts
- Vitest
- Playwright

## 一键启动

推荐使用根目录脚本启动本地开发环境：

```bash
./scripts/start_dev.sh
```

脚本会执行：

1. 如果缺少 `backend/.env`，从 `backend/.env.example` 创建。
2. 如果缺少 `frontend/.env`，从 `frontend/.env.example` 创建。
3. 交互式配置 API key，并写入本地 env 文件。
4. 自动生成本地 `JWT_SECRET_KEY`。
5. 如果缺少 `backend/.venv`，创建 Python 虚拟环境。
6. 安装后端和前端依赖，除非传入 `--skip-install`。
7. 执行 Alembic 数据库迁移，除非传入 `--no-migrate`。
8. 同时启动后端 FastAPI 和前端 Vite。

启动后访问：

- 前端：<http://localhost:5173>
- 后端 API：<http://localhost:8000/api/v1>
- 健康检查：<http://localhost:8000/health>
- OpenAPI 文档：<http://localhost:8000/docs>

### 非交互方式配置 API key

也可以通过环境变量直接传入：

```bash
OPENAI_API_KEY="sk-..." \
BOCHA_API_KEY="..." \
DOCMIND_ACCESS_KEY_ID="..." \
DOCMIND_ACCESS_KEY_SECRET="..." \
./scripts/start_dev.sh
```

常用参数：

```bash
./scripts/start_dev.sh --skip-install
./scripts/start_dev.sh --no-migrate
./scripts/start_dev.sh --skip-install --no-migrate
```

## 手动启动后端

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cp .env.example .env
alembic upgrade head
uvicorn app.main:app --reload
```

本地依赖服务：

- PostgreSQL：默认 `localhost:5432`
- Redis：默认 `localhost:6379`

Milvus Lite 使用 `MILVUS_DB_PATH` 指定的本地文件路径。

## 手动启动前端

```bash
cd frontend
npm install
cp .env.example .env
npm run dev
```

## 环境变量说明

重要后端变量：

- `OPENAI_API_KEY`：模型调用和 embedding 必需。
- `OPENAI_BASE_URL`：默认 `https://api.openai.com/v1`。
- `LLM_MODEL`：默认聊天和 Agent 模型。
- `EMBEDDING_MODEL`：默认 embedding 模型。
- `BOCHA_API_KEY`：联网搜索必需。
- `DOCMIND_ACCESS_KEY_ID` / `DOCMIND_ACCESS_KEY_SECRET`：阿里云 DocMind 文档解析，可选。
- `DATABASE_URL` 或 PostgreSQL 主机、用户、密码、数据库字段。
- `REDIS_HOST`、`REDIS_PORT`、`REDIS_PASSWORD`。
- `JWT_SECRET_KEY`。

重要前端变量：

- `VITE_API_BASE_URL`：默认 `http://localhost:8000/api/v1`。

不要提交本地 `.env` 文件或真实 API key。

## 常用命令

后端测试：

```bash
cd backend
.venv/bin/python -m pytest -q
```

前端测试：

```bash
cd frontend
npm test -- --run
npm run build
npm run e2e
```

数据库迁移：

```bash
cd backend
source .venv/bin/activate
alembic upgrade head
```

## API 概览

业务 API 统一挂载在 `/api/v1` 下。

- `POST /auth/register`
- `POST /auth/login`
- `GET /auth/me`
- `POST /chat/session`
- `GET /chat/sessions`
- `POST /chat/stream`
- `POST /deep-research/stream`
- `POST /knowledge/bases`
- `GET /knowledge/bases`
- `POST /knowledge/bases/{kb_id}/documents`
- `POST /knowledge/retrieve`
- `POST /search/web`
- `POST /memories/create`
- `POST /memories/search`

## 说明

- `reference/` 只作为历史参考资料，不参与运行时依赖。
- Deep Research checkpoint 与普通聊天消息分离保存。
- 当前本地 RAG 使用 Milvus dense vector search，默认不是 BM25。
- 系统定位为金融信息解释、资料检索、研究整理和报告写作工具，不提供投资建议、买卖评级、目标价或收益承诺。

英文说明见 [README.md](README.md)。
