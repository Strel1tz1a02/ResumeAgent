# ResumeAgent setup

[简体中文](SETUP.zh-CN.md) · **English** · [Back to README](README.en.md)

This guide covers two supported paths:

- **Docker Compose** for the complete application with the fewest manual steps.
- **Local frontend/backend development** with infrastructure in Docker.

## Option A: Docker Compose (recommended)

### Requirements

- [Git](https://git-scm.com/)
- [Docker Desktop](https://www.docker.com/products/docker-desktop/), or Docker Engine with Compose v2
- At least 6 GB RAM and 10 GB free disk space are recommended

### Start

```bash
git clone https://github.com/Strel1tz1a02/resume-agent.git
cd resume-agent
docker compose up --build -d
```

```bash
docker compose ps
docker compose logs -f resume-matcher
```

Open **<http://localhost:3000>**. The backend is available through the Next.js same-origin proxy; Compose does not expose port 8000 publicly.

### First-run configuration

1. Open **Settings** from the dashboard.
2. Select OpenAI, Anthropic, Google, OpenRouter, DeepSeek, Groq, Ollama, or OpenAI-compatible.
3. Add an API key for a cloud provider, or configure the base URL for a local service.
4. Save and run **Test connection**.
5. Start with the **Experience library** and capture real career evidence.

Keys are encrypted in the local SQLite database. Cloud model providers receive the resume and job-description content required for a request.

### Stop and update

```bash
# Stop while preserving data volumes
docker compose down

# Update and rebuild
git pull
docker compose up --build -d
```

The stack uses `resume-data`, `redis-data`, and `qdrant-data` volumes. Do not run `docker compose down -v` unless you intend to delete all application data and indexes.

### Ollama on the host

Install [Ollama](https://ollama.com/) and pull a model:

```bash
ollama pull gemma3:4b
```

Select **Ollama** in ResumeAgent Settings. With Docker Desktop on macOS or Windows, use `http://host.docker.internal:11434` as the base URL. On Linux, use a host address reachable from the container or configure an appropriate Docker network.

## Option B: local development

### Requirements

| Tool | Version |
|---|---|
| Python | 3.13 |
| Node.js | 22+ |
| npm | 10+ |
| uv | Current stable release |
| Docker Compose | v2 |

### Windows helper

```powershell
git clone https://github.com/Strel1tz1a02/resume-agent.git
Set-Location ResumeAgent
./start-dev.ps1 -Setup
```

On later runs, use `./start-dev.ps1`. Add `-SkipInfrastructure` when Redis, Qdrant, and Playwright MCP are already running.

### Manual setup on any platform

Create configuration files from the tracked canonical templates.

macOS/Linux:

```bash
cp config/backend.env.example apps/backend/.env
cp config/frontend.env.example apps/frontend/.env.local
```

PowerShell:

```powershell
Copy-Item config/backend.env.example apps/backend/.env
Copy-Item config/frontend.env.example apps/frontend/.env.local
```

Start infrastructure:

```bash
docker compose up -d redis qdrant playwright-mcp-gateway
```

Install dependencies:

```bash
cd apps/backend
uv sync --extra dev
uv run playwright install chromium

cd ../frontend
npm ci
```

Run these processes in separate terminals:

```bash
# Terminal 1: FastAPI
cd apps/backend
uv run app
```

```bash
# Terminal 2: memory worker
cd apps/backend
uv run arq app.ai_chat.memory.worker.WorkerSettings
```

```bash
# Terminal 3: experience index worker
cd apps/backend
uv run arq app.resume_generation.index_worker.WorkerSettings
```

```bash
# Terminal 4: Next.js
cd apps/frontend
npm run dev
```

Open **<http://localhost:3000>**. Running only Next.js and FastAPI is enough to render basic pages, but memory, evidence indexing, and URL import will be incomplete without the other services.

## Local service map

| Service | Address | Purpose |
|---|---|---|
| Frontend | `http://localhost:3000` | Public application |
| FastAPI | `http://localhost:8000` | Local development API and `/docs` |
| Redis | `127.0.0.1:6379` | ARQ queues |
| Qdrant | `http://127.0.0.1:6333` | Dense + BM25 evidence retrieval |
| Playwright MCP | `http://127.0.0.1:8931/mcp` | Restricted JD URL import |

## Important environment variables

The canonical templates are [`config/backend.env.example`](config/backend.env.example) and [`config/frontend.env.example`](config/frontend.env.example).

| Variable | Default | Purpose |
|---|---|---|
| `LLM_PROVIDER` | `openai` | Model provider |
| `LLM_MODEL` | Template: `gpt-5-nano-2025-08-07` | Model identifier; editable in Settings |
| `LLM_API_KEY` | Empty | Optional environment override; can be saved in Settings |
| `LLM_API_BASE` | Empty | Ollama or compatible endpoint |
| `REQUEST_TIMEOUT_SECONDS` | `240` | Backend long-request timeout |
| `NEXT_PUBLIC_REQUEST_TIMEOUT_MS` | `240000` | Frontend timeout; keep aligned with the backend |
| `REDIS_URL` | `redis://127.0.0.1:6379/0` | ARQ queue |
| `QDRANT_URL` | `http://127.0.0.1:6333` | Vector database |
| `PLAYWRIGHT_MCP_URL` | `http://127.0.0.1:8931/mcp` | JD page-reading service |

## Verify the installation

```bash
# Backend tests; real-LLM evals are excluded by default
cd apps/backend
uv run pytest

# Frontend checks
cd ../frontend
npm test
npm run lint
npm run build

# Compose validation
cd ../..
docker compose config --quiet
```

## Troubleshooting

### The dashboard says “Configuration required”

This is expected on a clean install. Add a provider key or reachable local model in Settings, then test the connection.

### A worker is unhealthy

```bash
docker compose ps
docker compose logs memory-worker
docker compose logs resume-index-worker
docker compose logs redis qdrant
```

The first index run can download dense and sparse embedding models.

### JD URL import fails

```bash
docker compose logs playwright-mcp playwright-mcp-gateway mcp-egress-proxy
```

Localhost, private-network, link-local, cloud-metadata, and reserved addresses are rejected intentionally as SSRF protection.

### PDF export fails in local development

```bash
cd apps/backend
uv run playwright install chromium
```

Also confirm that the frontend is running at `FRONTEND_BASE_URL`.

### Get help

- [Report a bug](https://github.com/Strel1tz1a02/resume-agent/issues/new?template=bug_report.yml)
- [Request a feature](https://github.com/Strel1tz1a02/resume-agent/issues/new?template=feature_request.yml)
- [Developer documentation](docs/agent/README.md)
- [Testing strategy](docs/agent/testing-strategy.md)
