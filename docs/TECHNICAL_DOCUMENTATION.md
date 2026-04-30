# 金融行业信息报告编写 Agent 助手技术文档

> 本文档面向后续开发者、技术评审者和系统维护者，系统性说明本项目的总体设计思路、技术栈、后端分层、前端实现、RAG/记忆/联网检索能力，以及 Deep Research 多 Agent 架构的状态管理、运行逻辑、流式输出和持久化方案。

## 1. 项目概述

### 1.1 项目定位

本项目是一个面向金融行业研究、资料检索、知识库问答和行业/公司报告生成的 Agent 助手系统。系统不是通用聊天机器人，而是围绕金融研究工作流构建的专业信息助手，核心目标包括：

- 支持用户进行普通金融研究问答。
- 支持本地知识库上传、解析、向量化和 RAG 检索问答。
- 支持联网搜索公开信息，并将搜索结果作为有引用依据的回答上下文。
- 支持长期记忆，沉淀用户研究偏好、关注领域和历史研究上下文。
- 支持 Deep Research 多 Agent 流程，从研究规划、检索、数据分析、图表生成、报告写作到质量审核，输出结构化研究报告。
- 支持 Deep Research 阶段性状态保存和恢复，便于长流程中断后继续执行或恢复 UI 展示。

系统整体采用前后端分离架构：

- 后端：FastAPI + PostgreSQL + Redis + Milvus + OpenAI SDK。
- 前端：React + TypeScript + Vite + ECharts + SSE 流式交互。

### 1.2 核心设计原则

项目实现遵循以下原则：

- **分层清晰**：后端严格区分 `config/core/models/schemas/service/router`，避免把业务逻辑写入入口文件或路由层。
- **普通聊天与 Deep Research 隔离**：普通聊天消息由 `chat_service.py` 和 `session_service.py` 持久化；Deep Research 的中间状态只保存在 checkpoint，不写入普通 `chat_messages`。
- **金融领域限定**：普通聊天、联网搜索、本地 RAG、Deep Research Agent prompt 均围绕金融信息解释、行业研究、公司经营分析、风险识别和报告写作设计。
- **可追溯事实与来源**：RAG、联网搜索和 Deep Research 都强调来源引用、事实提取、数据点归因和风险限制。
- **非 LangGraph 依赖的 Agent 编排**：Deep Research 借鉴 LangGraph 的状态机思想，但使用手写 `DeepResearchGraph` 实现 Agent 调度、阶段流转和流式输出。
- **阶段性流式反馈**：每个 Agent 的执行动作都会进入 `agent_events`，并通过 `_message_queue` 实时推送到前端 SSE。
- **可恢复工作记忆**：Deep Research 的 `ResearchState` 是全局工作记忆，阶段结束后写入 `deep_research_checkpoints`，并生成面向前端恢复的 `ui_state_json`。

## 2. 总体架构

### 2.1 目录结构

```text
backend/
  alembic/
    env.py
    versions/
  app/
    config/
      settings.py
    core/
      database.py
      redis_client.py
      security.py
      openai_client.py
      milvus.py
    models/
      user.py
      chat.py
      knowledge.py
      deep_research.py
    schemas/
      user.py
      chat.py
      knowledge.py
      search.py
      memory.py
      deep_research.py
    router/
      api.py
      auth_router.py
      chat_router.py
      knowledge_router.py
      document_router.py
      search_router.py
      memory_router.py
      deep_research_router.py
    service/
      auth_service.py
      session_service.py
      chat_service.py
      search_service.py
      document_service.py
      retrieval_service.py
      memory_service.py
      checkpoint_service.py
      deep_research/
        state.py
        base.py
        graph.py
        service.py
        agents/
          architect.py
          scout.py
          data_analyst.py
          wizard.py
          writer.py
          critic.py
  tests/

frontend/
  src/
    api/
    lib/
    features/
      auth/
      chat/
      knowledge/
      deepResearch/
    styles/
    types.ts
  e2e/
```

### 2.2 后端调用链

后端整体调用方向如下：

```text
app/config/settings.py
  -> app/core/*
  -> app/models/*
  -> app/schemas/*
  -> app/service/*
  -> app/router/*
  -> app/main.py
```

含义：

- `settings.py` 读取 `.env` 和环境变量，提供统一配置。
- `core/` 封装基础设施，包括数据库、Redis、JWT、Milvus、OpenAI 客户端等。
- `models/` 定义 SQLAlchemy ORM 模型。
- `schemas/` 定义 Pydantic API 契约。
- `service/` 承载业务流程、数据库读写、Agent 逻辑、RAG、记忆和 checkpoint。
- `router/` 负责 HTTP 边界、鉴权依赖、状态码和异常转换。
- `main.py` 创建 FastAPI 应用，挂载 CORS、生命周期和路由。

### 2.3 前端调用链

前端整体调用方向如下：

```text
src/main.tsx
  -> App.tsx
  -> AuthProvider / ProtectedRoute
  -> WorkspacePage
  -> api/*
  -> lib/sse.ts
  -> deepResearch/* / knowledge/* / chat/*
```

前端页面的核心是 `WorkspacePage`：

- 左侧为研究记录和知识库入口。
- 中间为聊天对话区。
- Deep Research 模式下右侧出现独立 `DeepResearchWorkspace`，用于展示 Agent 执行过程、知识图谱、图表、报告草稿和引用。
- 最终报告作为 assistant 结果返回到聊天区；过程信息不会塞入聊天气泡。

## 3. 技术栈

### 3.1 后端技术栈

| 类型 | 技术 | 用途 |
| --- | --- | --- |
| Web 框架 | FastAPI | HTTP API、SSE 流式接口、依赖注入 |
| ASGI Server | Uvicorn | 本地开发和服务运行 |
| 配置管理 | Pydantic Settings | `.env` 与环境变量读取 |
| ORM | SQLAlchemy Async | PostgreSQL 异步访问 |
| 迁移 | Alembic | 数据库 schema 版本管理 |
| 数据库 | PostgreSQL | 用户、会话、消息、知识库元数据、checkpoint |
| 缓存 | Redis | 聊天短期记忆、联网搜索缓存 |
| 向量数据库 | Milvus / pymilvus | 本地知识库 chunk 向量、长期记忆向量 |
| LLM SDK | OpenAI Python SDK | 普通聊天、Agent 调用、Embedding |
| 文档解析 | 阿里云 DocMind | PDF 解析 |
| 表格解析 | openpyxl | Excel 文件解析与 fallback |
| 图表生成 | matplotlib / seaborn | Wizard 生成报告静态 PNG 图表 |
| Token 计算 | tiktoken | 聊天上下文裁剪、文档分块 |
| 测试 | Pytest / HTTPX | 服务、路由、Agent、迁移和验收测试 |

### 3.2 前端技术栈

| 类型 | 技术 | 用途 |
| --- | --- | --- |
| UI 框架 | React 19 | 前端组件与状态管理 |
| 类型系统 | TypeScript | API 契约和 UI 数据类型 |
| 构建工具 | Vite | 本地开发、构建 |
| 图表 | ECharts | 交互图表、知识图谱 force graph |
| 图标 | lucide-react | 工具栏、导航、状态图标 |
| 路由 | react-router-dom | 登录与工作区路由 |
| 测试 | Vitest / Testing Library | 单元测试、组件测试 |
| E2E | Playwright | 端到端工作流测试 |
| 流式解析 | 自研 SSE parser | 解析后端 `data: {json}\n\n` 事件 |

## 4. 配置与基础设施

### 4.1 Settings 配置

配置入口为 `backend/app/config/settings.py`，使用 `BaseSettings` 读取环境变量。核心配置包括：

- 应用：`APP_NAME`、`APP_ENV`、`DEBUG`、`API_V1_PREFIX`。
- PostgreSQL：`POSTGRES_HOST`、`POSTGRES_PORT`、`POSTGRES_USER`、`POSTGRES_PASSWORD`、`POSTGRES_DB`、`DATABASE_URL`。
- Redis：`REDIS_HOST`、`REDIS_PORT`、`REDIS_PASSWORD`。
- Milvus：`MILVUS_DB_PATH`。
- OpenAI：`OPENAI_API_KEY`、`OPENAI_BASE_URL`、`LLM_MODEL`、`EMBEDDING_MODEL`、`EMBEDDING_DIM`。
- Bocha：`BOCHA_API_KEY`。
- JWT：`JWT_SECRET_KEY`、`JWT_ALGORITHM`、`JWT_ACCESS_TOKEN_EXPIRE_MINUTES`。
- 上传目录：`UPLOAD_DIR`。

### 4.2 数据库基础设施

`backend/app/core/database.py` 提供：

- `Base`：所有 ORM 模型共享的 declarative base。
- `create_database_engine()`：创建 async SQLAlchemy engine。
- `get_database_engine()`：`lru_cache` 缓存 engine。
- `get_sessionmaker()`：创建异步 session factory。
- `get_db()`：FastAPI 依赖，每个请求 yield 一个 `AsyncSession`。

应用关闭时，`main.py` 的 lifespan 会释放数据库 engine 和 Redis 连接池，避免测试或热重载期间资源泄漏。

### 4.3 Redis 基础设施

Redis 主要用于：

- 聊天短期上下文窗口：`chat:session:{session_id}:messages`。
- Bocha 联网搜索缓存：`web_search:bocha:{hash}`。

短期记忆和联网搜索缓存使用不同 key 前缀，避免混用。

### 4.4 安全基础设施

`backend/app/core/security.py` 提供：

- 密码哈希与校验。
- JWT 创建与解码。
- 当前用户鉴权依赖由 `auth_router.py` 暴露给其他 router 复用。

路由层通过 `get_current_user_required` 获取当前登录用户，所有用户数据查询都按 `user_id` 隔离。

## 5. 数据模型设计

### 5.1 用户模型

`User` 模型存储：

- 用户名、邮箱、密码哈希。
- 激活状态、超级用户标记。
- 创建/更新时间。

认证接口包括：

- `POST /api/v1/auth/register`
- `POST /api/v1/auth/login`
- `GET /api/v1/auth/me`
- `POST /api/v1/auth/logout`

### 5.2 聊天会话与消息

`ChatSession`：

- `id`
- `user_id`
- `title`
- `is_active`
- `created_at`
- `updated_at`

`ChatMessage`：

- `id`
- `session_id`
- `role`: `user | assistant | system`
- `content`
- `tokens`
- `created_at`

普通聊天消息完整保存在 PostgreSQL，Redis 只保存裁剪后的短期上下文窗口。删除研究记录时使用 `ChatSession.is_active=False` 软删除，同时清理 Redis 短期记忆，不物理删除 `chat_messages`。

### 5.3 知识库与文档

`KnowledgeBase`：

- 用户拥有的知识库元数据。
- `collection_name` 对应 Milvus collection。

`Document`：

- 上传文件元数据。
- `file_path` 为后端内部路径，不直接暴露给前端。
- `status`: `pending | processing | success | failed`
- `chunk_count`
- `error_message`

文档解析成功后，chunk 内容和向量写入 Milvus；PG 只保存文档元数据和 chunk 数量。

### 5.4 长期记忆

`LongTermMemory`：

- `user_id`
- `session_id`
- `summary`
- `key_insights`
- `milvus_ids`
- `token_count`

长期记忆的摘要存储在 PostgreSQL，向量存储在 Milvus 独立 collection `long_term_memories` 中。聊天时只把记忆作为上下文注入 prompt，不把 memory context 写回普通聊天消息。

### 5.5 Deep Research Checkpoint

`DeepResearchCheckpoint` 表名为 `deep_research_checkpoints`，字段包括：

- `user_id`
- `session_id`
- `query`
- `phase`
- `iteration`
- `max_iterations`
- `state_json`
- `ui_state_json`
- `final_report`
- `status`
- `error_message`
- `created_at`
- `updated_at`

设计要点：

- `session_id` 唯一，表示每个 Deep Research 会话只保留最新 checkpoint。
- 外键绑定 `users.id` 和 `chat_sessions.id`。
- 只保存 Deep Research state，不读取或写入普通聊天消息历史。
- 所有 checkpoint 查询均按 `user_id + session_id` 隔离。

## 6. 普通聊天与搜索/RAG 流程

### 6.1 聊天接口

普通聊天流式接口：

```text
POST /api/v1/chat/stream
```

请求使用 `ChatStreamRequest`：

- `session_id`
- `content`
- `search_mode`: `none | local | web`

响应是 SSE：

- `delta`: LLM 增量文本。
- `done`: 结束事件，返回 `message_id` 和引用来源。
- `error`: 错误事件。

### 6.2 search_mode=none

纯普通聊天模式：

- 从 PostgreSQL/Redis 获取短期历史。
- 查询长期记忆 context。
- 注入 `ORDINARY_CHAT_SYSTEM_PROMPT`。
- 调用 LLM 流式输出。
- 将用户原始问题和 assistant 最终回答写入 PostgreSQL 与 Redis。

限制：

- 不能声称读取本地文件。
- 不能编造实时数据。
- 只服务金融研究、资料检索、报告写作和系统使用引导。

### 6.3 search_mode=local

本地 RAG 模式：

1. `local_file_router_service.py` 读取当前用户已成功入库的文件元数据。
2. LLM 判断用户意图，决定检索具体文件、知识库或全部知识库。
3. `retrieval_service.py` 调用 embedding，进入 Milvus dense vector search。
4. 召回的 chunk 转换为 `ChatReference`。
5. 使用 `RAG_PROMPT_TEMPLATE` 生成增强 prompt。
6. LLM 回答时用 `[编号]` 引用本地资料。
7. 最终 `done.references` 返回引用对象。

本地 RAG prompt 不写入 PG/Redis 历史，PG/Redis 仍只保存用户原始问题和 assistant 回答。

### 6.4 search_mode=web

联网搜索模式：

1. `search_service.py` 调用 Bocha Web Search API。
2. Redis 使用 `web_search:bocha:` 前缀缓存搜索结果，TTL 默认 3600 秒。
3. 搜索结果转换为 `ChatReference`。
4. 使用 `WEB_SEARCH_PROMPT_TEMPLATE` 生成增强 prompt。
5. LLM 基于联网资料回答，并要求用 `[编号]` 引用来源。
6. `done.references` 返回 `source_type="web"` 的引用。

## 7. 知识库入库与检索

### 7.1 上传与解析

知识库相关接口：

- `POST /api/v1/knowledge/bases`
- `GET /api/v1/knowledge/bases`
- `DELETE /api/v1/knowledge/bases/{kb_id}`
- `GET /api/v1/knowledge/bases/{kb_id}/stats`
- `POST /api/v1/knowledge/bases/{kb_id}/documents`
- `GET /api/v1/knowledge/bases/{kb_id}/documents`
- `DELETE /api/v1/knowledge/bases/{kb_id}/documents/{doc_id}`
- `POST /api/v1/knowledge/retrieve`

文档入库流水线：

```text
document_router.py
  -> document_service.process_document()
  -> parse_document_to_chunks()
  -> embedding_service.generate_embedding()
  -> milvus_service.create_collection()
  -> milvus_service.batch_insert()
  -> update Document.status/chunk_count
```

### 7.2 PDF 分块

PDF 解析优先使用 DocMind，得到 Markdown 文本后按 token 分块：

- 默认 `PDF_CHUNK_TOKENS = 800`
- 默认 `PDF_CHUNK_OVERLAP = 100`

分块结果包含：

- `content`
- `source_type`
- `chunk_index`

### 7.3 Excel 解析

Excel 文件通过 `xlsx_service.py` 解析：

- `.xlsx`
- `.xlsm`
- `.xls`

解析后保留 sheet、行范围等 metadata，方便前端引用和定位。

### 7.4 向量检索

`retrieval_service.py` 支持：

- 单知识库检索：`retrieve_from_kb`
- 多知识库检索：`retrieve_from_kbs`
- 指定文档检索：`retrieve_from_documents`
- 当前用户全部知识库检索：`retrieve_from_all_user_kbs`

所有检索都先校验 `user_id`，防止跨用户访问。

## 8. 长期记忆设计

长期记忆目标是让系统能够记住用户的研究偏好、关注行业/公司/指标和历史研究上下文。

### 8.1 创建逻辑

`memory_service.py` 负责：

- 从会话消息中提取可复用信息。
- 调用 LLM 输出 JSON 摘要。
- 将摘要写入 PostgreSQL。
- 将摘要和关键洞察向量化，写入 Milvus。

### 8.2 召回逻辑

当用户继续聊天时：

1. 根据当前 query 生成 embedding。
2. 在 `long_term_memories` collection 中按当前 `user_id` 检索。
3. 返回 top-k memory。
4. 构建 memory context 注入普通聊天、本地 RAG 或联网搜索 prompt。

### 8.3 设计边界

- 长期记忆只代表用户历史偏好和上下文。
- 长期记忆不能替代本地资料或联网事实来源。
- 自动长期记忆创建失败不能中断聊天 SSE。

## 9. Deep Research 多 Agent 架构

Deep Research 是本项目的核心能力。它用多 Agent 分阶段完成金融研究报告生成。

### 9.1 为什么不使用 LangGraph/LangChain

本项目没有引入 LangGraph 或 LangChain，而是手写类 LangGraph 的状态机，原因是：

- 需要分阶段将每个 Agent 的结果实时输出给前端。
- 需要在每个 Agent/阶段结束后保存 checkpoint。
- 需要将普通聊天消息与 Deep Research 执行事件严格隔离。
- 需要更细粒度地控制补搜、修订、审核闭环。
- 当前系统已有 service/router/schema 分层，需要避免外部框架侵入整体结构。

### 9.2 全局工作记忆 ResearchState

`ResearchState` 定义在 `backend/app/service/deep_research/state.py`，是所有 Agent 共享的全局工作记忆。

核心字段包括：

- 基础字段：
  - `query`
  - `user_id`
  - `session_id`
  - `phase`
  - `iteration`
  - `max_iterations`
  - `search_web`
  - `search_local`

- 规划与研究问题：
  - `outline`
  - `mind_map`
  - `key_entities`
  - `research_questions`
  - `hypotheses`

- 研究证据：
  - `knowledge_graph`
  - `facts`
  - `data_points`
  - `raw_sources`
  - `insights`

- 可视化和代码执行：
  - `charts`
  - `code_executions`

- 报告生成：
  - `draft_sections`
  - `final_report`
  - `references`

- 审核闭环：
  - `critic_feedback`
  - `unresolved_issues`
  - `quality_score`
  - `review_verdict`
  - `forced_completed`
  - `pending_search_queries`

- 可观测性：
  - `phase_outputs`
  - `agent_outputs`
  - `agent_events`
  - `logs`
  - `errors`

### 9.3 ResearchPhase 状态机

Deep Research 使用以下 phase：

```text
init
  -> planning
  -> researching
  -> analyzing
  -> writing
  -> reviewing
  -> completed
```

审核闭环包含两个回退 phase：

```text
reviewing
  -> re_researching
  -> revising
  -> reviewing
```

或：

```text
reviewing
  -> revising
  -> reviewing
```

当达到最大迭代次数或安全上限时，系统会强制进入 `completed`，并通过 warning 事件暴露风险。

### 9.4 BaseAgent

所有 Agent 都继承 `BaseAgent`。

`BaseAgent` 提供：

- 抽象方法：
  - `async process(state: ResearchState) -> ResearchState`

- LLM 调用：
  - `call_llm()`
  - 默认使用 OpenAI SDK 同步 client。
  - 通过 `asyncio.to_thread()` 放入线程，避免阻塞事件循环。
  - 支持 JSON mode。

- JSON 解析：
  - `parse_json_response()`
  - 支持纯 JSON、Markdown JSON code block、最外层 `{...}` 提取、`ast.literal_eval` fallback。
  - 修复 BOM、注释、尾随逗号、非法反斜杠、缺失 key 引号等常见 LLM 输出问题。

- 事件输出：
  - `add_message()`
  - 写入 `state["agent_events"]`。
  - 如果存在 `_message_queue`，同时 `.put_nowait(event)` 推送到 SSE 流。
  - 事件不是普通聊天消息，不写 `chat_messages`。

- 执行日志：
  - `add_log()`
  - 写入 `state["logs"]`。

### 9.5 Agent 列表与职责

#### 9.5.1 Architect

文件：

```text
backend/app/service/deep_research/agents/architect.py
```

职责：

- 深度理解用户 query。
- 生成研究大纲。
- 生成研究假设。
- 生成核心研究问题。
- 初始化 mind map / knowledge graph。

运行条件：

- 只在 `phase == init` 时执行。

输出到 state：

- `outline`
- `research_questions`
- `hypotheses`
- `mind_map`
- `knowledge_graph`
- `phase = planning`
- `agent_outputs`
- `phase_outputs`

设计特点：

- prompt 使用扁平 JSON 字段，减少 LLM 结构错误。
- `_convert_flat_to_outline()` 将 `sec_1_title/sec_1_desc/sec_1_query` 转为标准 outline。
- 失败时不抛异常，而是写入 `state["errors"]`，允许编排层处理重试或展示。

#### 9.5.2 Scout

文件：

```text
backend/app/service/deep_research/agents/scout.py
```

职责：

- 根据 Architect 的 pending sections 执行深度检索。
- 支持联网搜索和本地知识库检索。
- 提取 facts、data_points、insights。
- 验证或推翻 hypotheses。
- 在 Critic 要求补搜时执行 supplementary research。

运行条件：

- `phase == planning`
- `phase == researching`
- `phase == re_researching`

搜索来源：

- Web：复用 `search_service.search_web`，底层调用 Bocha。
- Local：复用 `retrieve_from_all_user_kbs`，底层调用 Milvus。

输出到 state：

- `facts`
- `data_points`
- `raw_sources`
- `insights`
- `hypotheses`
- `knowledge_graph` 增量反馈
- `phase = researching` 或补搜后 `phase = revising`

SSE 事件：

- `research_step`
- `thought`
- `action`
- `search_progress`
- `search_results`
- `observation`
- `knowledge_graph`

金融领域优化：

- 搜索和分析 prompt 优先关注公司公告、年报、季报、交易所公告、监管机构、央行/统计局/行业协会、券商和咨询报告。
- 输出禁止股票买卖建议、评级、目标价和收益承诺。

#### 9.5.3 DataAnalyst

文件：

```text
backend/app/service/deep_research/agents/data_analyst.py
```

职责：

- 从 Scout 的 facts 中提取结构化数据。
- 深度重建知识图谱。
- 生成前端交互式 ECharts 图表配置。

运行条件：

- 只在 `phase == analyzing` 时执行。

主要步骤：

```text
analyze_data()
  -> extract_data()
  -> build_knowledge_graph()
  -> generate_charts()
```

输出到 state：

- `data_points`
- `insights`
- `knowledge_graph`
- `charts`
- `agent_outputs`
- `phase_outputs`

图谱处理：

- 节点包含 `id/label/name/type/importance/size/summary/display_label`。
- 过滤无效边、自环边、重复边。
- 限制节点和边数量，避免前端渲染混乱。

图表处理：

- 只基于已有结构化数据生成图表。
- 趋势图至少需要 2 个时间点。
- 饼图至少需要 2 个类别。
- bar 图要求类目和 series 数据长度一致。
- 数据不足时跳过不完整图表，避免前端出现空白或误导图。

#### 9.5.4 Wizard

文件：

```text
backend/app/service/deep_research/agents/wizard.py
```

职责：

- 生成最终报告可用的静态 PNG 图表。
- 使用 Python 代码真实执行数据分析。
- 对失败代码进行自修复。

运行条件：

- 通常在 DataAnalyst 之后执行。
- 如果 phase 不是 `analyzing` 但 data_points 足够，也可自动切到 analyzing。

核心能力：

- `analyze_data()`
- `clean_code()`
- `execute_code()`
- `execute_with_self_correction()`
- `fix_code()`
- `is_code_safe()`
- `execute_in_sandbox()`
- `generate_charts()`

安全策略：

- 禁止文件、网络和系统调用。
- 禁止 `exec/eval/open/__import__/subprocess/os/sys/requests/socket/pathlib/pickle` 等危险模式。
- sandbox 只暴露有限 builtins 和 `pd/np/plt/sns`。
- 图表以 base64 PNG 存入 state，不写文件路径。

输出到 state：

- `code_executions`
- `charts` 中的 report image：
  - `artifact_type="report_image"`
  - `image_base64`
  - `metadata.generated_by="Wizard"`

#### 9.5.5 Writer

文件：

```text
backend/app/service/deep_research/agents/writer.py
```

职责：

- 根据 outline 分章节撰写报告。
- 使用 facts、data_points、insights、charts 和 references 组织内容。
- 合成完整最终报告。
- 根据 Critic 反馈修订报告。

运行条件：

- `phase == writing` 执行 `write_report()`。
- `phase == revising` 执行 `revise_report()`。

核心步骤：

```text
write_report()
  -> for section in outline:
       write_section()
  -> synthesize_report()
  -> phase = reviewing
```

输出到 state：

- `draft_sections`
- `final_report`
- `references`
- `phase = reviewing`

报告格式：

- `## 执行摘要`
- 分章节正文
- `## 风险与限制`
- `## 结论与展望`
- `## 参考文献`

合规限制：

- 禁止股票推荐。
- 禁止买入/卖出/持有评级。
- 禁止目标价、收益承诺、仓位建议或交易指令。

健壮性处理：

- 如果 LLM 返回 dict/list 字符串或 `{'内容': ...}`，Writer 会递归提取正文并重新组装为 Markdown 段落。
- 最终报告会清理原始 JSON/Python dict 符号，避免 `{}` 出现在报告正文中。

#### 9.5.6 Critic

文件：

```text
backend/app/service/deep_research/agents/critic.py
```

职责：

- 审核最终报告质量。
- 检查事实依据、逻辑、遗漏、幻觉、过期信息、偏见和合规风险。
- 判断是否需要补搜或修订。
- 生成质量分、审核结论和未解决问题。

运行条件：

- 只在 `phase == reviewing` 时执行。

输出字段：

- `quality_score`
- `review_verdict`
- `critic_feedback`
- `unresolved_issues`
- `pending_search_queries`
- `phase`

路由决策：

- `verdict == pass`：进入 `completed`。
- 需要补充搜索：进入 `re_researching`。
- 不需要补搜但需要改写：进入 `revising`。
- 达到最大迭代次数：强制 `completed`，发送 warning。

合规审核：

- 如果报告出现股票推荐、买入/卖出/持有评级、目标价、收益承诺、仓位建议或交易指令，必须标记为 `critical` 或 `major`。

## 10. DeepResearchGraph 编排逻辑

文件：

```text
backend/app/service/deep_research/graph.py
```

### 10.1 运行入口

后端流式入口：

```text
POST /api/v1/deep-research/stream
```

请求：

```json
{
  "session_id": "uuid",
  "content": "研究问题",
  "search_web": true,
  "search_local": false,
  "resume": false
}
```

返回：

```text
Content-Type: text/event-stream
data: {"type":"research_start",...}

data: {"type":"research_step",...}

data: {"type":"checkpoint_saved",...}

data: {"type":"done",...}
```

### 10.2 首次运行

首次运行时：

1. 验证 `session_id` 属于当前用户。
2. 创建初始 `ResearchState`。
3. yield `research_start`。
4. 进入 `_run_simplified()`。

主流程：

```text
Architect
  -> Scout
  -> DataAnalyst
  -> Wizard
  -> Writer
  -> Critic
```

如果 Critic 不通过：

```text
Critic -> Scout(re_researching) -> Writer(revising) -> Critic
```

或：

```text
Critic -> Writer(revising) -> Critic
```

直到：

- `phase == completed`
- 或达到安全迭代上限。

### 10.3 Resume 恢复

当 `resume=true`：

1. 按 `user_id + session_id` 加载 checkpoint。
2. 使用 checkpoint 的 `state_json` 恢复后端 ResearchState。
3. 使用 `ui_state_json` 恢复前端展示状态。
4. yield `research_resumed`。
5. 如果 checkpoint 已 completed，则直接 yield `done`。
6. 否则根据当前 phase 和 last_agent 判断下一阶段继续执行。

### 10.4 Agent 流式执行

`_run_agent_with_streaming()` 的核心逻辑：

1. `asyncio.create_task(agent.process(state))` 后台启动 Agent。
2. while task 未完成，持续从 `_message_queue` 读取事件。
3. 每读到一个事件就 yield 给前端。
4. task 完成后 `await task`，确保捕获 Agent 异常。
5. drain 队列中残留事件。

这种设计使 Agent 可以同步更新 state，同时把阶段性事件实时输出给前端。

### 10.5 Checkpoint 保存

每个 Agent 结束后：

1. 调用 `save_checkpoint()`。
2. 清理不可持久化字段：
   - `_message_queue`
   - db/session/client/task/runtime 等。
3. 写入 `deep_research_checkpoints`。
4. 生成 `checkpoint_saved` SSE 事件。

### 10.6 UI State

`update_ui_state()` 将后端 ResearchState 转为前端易恢复结构：

- `phase`
- `research_steps`
- `search_results`
- `charts`
- `knowledge_graph`
- `streaming_report`
- `final_report`
- `references`
- `quality_score`
- `unresolved_issues`
- `iterations`
- `verdict`
- `summary`

前端收到 `research_resumed` 后，可以直接恢复图谱、图表、报告和审核状态。

## 11. SSE 事件体系

Deep Research SSE 事件不是普通聊天消息，只代表 Agent 执行状态。

常见事件：

| type | 含义 |
| --- | --- |
| `research_start` | Deep Research 启动 |
| `research_resumed` | 从 checkpoint 恢复 |
| `research_step` | 阶段开始/完成/失败 |
| `thought` | Agent 思考或过程说明 |
| `action` | Agent 调用某个工具或子步骤 |
| `search_progress` | 检索进度 |
| `search_results` | 搜索结果 |
| `observation` | 本阶段观察结果 |
| `knowledge_graph` | 知识图谱更新 |
| `charts` | ECharts 图表批量更新 |
| `chart` | Wizard 单张报告图表 |
| `code` | Wizard 生成 Python 代码 |
| `code_fix` | Wizard 自动修复代码 |
| `code_result` | 代码执行结果 |
| `section_content` | Writer 章节草稿 |
| `report_draft` | Writer 报告草稿 |
| `review` | Critic 审核结果 |
| `critic_feedback` | Critic 具体问题 |
| `checkpoint_saved` | 阶段 checkpoint 已保存 |
| `warning` | 编排或审核警告 |
| `done` | Deep Research 完成 |
| `error` | 流程失败 |

## 12. 前端实现设计

### 12.1 页面布局

核心页面为 `WorkspacePage`：

- 左侧 sidebar：
  - Chat / 文件 tab。
  - 研究记录列表。
  - 知识库管理入口。

- 中间 main：
  - topbar。
  - messages 对话流。
  - composer 输入区。
  - 搜索模式切换：
    - 本地搜索
    - 网络搜索
  - Deep Research 开关。

- 右侧 DeepResearchWorkspace：
  - Deep Research 模式产生内容时出现。
  - 支持展开/收起。
  - tabs：
    - `进度`
    - `图谱`
    - `图表`
    - `报告`

### 12.2 普通聊天展示

普通聊天：

- 用户消息显示为右侧气泡。
- assistant 流式 delta 追加到当前消息。
- done 后显示引用列表。

### 12.3 Deep Research 展示

Deep Research：

- 聊天区只显示：
  - 用户研究问题。
  - “Deep Research 正在执行...” 状态。
  - 最终报告。

- 右侧工作区显示：
  - Agent Timeline。
  - Knowledge Map。
  - ECharts 交互图表。
  - Wizard 生成的 PNG 报告图。
  - Writer 草稿和引用。

### 12.4 Knowledge Map

`KnowledgeGraphView` 使用 ECharts graph series：

- `layout="force"`
- 支持拖拽和缩放。
- 节点大小基于 importance / size。
- 节点颜色根据 type 区分：
  - topic
  - industry/company
  - indicator/opportunity
  - policy/risk
- 边标签默认隐藏，hover/高亮时显示，避免视觉混乱。

### 12.5 Charts

`EChartsView`：

- 接收后端 `echarts_option`。
- 前端再做一次 option normalizer。
- 自动补齐：
  - `grid.containLabel`
  - `tooltip`
  - `legend`
  - `title`
- 对不完整图表显示 fallback：
  - 单点 line 不渲染。
  - 单值 pie 不渲染。
  - bar 类目与数据长度不匹配不渲染。

`ReportChartsView`：

- 渲染 Wizard 输出的 base64 PNG。
- 用于最终报告图表展示。

### 12.6 Report Markdown

`ResearchReportView` 和聊天区最终报告复用轻量 Markdown renderer：

- 支持：
  - `##`
  - `###`
  - 段落
  - 有序/无序列表
  - 分隔线
  - HTTP/HTTPS Markdown 链接

前端不会引入完整 Markdown 依赖，避免扩大攻击面和 bundle。

### 12.7 SSE 解析

前端 API 层：

```text
frontend/src/api/deepResearch.ts
frontend/src/lib/sse.ts
```

负责：

- 发起 `fetch`。
- 读取 ReadableStream。
- 按 SSE `data:` 事件切分。
- JSON.parse。
- 回调给 WorkspacePage。

### 12.8 动效设计

Deep Research UI 动效用于表达 Agent 正在运行：

- Agent timeline running pulse。
- 阶段切换 slide/fade。
- chart skeleton。
- graph/chart panel reveal。
- checkpoint subtle feedback。

所有动效遵守 `prefers-reduced-motion`，只动画 `transform` 和 `opacity`，避免布局抖动。

## 13. 数据流总览

### 13.1 普通聊天数据流

```text
Frontend WorkspacePage
  -> POST /api/v1/chat/stream
  -> chat_router.py
  -> chat_service.stream_chat_response()
  -> session_service 读取/写入 PG + Redis
  -> memory_service 检索长期记忆
  -> local retrieval 或 web search 或 ordinary LLM
  -> SSE delta/done
  -> Frontend 更新 message
```

### 13.2 本地知识库入库数据流

```text
Frontend KnowledgePanel upload
  -> document_router.py
  -> Document(status=pending)
  -> background process_document()
  -> DocMind / xlsx parser
  -> chunk_text()
  -> embedding_service.generate_embedding()
  -> Milvus collection insert
  -> Document(status=success, chunk_count=n)
```

### 13.3 本地 RAG 数据流

```text
User query
  -> local_file_router_service
  -> retrieval_service
  -> embedding
  -> Milvus vector_search
  -> ChatReference
  -> RAG prompt
  -> LLM stream
  -> done.references
```

### 13.4 Deep Research 数据流

```text
Frontend Deep Research mode
  -> POST /api/v1/deep-research/stream
  -> DeepResearchGraph.run()
  -> ResearchState
  -> Architect
  -> Scout
  -> DataAnalyst
  -> Wizard
  -> Writer
  -> Critic
  -> optional Scout/Writer/Critic loop
  -> checkpoint per agent
  -> done final_report/charts/knowledge_graph/references
  -> Frontend DeepResearchWorkspace + final assistant report
```

## 14. 数据库迁移

当前迁移文件：

```text
202604270001_create_users.py
202604270002_create_chat_tables.py
202604280003_create_knowledge_tables.py
202604290004_create_long_term_memories.py
202604290005_create_deep_research_checkpoints.py
```

运行迁移：

```bash
cd backend
source .venv/bin/activate
alembic upgrade head
```

查看当前版本：

```bash
cd backend
.venv/bin/python -m alembic current
```

当前 head 应为：

```text
202604290005
```

## 15. API 总览

### 15.1 健康检查

```text
GET /health
```

### 15.2 认证

```text
POST /api/v1/auth/register
POST /api/v1/auth/login
GET /api/v1/auth/me
POST /api/v1/auth/logout
```

### 15.3 聊天

```text
POST /api/v1/chat/session
GET /api/v1/chat/sessions
POST /api/v1/chat/stream
DELETE /api/v1/chat/session/{session_id}
GET /api/v1/chat/session/{session_id}/messages
```

### 15.4 知识库与文档

```text
POST /api/v1/knowledge/bases
GET /api/v1/knowledge/bases
DELETE /api/v1/knowledge/bases/{kb_id}
GET /api/v1/knowledge/bases/{kb_id}/stats
POST /api/v1/knowledge/bases/{kb_id}/documents
GET /api/v1/knowledge/bases/{kb_id}/documents
DELETE /api/v1/knowledge/bases/{kb_id}/documents/{doc_id}
POST /api/v1/knowledge/retrieve
```

### 15.5 搜索

```text
POST /api/v1/search/web
```

### 15.6 长期记忆

```text
POST /api/v1/memories/create
POST /api/v1/memories/search
GET /api/v1/memories/context/{query}
```

### 15.7 Deep Research

```text
POST /api/v1/deep-research/stream
```

## 16. 运行方式

### 16.1 后端

```bash
cd backend
source .venv/bin/activate
uvicorn app.main:app --reload
```

默认地址：

```text
http://127.0.0.1:8000
```

健康检查：

```bash
curl http://127.0.0.1:8000/health
```

### 16.2 前端

```bash
cd frontend
npm install
npm run dev
```

默认地址：

```text
http://127.0.0.1:5173
```

### 16.3 依赖服务

本地运行完整功能通常需要：

- PostgreSQL
- Redis
- Milvus Lite / Milvus 本地路径配置
- OpenAI API Key
- Bocha API Key
- DocMind API Key（用于 PDF 解析）

没有外部 API key 时，部分真实调用无法运行，但大部分单元测试使用 mock/fake client。

## 17. 测试体系

### 17.1 后端测试

全量测试：

```bash
cd backend
.venv/bin/python -m pytest -q
```

重点测试模块：

- `test_acceptance_smoke.py`
- `test_auth.py`
- `test_chat_service.py`
- `test_chat_router.py`
- `test_knowledge_router.py`
- `test_document_service.py`
- `test_retrieval_service.py`
- `test_memory_service.py`
- `test_deep_research_state.py`
- `test_deep_research_base.py`
- `test_deep_research_architect.py`
- `test_deep_research_scout.py`
- `test_deep_research_data_analyst.py`
- `test_deep_research_wizard.py`
- `test_deep_research_writer.py`
- `test_deep_research_critic.py`
- `test_deep_research_graph.py`
- `test_deep_research_router.py`

### 17.2 前端测试

单元/组件测试：

```bash
cd frontend
npm test -- --run
```

构建：

```bash
cd frontend
npm run build
```

E2E：

```bash
cd frontend
npm run e2e
```

覆盖内容：

- 登录流程。
- 聊天流式输出。
- 搜索模式切换。
- 知识库创建与上传 mock。
- Deep Research SSE mock。
- 独立 Deep Research Workspace。
- Knowledge Map、Charts、Report Preview。

## 18. 金融合规与内容边界

系统明确定位为金融信息解释和研究报告辅助工具，不输出投资建议。

在普通聊天、Agent prompt、Writer、Critic 中均设置了限制：

- 不给股票推荐。
- 不输出买入/卖出/持有评级。
- 不输出目标价。
- 不承诺收益。
- 不给仓位建议。
- 不输出交易指令。

报告内容应强调：

- 数据来源。
- 数据时点。
- 统计口径。
- 不确定性。
- 风险因素。
- 结论边界。

## 19. 当前已知限制

### 19.1 外部依赖限制

- OpenAI、Bocha、DocMind 均依赖外部 API key。
- 网络不稳定或 API 限流会影响真实 Deep Research 流程。

### 19.2 检索质量限制

- 当前本地检索主要是 dense vector retrieval。
- 尚未实现 BM25/hybrid retrieval。
- 本地文件路由依赖 LLM 输出可校验 JSON，极端情况下可能需要 fallback。

### 19.3 图表质量限制

- DataAnalyst 的 ECharts 图表依赖 LLM 输出，后端和前端已增加基本校验，但复杂金融图表仍可能需要更严格 schema。
- Wizard 执行 Python 图表代码有 sandbox 限制，复杂分析代码可能被安全策略拒绝。

### 19.4 报告质量限制

- Writer 和 Critic 已形成写作/审核闭环，但最终质量仍依赖检索事实和 LLM 生成质量。
- Critic 补搜后当前只重跑 `Scout -> Writer -> Critic`，不重跑 DataAnalyst/Wizard。

## 20. 后续扩展建议

### 20.1 检索增强

- 增加 BM25 + dense hybrid retrieval。
- 支持文件级、章节级和表格级结构化检索。
- 对公司年报、财报、公告建立更细粒度 metadata。

### 20.2 Deep Research 增强

- 增加暂停/继续控制接口。
- 增加 Agent 级执行耗时和 token 统计。
- 增加人工反馈入口，让用户在某阶段调整研究计划。
- 增加报告导出为 PDF/DOCX。
- 增加图表人工选择和重生成。

### 20.3 数据可视化增强

- 为金融指标定义标准图表模板：
  - 收入/利润趋势。
  - 毛利率/净利率趋势。
  - 资产负债结构。
  - 同业对比。
  - 风险指标矩阵。
- 后端返回更严格的 chart schema，前端只负责渲染。

### 20.4 审核与合规增强

- 增加事实溯源评分。
- 增加“无来源结论”自动标记。
- 增加投资建议敏感词更细粒度识别。
- 增加报告中引用覆盖率统计。

### 20.5 任务队列化

当前 Deep Research 通过 HTTP SSE 长连接执行。后续可扩展为：

- 后台任务队列。
- WebSocket 或事件订阅。
- 多用户并发任务管理。
- checkpoint + job status 分离。

## 21. 开发注意事项

### 21.1 不要混用普通聊天与 Deep Research 状态

- 普通聊天消息只由 `session_service.py` 和 `chat_service.py` 写入。
- Deep Research 过程事件只写 `agent_events` 和 checkpoint。
- `checkpoint_service.py` 不读取、不写入 `chat_messages`。

### 21.2 修改模型必须加迁移

修改 `app/models/*` 时必须：

- 更新 `app/models/__init__.py`。
- 新增 Alembic migration。
- 更新模型测试和迁移测试。

### 21.3 修改 API 必须同步前端

修改 schema/router/SSE payload 时必须同步：

- `frontend/src/types.ts`
- `frontend/src/api/*`
- `frontend/src/lib/sse.ts`
- 对应组件测试和 E2E mock。

### 21.4 Agent 输出必须可恢复

Agent 写入 state 的内容必须 JSON serializable。不可持久化对象必须放在 `_message_queue`、`runtime` 或其他 runtime-only key 中，并由 `clean_state_for_checkpoint()` 清理。

### 21.5 Prompt 修改要考虑金融边界

所有 Agent prompt 都应保持：

- 金融研究语境。
- 来源优先。
- 不编造事实。
- 不输出股票推荐。
- 明确风险和限制。

## 22. 总结

本项目已经形成一个完整的金融行业研究 Agent 系统雏形：

- 后端具备认证、聊天、知识库、RAG、联网搜索、长期记忆和 Deep Research 编排。
- 前端具备登录、研究工作台、知识库管理、聊天流式输出、Deep Research 独立工作区、图谱/图表/报告展示。
- Deep Research 采用手写状态机和多 Agent 协作，实现从规划、检索、分析、图表、写作到审核的完整流程。
- Checkpoint 机制确保长流程可保存、可恢复，并与普通聊天持久化严格隔离。

整体架构的核心价值在于：把金融研究从“单次问答”升级为“计划驱动、证据沉淀、图表辅助、报告生成、质量审核”的多阶段工作流，同时保留清晰的工程边界和可测试性。
