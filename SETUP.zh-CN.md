# ResumeAgent 安装与配置

**简体中文** · [English](SETUP.md) · [返回 README](README.md)

本文提供两条经过仓库配置支持的启动路径：

- **普通用户：Docker Compose**，一次启动完整应用与依赖服务。
- **开发者：本地前后端 + Docker 基础设施**，支持热更新和单独调试。

## 方案 A：Docker Compose（推荐）

### 前置条件

- [Git](https://git-scm.com/)
- [Docker Desktop](https://www.docker.com/products/docker-desktop/)，或 Docker Engine + Compose v2
- 建议至少预留 6 GB 内存与 10 GB 磁盘空间

### 启动

```bash
git clone https://github.com/Strel1tz1a02/resume-agent.git
cd resume-agent
docker compose up --build -d
```

查看所有服务是否就绪：

```bash
docker compose ps
docker compose logs -f resume-matcher
```

打开 **<http://localhost:3000>**。后端只通过 Next.js 同源代理暴露，不需要单独访问 `8000` 端口。

### 首次配置

1. 打开右下角 **设置**。
2. 选择 OpenAI、Anthropic、Google、OpenRouter、DeepSeek、Groq、Ollama 或 OpenAI-compatible。
3. 云端提供商填写 API Key；本地服务填写 Base URL。
4. 点击 **保存**，再点击 **测试连接**。
5. 回到仪表板，从 **经历库** 开始整理真实经历证据。

API Key 会在本地 SQLite 数据库中加密保存。使用云端模型时，请注意简历与 JD 内容会发送到对应提供商。

### 停止、更新与备份

```bash
# 停止；保留数据卷
docker compose down

# 更新代码并重建
git pull
docker compose up --build -d
```

Compose 使用三个持久卷：`resume-data`、`redis-data`、`qdrant-data`。不要执行 `docker compose down -v`，除非你明确希望删除全部应用数据和索引。

### 使用 Ollama

先在宿主机安装 [Ollama](https://ollama.com/) 并拉取模型：

```bash
ollama pull gemma3:4b
```

在 ResumeAgent 设置中选择 **Ollama**：

- macOS / Windows Docker Desktop：Base URL 使用 `http://host.docker.internal:11434`
- Linux：使用可从容器访问的宿主机地址，或按你的 Docker 网络方案配置
- 模型填写实际已拉取的名称，例如 `gemma3:4b`

## 方案 B：本地开发

### 前置条件

| 工具 | 版本 |
|---|---|
| Python | 3.13 |
| Node.js | 22+ |
| npm | 10+ |
| uv | 最新稳定版 |
| Docker Compose | v2 |

检查环境：

```bash
python --version
node --version
npm --version
uv --version
docker compose version
```

### Windows 一键开发脚本

在 PowerShell 中运行：

```powershell
git clone https://github.com/Strel1tz1a02/resume-agent.git
Set-Location ResumeAgent
./start-dev.ps1 -Setup
```

脚本会：

- 从 `config/*.env.example` 创建缺失的本地环境文件；
- 启动 Redis、Qdrant 与隔离的 Playwright MCP；
- 使用 `uv` 安装并启动后端、Memory Worker 与 Resume Index Worker；
- 使用 npm 安装并启动前端。

后续启动不需要重复安装：

```powershell
./start-dev.ps1
```

如果基础设施已经由其他方式运行：

```powershell
./start-dev.ps1 -SkipInfrastructure
```

### 手动启动（macOS / Linux / Windows）

#### 1. 创建配置文件

macOS / Linux：

```bash
cp config/backend.env.example apps/backend/.env
cp config/frontend.env.example apps/frontend/.env.local
```

PowerShell：

```powershell
Copy-Item config/backend.env.example apps/backend/.env
Copy-Item config/frontend.env.example apps/frontend/.env.local
```

这两个 `config/*.env.example` 是仓库跟踪的唯一规范模板。不要依赖 `apps/**/.env.example`；fresh clone 中不存在这些文件。

#### 2. 启动基础设施

```bash
docker compose up -d redis qdrant playwright-mcp-gateway
```

本地将使用：

| 服务 | 地址 | 用途 |
|---|---|---|
| Redis | `127.0.0.1:6379` | 后台任务队列 |
| Qdrant | `http://127.0.0.1:6333` | dense + BM25 经历检索 |
| Playwright MCP | `http://127.0.0.1:8931/mcp` | 安全的 JD URL 导入 |

#### 3. 安装依赖

```bash
cd apps/backend
uv sync --extra dev
uv run playwright install chromium

cd ../frontend
npm ci
```

#### 4. 启动 4 个本地进程

分别打开终端，并保持运行：

```bash
# 终端 1：FastAPI
cd apps/backend
uv run app
```

```bash
# 终端 2：AI 对话记忆后台任务
cd apps/backend
uv run arq app.ai_chat.memory.worker.WorkerSettings
```

```bash
# 终端 3：经历索引后台任务
cd apps/backend
uv run arq app.resume_generation.index_worker.WorkerSettings
```

```bash
# 终端 4：Next.js
cd apps/frontend
npm run dev
```

加上 Docker 中的基础设施后，打开 **<http://localhost:3000>**。仅启动前端和 FastAPI 会让基础页面可见，但记忆、证据索引与 URL 导入等功能不完整。

## 关键环境变量

后端完整模板见 [`config/backend.env.example`](config/backend.env.example)，前端模板见 [`config/frontend.env.example`](config/frontend.env.example)。

| 变量 | 默认值 | 说明 |
|---|---|---|
| `LLM_PROVIDER` | `openai` | 模型提供商 |
| `LLM_MODEL` | 模板为 `gpt-5-nano-2025-08-07` | 模型名称，可在设置界面修改 |
| `LLM_API_KEY` | 空 | 可改为在设置界面保存 |
| `LLM_API_BASE` | 空 | Ollama 或兼容接口地址 |
| `REQUEST_TIMEOUT_SECONDS` | `240` | 后端长请求超时 |
| `NEXT_PUBLIC_REQUEST_TIMEOUT_MS` | `240000` | 前端超时，应与后端保持一致 |
| `REDIS_URL` | `redis://127.0.0.1:6379/0` | ARQ 队列 |
| `QDRANT_URL` | `http://127.0.0.1:6333` | 向量数据库 |
| `PLAYWRIGHT_MCP_URL` | `http://127.0.0.1:8931/mcp` | JD 页面读取服务 |

## 验证安装

```bash
# 后端测试（默认排除会调用真实模型的 eval）
cd apps/backend
uv run pytest

# 前端质量检查
cd ../frontend
npm test
npm run lint
npm run build

# Docker Compose 语法
cd ../..
docker compose config --quiet
```

后端接口文档在本地开发时可访问 **<http://localhost:8000/docs>**。Docker 全量运行时，公共入口仍是 **<http://localhost:3000>**。

## 常见问题

### 页面显示“需要配置”

这是首次启动的正常状态。进入设置添加 API Key，或配置可访问的 Ollama / OpenAI-compatible 服务，再测试连接。

### `docker compose up` 后某个 worker 不健康

```bash
docker compose ps
docker compose logs memory-worker
docker compose logs resume-index-worker
docker compose logs redis qdrant
```

索引 worker 首次运行可能需要下载 dense/sparse 模型，时间取决于网络。

### JD URL 导入失败

确认 `playwright-mcp`、`playwright-mcp-gateway` 与 `mcp-egress-proxy` 均处于运行或健康状态：

```bash
docker compose ps
docker compose logs playwright-mcp playwright-mcp-gateway mcp-egress-proxy
```

出于 SSRF 防护，localhost、私网、link-local、云元数据和保留地址会被拒绝，这是预期行为。

### PDF 导出失败

本地开发需要安装 Chromium：

```bash
cd apps/backend
uv run playwright install chromium
```

并确认前端运行在 `FRONTEND_BASE_URL` 指定的地址。

### 端口被占用

默认端口为 3000、8000、6379、6333 与 8931。Compose 部署可在根目录 `.env` 中修改宿主机映射变量（例如 `PORT=3001`），无需改变容器内部端口。手动本地开发若修改 Next.js 端口，则需同步更新后端 `FRONTEND_BASE_URL` 与 `CORS_ORIGINS`。

### 获取帮助

- [提交 Bug](https://github.com/Strel1tz1a02/resume-agent/issues/new?template=bug_report.yml)
- [提出功能建议](https://github.com/Strel1tz1a02/resume-agent/issues/new?template=feature_request.yml)
- [开发者文档](docs/agent/README.md)
- [测试策略](docs/agent/testing-strategy.md)
