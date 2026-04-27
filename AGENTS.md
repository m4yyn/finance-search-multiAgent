# 金融行业信息报告编写 Agent 助手项目规范

## 项目目标

本项目从零构建“金融行业信息报告编写 Agent 助手”。当前阶段只搭建后端代码骨架与运行环境，不实现具体业务逻辑、Agent 推理链路、RAG 流程或报告生成逻辑。

## 目录约束

- `reference/` 仅作为代码逻辑参考资料，禁止被新项目直接导入、调用、复制或作为运行时依赖。
- 新后端代码统一放在 `backend/` 下。
- 除非用户明确要求，不修改 `reference/`、`frontend/`、`data/` 中的内容。
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
```

## 当前骨架运行方式

在 `backend/` 目录中使用专用虚拟环境：

```bash
source .venv/bin/activate
uvicorn app.main:app --reload
```

健康检查接口：

```text
GET /health
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
- `BOCHA_API_KEY`
- `JWT_SECRET_KEY`
- `JWT_ALGORITHM`
- `JWT_ACCESS_TOKEN_EXPIRE_MINUTES`
- `UPLOAD_DIR`
