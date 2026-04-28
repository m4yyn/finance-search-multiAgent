# 金融行业信息报告编写 Agent 助手项目规范

## 项目目标

本项目从零构建“金融行业信息报告编写 Agent 助手”。当前后端已具备 FastAPI 骨架、配置读取、PostgreSQL ORM、Redis 工具、JWT 鉴权、用户注册登录、聊天会话、流式 LLM 回复、知识库元数据与 Alembic 迁移基础。后续实现 Agent、RAG、报告生成或数据处理逻辑时，必须沿用现有分层关系，不得绕过 service/router/schema/core 边界。

## 目录约束

- `reference/` 仅作为代码逻辑参考资料，禁止被新项目直接导入、调用、复制或作为运行时依赖。
- 新后端代码统一放在 `backend/` 下。
- 除非用户明确要求，不修改 `reference/`、`data/` 中的内容；当前前端工程统一放在 `frontend/` 下。
- 后续新增代码应优先遵循 `backend/app/` 的分层结构，避免把业务逻辑写入入口文件。

## 后端技术栈

- Web 框架：FastAPI
- 配置管理：Pydantic Settings
- 数据库：PostgreSQL
- 缓存/队列基础设施：Redis
- 向量数据库：Milvus，Python SDK 使用 `pymilvus`
- LLM SDK：OpenAI
- 数据校验与 API 契约：Pydantic
- 测试：Pytest，FastAPI TestClient

## 后端目录结构

```text
backend/
  alembic/               # Alembic 迁移环境与版本文件
    env.py               # 动态读取 backend/.env 中的 DATABASE_URL，并注入 ORM metadata
    versions/            # 数据库迁移版本；模型变更必须新增迁移
  app/
    config/              # 配置文件与环境变量读取
    core/                # 数据库配置、会话管理、密钥哈希等基础设施层
    models/              # PostgreSQL 表结构定义
    router/              # FastAPI 路由注册与接口分组
    schemas/             # API 接口契约层，Pydantic schema
    service/             # 核心服务层
      deep_research/     # 后续 Agent 核心逻辑函数与 Deep Research 编排
  tests/                 # 后端测试
  .venv/                 # 后端专用 Python 虚拟环境，本地生成，不提交
frontend/
  src/api/               # 前端 API client，统一处理 JWT、错误与后端路径
  src/lib/               # 通用前端工具，例如 SSE parser
  src/features/          # auth/chat/knowledge 等业务组件与 hooks
  src/styles/            # 全局主题与布局样式
  e2e/                   # Playwright 端到端测试
```

## 后端系统结构与文件关系

后端调用链遵循以下方向：

```text
app/config/settings.py
  -> app/core/*                 # 数据库、Redis、JWT、OpenAI、Milvus 等基础设施
  -> app/models/*               # ORM 表结构，统一挂载到 app.core.database.Base.metadata
  -> app/schemas/*              # 请求与响应契约
  -> app/service/*              # 业务规则与数据库读写
  -> app/router/*               # HTTP 接口、依赖注入、状态码与异常转换
  -> app/main.py                # FastAPI 实例创建与路由挂载
```

- `app/core/database.py` 是 ORM 与迁移的中心：定义 `Base`、异步 engine、sessionmaker、`get_db()`。所有模型必须继承这里的 `Base`。
- `app/models/__init__.py` 必须导入所有 ORM 模型，确保 Alembic 在 `alembic/env.py` 中通过 `import app.models` 能拿到完整 metadata。
- `alembic/env.py` 必须从 `app.config.settings.get_settings()` 动态读取 `DATABASE_URL`/PostgreSQL 配置，不允许写死数据库地址。
- `app/schemas/*` 只定义 API 输入输出，不直接访问数据库。
- `app/service/*` 承载业务规则和数据库查询；router 不应直接堆叠复杂 SQL 或业务判断。
- `app/router/api.py` 只聚合 `/api/v1` 下的业务路由；`app/main.py` 负责挂载 `/health` 和带前缀的 API 路由。
- `app/core/redis_client.py` 提供 Redis 连接池与 `RedisCache`，认证会话、缓存和队列类能力都应优先复用该工具。
- `app/core/security.py` 提供 bcrypt 密码哈希与 python-jose JWT 编解码；认证相关代码不得在其他文件重复实现加密或 token 逻辑。
- `app/schemas/user.py` 是用户注册、登录、用户响应和 token 响应的标准 API 契约入口；`app/schemas/auth.py` 仅保留兼容重导出。
- `app/router/auth_router.py` 是认证接口的标准路由入口；`app/router/auth.py` 仅保留兼容重导出。
- `app/models/chat.py` 定义聊天会话与消息表；PG 保存完整历史，Redis 只保存短期上下文窗口；研究记录删除使用 `ChatSession.is_active=False` 软删除，不物理删除 `chat_messages`。
- `app/models/knowledge.py` 定义知识库与上传文档元数据表；PG 存储知识库、文件路径、文件状态和 chunk 数量，Milvus 存储后续 chunk 向量和 chunk metadata。
- `app/service/session_service.py` 负责聊天会话、研究记录软删除、消息持久化、Redis 短期记忆裁剪与 OpenAI messages 格式化；删除研究记录时必须同步清理 `chat:session:{session_id}:messages`。
- `app/service/chat_service.py` 负责聊天的完整流式编排，串联 ORM、Redis、LLM 与结构化 SSE 输出；`/chat/stream` 使用 `search_mode` 三态：`none` 纯 LLM，`local` 本地 RAG，`web` Bocha 联网搜索。纯 LLM 普通聊天必须注入 `ORDINARY_CHAT_SYSTEM_PROMPT`，限制其只服务金融研究、资料检索、报告写作和系统使用引导。
- `app/service/local_file_router_service.py` 负责本地搜索的文件意图识别：读取当前用户已成功入库的非敏感文件元数据，让 LLM 严格输出可校验 JSON，再路由到具体文件、知识库或全库检索。
- `app/service/llm_service.py` 是临时 OpenAI streaming 封装，后续可被 Agent/Deep Research 编排替换。
- `app/service/embedding_service.py` 封装 OpenAI embedding 调用，后续知识库 chunk 向量化和 Milvus 入库必须复用该入口。
- `app/service/milvus_service.py` 封装本地 Milvus collection、chunk 插入、dense vector 检索和删除操作；BM25/hybrid 检索后续放 retrieval 编排层。
- `app/service/docmind_service.py` 封装阿里云 DocMind 异步解析任务，负责提交、轮询和分页聚合 Markdown 结果。
- `app/service/xlsx_service.py` 负责 Excel 解析和切块，DocMind 失败时对 `.xlsx/.xlsm` 使用 openpyxl 本地回退。
- `app/service/document_service.py` 负责文档入库流水线，串联 PG `Document` 状态、解析、chunk、embedding 和 Milvus 写入。
- `app/service/retrieval_service.py` 负责知识库 dense vector 召回，支持单 KB 和多 KB 合并排序。
- 聊天 RAG 的引用对象由 `app/schemas/chat.py` 的 `ChatReference` 表达，并随最终 `done` SSE 事件返回给前端；增强 prompt 只传给 LLM，不写入 PG/Redis 历史。
- `app/service/search_service.py` 负责 Bocha 联网搜索和 Redis 短缓存；缓存 key 必须使用 `web_search:bocha:` 前缀，避免与本地 RAG、聊天 session 短期记忆混用。
- `app/router/knowledge_router.py` 是知识库创建、列表、删除和测试召回接口入口。
- `GET /api/v1/knowledge/bases/{kb_id}/stats` 用于验收和诊断 PG chunk_count 与 Milvus row_count 是否一致。
- `app/router/document_router.py` 是知识库文档上传、列表和删除接口入口，上传后用后台任务触发入库。
- `app/router/chat_router.py` 是聊天接口入口，发送消息接口使用结构化 JSON SSE。
- `app/router/search_router.py` 是联网搜索测试接口入口，当前提供 `POST /api/v1/search/web`。
- `frontend/src/api/` 必须与后端 `/api/v1` 契约保持同步；改后端 schema 或 router 时同步检查前端 types、API client 和 E2E mock。
- `frontend/src/lib/sse.ts` 是前端流式输出解析中心；改后端 SSE payload 时必须同步更新该 parser、chat UI 和测试。

## 模块索引

| 模块 | 职责 | 上游依赖 | 下游影响 |
| --- | --- | --- | --- |
| `backend/app/config/` | 读取 `.env` 和环境变量，产出统一 settings 对象 | `backend/.env`、部署环境变量、`backend/.env.example` | `core/*`、`alembic/env.py`、应用启动配置 |
| `backend/app/core/` | 基础设施层：数据库、Redis、JWT/密码、OpenAI、Milvus、会话工具 | `config/settings.py`、第三方 SDK | `models`、`service`、`router`、测试 fixture |
| `backend/app/models/` | PostgreSQL ORM 表结构，统一挂载到 `Base.metadata` | `core/database.py` | Alembic autogenerate、service 查询、数据库测试 |
| `backend/app/schemas/` | API 请求与响应契约，禁止返回敏感字段如 `hashed_password` | 接口需求、Pydantic | router `response_model`、service 入参、接口测试 |
| `backend/app/service/` | 业务规则、数据库读写、认证领域逻辑 | `models`、`schemas`、`core` | router 状态码转换、业务测试 |
| `backend/app/service/deep_research/` | 后续 Agent、Deep Research、RAG 与报告生成核心编排 | `core` 客户端、领域 service | router/API、任务队列、报告生成测试 |
| `backend/app/router/` | HTTP 边界：路由、依赖注入、鉴权依赖、异常与状态码 | `schemas`、`service`、`core` | `app/main.py` 路由挂载、TestClient 接口测试 |
| `backend/alembic/` | 数据库迁移环境和版本脚本 | `config/settings.py`、`models/__init__.py`、`Base.metadata` | 本机 PostgreSQL schema、迁移测试 |
| `backend/tests/` | 验证配置、ORM、迁移、Redis、安全工具、认证接口 | 应用代码与测试 fixture | 回归质量门槛，修改代码后必须同步更新 |

## 修改前阅读索引

- 改认证接口或用户 API：先读 `backend/app/schemas/user.py` -> `backend/app/core/security.py` -> `backend/app/core/redis_client.py` -> `backend/app/service/auth_service.py` -> `backend/app/router/auth_router.py` -> `backend/tests/test_auth.py`。
- 改用户表或数据库字段：先读 `backend/app/core/database.py` -> `backend/app/models/user.py` -> `backend/app/models/__init__.py` -> `backend/alembic/env.py` -> `backend/alembic/versions/*` -> `backend/tests/test_user_model.py` -> `backend/tests/test_alembic_config.py`。
- 改聊天会话、消息或流式回复：先读 `backend/app/models/chat.py` -> `backend/app/schemas/chat.py` -> `backend/app/service/session_service.py` -> `backend/app/service/chat_service.py` -> `backend/app/service/llm_service.py` -> `backend/app/router/chat_router.py` -> `backend/tests/test_chat_service.py` -> `backend/tests/test_chat_router.py`。
- 改聊天 RAG：先读 `backend/app/schemas/chat.py` -> `backend/app/schemas/knowledge.py` -> `backend/app/service/local_file_router_service.py` -> `backend/app/service/retrieval_service.py` -> `backend/app/service/chat_service.py` -> `backend/app/router/chat_router.py` -> `backend/tests/test_local_file_router_service.py` -> `backend/tests/test_chat_service.py` -> `backend/tests/test_chat_router.py` -> `backend/scripts/test_phase3_rag.py`。
- 改知识库、上传文档或检索契约：先读 `backend/app/models/knowledge.py` -> `backend/app/schemas/knowledge.py` -> `backend/app/service/docmind_service.py` -> `backend/app/service/xlsx_service.py` -> `backend/app/service/document_service.py` -> `backend/app/service/embedding_service.py` -> `backend/app/service/milvus_service.py` -> `backend/app/service/retrieval_service.py` -> `backend/app/router/knowledge_router.py` -> `backend/app/router/document_router.py` -> `backend/app/core/milvus.py` -> `backend/alembic/versions/*` -> `backend/tests/test_knowledge_model.py` -> `backend/tests/test_knowledge_schemas.py`。
- 改配置或环境变量：先读 `backend/.env.example` -> `backend/app/config/settings.py` -> 相关 `backend/app/core/*` 客户端 -> `backend/alembic/env.py` -> 对应测试。
- 改业务服务：先读对应 `schemas` 和 `models` -> `backend/app/service/*` -> 调用它的 `router` -> 对应测试。
- 改路由挂载或 API 前缀：先读 `backend/app/router/api.py` -> `backend/app/main.py` -> `backend/app/config/settings.py` -> 接口测试。
- 改前端 API 或页面：先读 `frontend/src/types.ts` -> `frontend/src/api/*` -> 对应 `frontend/src/features/*` -> `frontend/e2e/app.spec.ts`。
- 改 Agent/Deep Research 逻辑：先读 `backend/app/service/deep_research/` -> 需要的 `core` 客户端 -> 未来任务/报告相关 router 和测试；不得从 `reference/` 直接导入。

## 当前骨架运行方式

在 `backend/` 目录中使用专用虚拟环境：

```bash
source .venv/bin/activate
uvicorn app.main:app --reload
```

常用接口：

```text
GET /health
POST /api/v1/auth/register
POST /api/v1/auth/login
GET /api/v1/auth/me
POST /api/v1/auth/logout
POST /api/v1/chat/session
GET /api/v1/chat/sessions
POST /api/v1/chat/stream
DELETE /api/v1/chat/session/{session_id}
GET /api/v1/chat/session/{session_id}/messages
POST /api/v1/knowledge/bases
GET /api/v1/knowledge/bases
DELETE /api/v1/knowledge/bases/{kb_id}
GET /api/v1/knowledge/bases/{kb_id}/stats
POST /api/v1/knowledge/bases/{kb_id}/documents
GET /api/v1/knowledge/bases/{kb_id}/documents
DELETE /api/v1/knowledge/bases/{kb_id}/documents/{doc_id}
POST /api/v1/knowledge/retrieve
POST /api/v1/search/web
```

数据库迁移：

```bash
cd backend
source .venv/bin/activate
alembic upgrade head
```

验收测试：

```bash
cd backend
.venv/bin/python -m pytest tests/test_acceptance_smoke.py -q
.venv/bin/python -m pytest -q
```

`tests/test_acceptance_smoke.py` 覆盖真实 PostgreSQL 连接、Redis 读写、密码哈希、JWT、ORM 导入、Alembic 当前版本、`users` 表、4 个用户 schema、OpenAPI 文档中的 `/auth/*` 路径、CORS 预检，以及注册/重复注册/错密码/正确登录/无 token `/me`/带 token `/me`/数据库密码哈希。

聊天模块专项测试：

```bash
cd backend
.venv/bin/python -m pytest tests/test_chat_model.py tests/test_chat_schemas.py tests/test_session_service.py tests/test_chat_service.py tests/test_llm_service.py tests/test_chat_router.py -q
```

知识库元数据专项测试：

```bash
cd backend
.venv/bin/python -m pytest tests/test_knowledge_model.py tests/test_knowledge_schemas.py tests/test_alembic_config.py -q
.venv/bin/python -m pytest tests/test_embedding_service.py -q
.venv/bin/python -m pytest tests/test_milvus_service.py -q
.venv/bin/python -m pytest tests/test_docmind_service.py tests/test_xlsx_service.py tests/test_document_service.py tests/test_document_chunking.py -q
.venv/bin/python -m pytest tests/test_retrieval_service.py tests/test_knowledge_router.py tests/test_document_router.py -q
```

端到端脚本：

```bash
cd backend
.venv/bin/python scripts/test_llm.py
.venv/bin/python scripts/test_chat.py
.venv/bin/python scripts/test_phase3_rag.py
```

## 编码规范

- 入口文件 `app/main.py` 只负责创建 FastAPI 实例、注册路由与生命周期钩子。
- `app/config/` 只放配置读取与配置对象，不写业务流程。
- `app/core/` 只放基础设施能力，例如数据库连接、Redis 客户端、OpenAI 客户端、会话与密钥哈希。
- `app/models/` 只放 PostgreSQL ORM 模型定义。
- `app/schemas/` 只放请求/响应数据结构。
- `app/router/` 只放 HTTP 路由层，不直接写复杂业务逻辑。
- `app/service/` 放业务服务；Agent 与深度研究相关逻辑后续统一放入 `app/service/deep_research/`。
- 外部资源连接必须延迟初始化或显式初始化，不能让应用导入阶段依赖本地必须运行 PostgreSQL、Redis 或 Milvus。
- 不在代码中硬编码 API key、数据库密码或其他密钥；使用 `.env` 或部署环境变量。
- 新增行为应配套最小测试，至少覆盖导入、路由注册和核心契约。

## 联动修改规则

- 修改 `app/models/*`：
  - 同步更新 `app/models/__init__.py`
  - 新增或调整 Alembic migration
  - 增加/更新模型测试和迁移测试
- 修改 `app/config/settings.py` 或环境变量：
  - 同步更新 `backend/.env.example`
  - 检查 `app/core/database.py`、`app/core/redis_client.py`、`app/core/openai_client.py`、`app/core/milvus.py`
  - 检查 `alembic/env.py` 是否仍能读取正确数据库 URL
- 修改认证、JWT、密码或会话逻辑：
  - 同步检查 `app/schemas/user.py`、`app/core/security.py`、`app/core/redis_client.py`、`app/service/auth_service.py`、`app/router/auth_router.py`
  - 仅在兼容导出变化时更新 `app/schemas/auth.py` 和 `app/router/auth.py`
  - 更新注册、登录、鉴权、Redis session 与密码哈希测试
- 修改聊天会话、消息、Redis 短期记忆或 SSE：
 - 同步检查 `app/models/chat.py`、`app/schemas/chat.py`、`app/service/session_service.py`、`app/service/chat_service.py`、`app/service/llm_service.py`、`app/router/chat_router.py`
  - RAG 由 `search_mode="local"` 触发；纯聊天路径 `search_mode="none"` 不得调用 embedding、Milvus 或 retrieval，并且必须把 `ORDINARY_CHAT_SYSTEM_PROMPT` 作为发给 LLM 的第一条 system message
  - `ORDINARY_CHAT_SYSTEM_PROMPT` 只用于普通聊天；不得写入 PG/Redis，不得叠加到本地 RAG、网络搜索或未来 Deep Research 流程
  - 上传仍按 KnowledgeBase 分类；问答端不暴露高级筛选，具体文件/知识库选择由 `local_file_router_service.py` 自动识别
  - `search_mode="web"` 应调用 `search_service.py` 的 Bocha 搜索，使用 `WEB_SEARCH_PROMPT_TEMPLATE`，最终 `done.references` 返回 `source_type="web"` 的引用
  - Bocha 搜索缓存必须独立使用 `web_search:bocha:` key，不得写入 `chat:session:*:messages`
  - 删除研究记录必须使用 `is_active=False` 软删除，并清理对应 Redis 短期记忆；不得默认物理删除 PG `chat_messages`
  - RAG 增强 prompt 不得写入 PG/Redis，PG/Redis 只保存用户原始问题和 assistant 回答
  - `ChatSSEChunk.references` 只在最终 `done` 事件中返回，编号必须与 prompt 中 `[编号]` 一致
  - 模型字段变化必须新增 Alembic migration
  - 更新模型、schema、service、LLM stream 和 chat router 测试
- 修改知识库、上传文档、文件类型或检索契约：
  - 同步检查 `app/models/knowledge.py`、`app/schemas/knowledge.py`、`app/service/docmind_service.py`、`app/service/xlsx_service.py`、`app/service/document_service.py`、`app/service/embedding_service.py`、`app/service/milvus_service.py`、`app/service/retrieval_service.py`、`app/router/knowledge_router.py`、`app/router/document_router.py`、`app/core/milvus.py`
  - 模型字段变化必须新增 Alembic migration
  - 文件类型支持变化必须检查上传校验、解析服务、Milvus chunk metadata 与 `DocumentResponse`
  - `Document.file_path` 是后端内部字段，不应直接暴露到公开 API
- 修改 API schema：
  - 同步检查对应 router 的 request/response_model
  - 同步检查 service 入参与返回值
  - 保持接口错误状态码一致
- 修改 router：
  - 确认是否需要在 `app/router/api.py` 注册
  - 确认 `app/main.py` 的前缀挂载不被破坏
  - 添加 FastAPI TestClient 覆盖
- 修改 Alembic：
  - 确认 `alembic heads` 只有预期 head
  - 确认 `alembic upgrade head` 可在目标数据库执行
  - 确认迁移结果与 ORM 模型一致

## 环境变量约定

优先使用 `backend/.env`，不要提交真实密钥。示例变量放在 `backend/.env.example`：

- `APP_NAME`
- `APP_ENV`
- `DEBUG`
- `API_V1_PREFIX`
- `POSTGRES_HOST`
- `POSTGRES_PORT`
- `POSTGRES_USER`
- `POSTGRES_PASSWORD`
- `POSTGRES_DB`
- `DATABASE_URL`
- `REDIS_HOST`
- `REDIS_PORT`
- `REDIS_PASSWORD`
- `MILVUS_DB_PATH`
- `OPENAI_API_KEY`
- `OPENAI_BASE_URL`
- `LLM_MODEL`
- `EMBEDDING_MODEL`
- `EMBEDDING_DIM`
- `DOCMIND_ACCESS_KEY_ID`
- `DOCMIND_ACCESS_KEY_SECRET`
- `BOCHA_API_KEY`
- `JWT_SECRET_KEY`
- `JWT_ALGORITHM`
- `JWT_ACCESS_TOKEN_EXPIRE_MINUTES`
- `UPLOAD_DIR`
