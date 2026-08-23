# 技术栈

## 1) 运行时概览

| 区域 | 当前值 | 证据 |
|------|--------|------|
| 仓库形态 | apps/ 下的前后端单仓库；没有根级 workspace manifest | apps/backend/pyproject.toml；apps/frontend/package.json |
| 后端语言与运行时 | Python，声明 >=3.13；容器与 .python-version 均为 3.13 | apps/backend/pyproject.toml；apps/backend/.python-version；Dockerfile |
| 前端语言与运行时 | TypeScript 5；容器构建使用 Node 22 | apps/frontend/package.json；apps/frontend/tsconfig.json；Dockerfile |
| 包管理与构建 | 后端以 pyproject 为规范 manifest、requirements 为已漂移的遗留清单，项目脚本/钩子使用 uv；前端 npm + package-lock | apps/backend/pyproject.toml；apps/backend/requirements.txt；.githooks/pre-push；apps/frontend/package-lock.json |
| 模块系统 | Python 包；Next.js App Router；TypeScript noEmit、strict | apps/backend/app/__init__.py；apps/frontend/app/；apps/frontend/tsconfig.json |

注意：本次验证实际使用仓库现有 .venv 的 Python 3.12.13，低于项目声明的 3.13；选定测试通过，但这不能替代 3.13 环境验证。表中依赖版本描述的是当前工作树；origin HEAD 的后端/前端版本仍为 1.2.0、Next.js 仍为 ^16.2.6，工作树才改为 2.0.0 与 ^16.3.2。

## 2) 生产框架与关键依赖

| 依赖 | 版本 | 作用 | 证据 |
|------|------|------|------|
| FastAPI / Uvicorn | 0.128.4 / 0.40.0 | HTTP API、SSE 服务 | apps/backend/pyproject.toml |
| Pydantic / pydantic-settings | 2.12.5 / 2.14.2 | 请求、配置、领域 schema | apps/backend/pyproject.toml；apps/backend/app/config.py |
| SQLAlchemy async / aiosqlite | 2.0.36 / 0.20.0 | SQLite 主数据、Run、Interaction 持久化 | apps/backend/pyproject.toml；apps/backend/app/db_engine.py |
| LangGraph / checkpoint-sqlite | 1.2.10 / 3.1.0 | 业务 Graph 与 checkpoint | apps/backend/pyproject.toml；apps/backend/app/ai_chat/graph/driver.py |
| LangChain 及 provider adapters | 1.3.14；各 provider 版本见 manifest | 统一模型接入 | apps/backend/pyproject.toml；apps/backend/app/llm.py |
| ARQ / Redis | 0.28.0 / Docker Redis 7 | 记忆压缩、简历索引后台任务 | apps/backend/pyproject.toml；docker-compose.yml |
| Qdrant / FastEmbed | client 1.19.0 / 0.8.0；server 1.18.2 | 经历向量检索 | apps/backend/pyproject.toml；docker-compose.yml |
| MCP / Playwright | 2.0.0 / 1.58.0 | JD 页面采集、PDF 浏览器渲染 | apps/backend/pyproject.toml；apps/backend/app/jd_import/sources/playwright_mcp.py；apps/backend/app/pdf.py |
| Next.js / React | ^16.3.2 / ^19.2.4 | Web 前端 | apps/frontend/package.json |
| TanStack Query | ^5.101.4 | 前端服务端状态缓存 | apps/frontend/package.json；apps/frontend/lib/queries/experiences/provider.tsx |
| Tailwind CSS | ^4 | UI 样式 | apps/frontend/package.json；apps/frontend/postcss.config.mjs |

TinyDB 4.8.2 只在启动时的一次性旧库导入器中按需导入；主业务读写全部走 SQLite/SQLAlchemy。证据：apps/backend/app/scripts/migrate_tinydb_to_sqlite.py:47、apps/backend/app/database.py。

## 3) 开发工具链

| 工具 | 用途 | 证据 |
|------|------|------|
| pytest / pytest-asyncio | 后端单元、集成、异步测试 | apps/backend/pyproject.toml |
| 后端静态检查 | 仓库未跟踪 Ruff/Black/Mypy 配置，也没有正式 lint 脚本；`.ruff_cache` 不能作为规则来源 | apps/backend/pyproject.toml；git ls-files |
| Vitest / Testing Library / jsdom | 前端单元与组件测试 | apps/frontend/package.json；apps/frontend/vitest.config.ts；apps/frontend/vitest.setup.ts |
| ESLint 9 / Prettier 3.8 | 前端 lint/format | apps/frontend/eslint.config.mjs；apps/frontend/.prettierrc |
| TypeScript strict | 静态类型检查 | apps/frontend/tsconfig.json |
| Docker / Compose | 一体化镜像与多进程本地部署 | Dockerfile；docker-compose.yml |
| GitHub Actions | 当前工作树的 ci.yml 在 push/PR 上运行后端、前端、容器与 locale 检查；docker-publish.yml 负责 tag/manual 镜像发布 | .github/workflows/ci.yml；.github/workflows/docker-publish.yml |
| pre-push hook | 后端默认 pytest 集、locale parity、可用时运行前端全测；默认集遗漏内嵌 memory tests | .githooks/pre-push；apps/backend/pyproject.toml |

## 4) 关键命令

~~~bash
# 后端
cd apps/backend
uv sync --extra dev
uv run pytest

# 前端
cd apps/frontend
npm ci
npm run dev
npm test
npm run lint
npm run build

# 容器
docker compose up
~~~

开发环境以 `pyproject.toml + uv sync --extra dev` 为规范路径；Docker 使用 `pip install .` 安装同一 pyproject 包。`requirements.txt` 是并行遗留清单且已与 pyproject 漂移（缺 `mcp`、`pdfminer.six`），不应作为规范安装入口。

## 5) 环境与配置

- 后端配置源：仓库跟踪的唯一环境模板是 config/backend.env.example；定义在 apps/backend/app/config.py；非密钥运行时配置可写入 apps/backend/data/config.json。
- 关键后端变量：LLM_PROVIDER、LLM_MODEL、LLM_API_KEY、QDRANT_*、PLAYWRIGHT_MCP_*、REQUEST_TIMEOUT_SECONDS、CORS_ORIGINS、HOST、PORT。
- 前端变量：BACKEND_ORIGIN、NEXT_PUBLIC_API_URL、NEXT_PUBLIC_REQUEST_TIMEOUT_MS，见 config/frontend.env.example 与 apps/frontend/next.config.ts。
- 部署约束：生产镜像以非 root 用户运行；同时需要 SQLite 数据卷、Redis、Qdrant，并安装 Playwright Chromium，见 Dockerfile 与 docker-compose.yml。
- 超时约束：前端代理、客户端与后端请求超时应保持一致，见 config/frontend.env.example、apps/frontend/next.config.ts、apps/frontend/lib/api/client.ts、apps/backend/app/config.py。

## 6) 文档与实现偏差

- docs/agent/coding-standards.md 写 Python 3.11+；运行时声明是 3.13。
- docs/agent/workflow.md 提到 Jest；当前测试框架是 Vitest。
- `.claude/CLAUDE.md` 与 docs/agent/testing-strategy.md 仍写“没有 PR CI”，但当前工作树已新增 `.github/workflows/ci.yml`；是否保留 PR 触发属于团队策略问题。
- 本机核验环境为 Python 3.12.13 与 Node 24.18.0，分别低于/高于项目声明的 Python 3.13、Node 22；本机结果不能替代声明版本上的 CI。
- 本机 PATH 没有 `uv` 或 Docker CLI；`start-dev.ps1 -ValidateOnly` 只通过语法/配置预检，实际启动会在依赖检查阶段停止。Compose 校验是通过 Docker 的绝对路径完成，不代表文档中的裸 `docker` 命令在当前 shell 可直接运行。
- origin HEAD 的版本、Compose 默认镜像、publish workflow 镜像坐标和启动文档并不一致；当前工作树正在统一这些值，但尚未形成可从 fresh clone 复现的已提交发布状态。

## 7) Evidence

- apps/backend/pyproject.toml
- apps/backend/.python-version
- apps/frontend/package.json
- apps/frontend/tsconfig.json
- Dockerfile
- docker-compose.yml
- config/backend.env.example
- config/frontend.env.example
- .githooks/pre-push
- .github/workflows/ci.yml
- .github/workflows/docker-publish.yml
