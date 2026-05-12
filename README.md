# Financial Industry Information Assistant

An end-to-end financial research assistant for chat, web search, local document retrieval, long-term memory, and multi-agent Deep Research report generation.

The project contains a FastAPI backend, a React/Vite frontend, PostgreSQL metadata storage, Redis short-term context cache, Milvus vector retrieval, OpenAI model calls, Bocha web search integration, and a Deep Research multi-agent workflow.

## Features

- User registration, login, JWT authentication, and session management.
- Streaming chat over Server-Sent Events.
- Three chat search modes:
  - `none`: ordinary financial research chat.
  - `local`: local RAG over uploaded documents.
  - `web`: web search with Bocha API.
- Knowledge base management and document ingestion.
- Milvus dense vector retrieval for local document chunks.
- Long-term memory stored in PostgreSQL and Milvus.
- Deep Research multi-agent pipeline:
  - Architect: research planning.
  - Scout: web/local evidence search.
  - DataAnalyst: structured data, knowledge graph, ECharts configs.
  - Wizard: Python-generated static report charts.
  - Writer: financial report drafting and revision.
  - Critic: quality review, compliance checks, and supplement routing.
- Deep Research checkpoint persistence and resume support.
- Frontend workspace for chat, Deep Research progress, knowledge maps, charts, and report preview.

## Tech Stack

### Backend

- FastAPI
- Pydantic Settings
- SQLAlchemy async ORM
- PostgreSQL
- Redis
- Milvus / Milvus Lite
- OpenAI Python SDK
- Alembic
- Pytest

### Frontend

- React
- TypeScript
- Vite
- ECharts
- Vitest
- Playwright

## Quick Start

The recommended local startup path is the helper script:

```bash
./scripts/start_dev.sh
```

The script will:

1. Create `backend/.env` from `backend/.env.example` if missing.
2. Create `frontend/.env` from `frontend/.env.example` if missing.
3. Prompt for API keys and write them to local env files.
4. Generate a local JWT secret if needed.
5. Create `backend/.venv` if missing.
6. Install backend and frontend dependencies unless `--skip-install` is passed.
7. Run Alembic migrations unless `--no-migrate` is passed.
8. Start the backend and frontend dev servers.

After startup:

- Frontend: <http://localhost:5173>
- Backend API: <http://localhost:8000/api/v1>
- Health check: <http://localhost:8000/health>
- OpenAPI docs: <http://localhost:8000/docs>

### Non-interactive API key setup

You can provide keys through environment variables:

```bash
OPENAI_API_KEY="sk-..." \
BOCHA_API_KEY="..." \
DOCMIND_ACCESS_KEY_ID="..." \
DOCMIND_ACCESS_KEY_SECRET="..." \
./scripts/start_dev.sh
```

Optional flags:

```bash
./scripts/start_dev.sh --skip-install
./scripts/start_dev.sh --no-migrate
./scripts/start_dev.sh --skip-install --no-migrate
```

## Manual Backend Setup

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

Required local services:

- PostgreSQL on `localhost:5432`
- Redis on `localhost:6379`

Milvus Lite uses the local path configured by `MILVUS_DB_PATH`.

## Manual Frontend Setup

```bash
cd frontend
npm install
cp .env.example .env
npm run dev
```

## Environment Variables

Important backend variables:

- `OPENAI_API_KEY`: required for model calls and embeddings.
- `OPENAI_BASE_URL`: defaults to `https://api.openai.com/v1`.
- `LLM_MODEL`: default chat/agent model.
- `EMBEDDING_MODEL`: default embedding model.
- `BOCHA_API_KEY`: required for web search.
- `DOCMIND_ACCESS_KEY_ID` / `DOCMIND_ACCESS_KEY_SECRET`: optional document parsing integration.
- `DATABASE_URL` or PostgreSQL host/user/password/db fields.
- `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD`.
- `JWT_SECRET_KEY`.

Important frontend variables:

- `VITE_API_BASE_URL`: default `http://localhost:8000/api/v1`.

Do not commit local `.env` files or real API keys.

## Common Commands

Backend tests:

```bash
cd backend
.venv/bin/python -m pytest -q
```

Frontend tests:

```bash
cd frontend
npm test -- --run
npm run build
npm run e2e
```

Database migration:

```bash
cd backend
source .venv/bin/activate
alembic upgrade head
```

## API Overview

All business APIs are mounted under `/api/v1`.

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

## Notes

- `reference/` is historical reference material only and is not part of runtime code.
- Deep Research checkpoints are separate from ordinary chat messages.
- Local RAG currently uses dense vector retrieval in Milvus, not BM25 by default.
- The project is designed for financial information explanation, research organization, and report drafting. It does not provide investment recommendations.

For Chinese documentation, see [README_ZH.md](README_ZH.md).
