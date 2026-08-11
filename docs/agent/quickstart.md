# Quickstart Guide

> Essential commands to build, run, and test Resume Matcher.

## Prerequisites

- Node.js 22+
- Python 3.13+
- [uv](https://docs.astral.sh/uv/) (Python package manager)
- Redis 7+ (or use the Docker command below)

## Installation

```bash
# Backend (from repo root)
cd apps/backend
uv sync

# Frontend (from repo root)
cd apps/frontend
npm install
```

## Development

```bash
# Redis (Terminal 1)
docker run --rm -p 6379:6379 redis:7-alpine redis-server --appendonly yes

# Backend (Terminal 2, from repo root)
cd apps/backend
uv run uvicorn app.main:app --reload --port 8000

# Memory Worker (Terminal 3, from repo root)
cd apps/backend
uv run arq app.ai_chat.memory.worker.WorkerSettings

# Frontend (Terminal 4, from repo root)
cd apps/frontend
npm run dev
```

## Quality Checks

```bash
# From apps/frontend
npm run lint     # Lint frontend
npm run format   # Prettier
```

## Backend Commands

```bash
cd apps/backend
uv run uvicorn app.main:app --reload --port 8000
uv run arq app.ai_chat.memory.worker.WorkerSettings
uv run pytest
```

## Environment Setup

```bash
# Backend
cp apps/backend/.env.example apps/backend/.env

# Frontend
cp apps/frontend/.env.sample apps/frontend/.env.local
```

Memory Worker 可选配置：

```dotenv
REDIS_URL=redis://localhost:6379/0
AI_CHAT_MEMORY_QUEUE_NAME=ai-chat:memory
AI_CHAT_MEMORY_WORKER_CONCURRENCY=2
AI_CHAT_MEMORY_JOB_TIMEOUT_SECONDS=1800
AI_CHAT_MEMORY_WAIT_TIMEOUT_SECONDS=60
```

## First-Time Setup

1. Open http://localhost:3000/settings
2. Select AI provider + enter API key
3. Click "Test Connection"
4. Upload your first resume!
