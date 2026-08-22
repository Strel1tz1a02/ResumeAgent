# 技术栈

## 1) 运行时概览

| 区域 | 当前值 | 证据 |
|------|--------|------|
| 仓库形态 | apps/ 下的前后端单仓库；没有根级 workspace manifest | apps/backend/pyproject.toml；apps/frontend/package.json |
| 后端语言与运行时 | Python，声明 >=3.13；容器与 .python-version 均为 3.13 | apps/backend/pyproject.toml；apps/backend/.python-version；Dockerfile |
| 前端语言与运行时 | TypeScript 5；容器构建使用 Node 22 | apps/frontend/package.json；apps/frontend/tsconfig.json；Dockerfile |
| 包管理与构建 | 后端 pyproject + requirements，项目脚本/钩子使用 uv；前端 npm + package-lock | apps/backend/pyproject.toml；apps/backend/requirements.txt；.githooks/pre-push；apps/frontend/package-lock.json |
| 模块系统 | Python 包；Next.js App Router；TypeScript noEmit、strict | apps/backend/app/__init__.py；apps/frontend/app/；apps/frontend/tsconfig.json |

注意：本次验证实际使用仓库现有 .venv 的 Python 3.12.13，低于项目声明的 3.13；选定测试通过，但这不能替代 3.13 环境验证。

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
| Next.js / React | ^16.2.6 / ^19.2.4 | Web 前端 | apps/frontend/package.json |
| TanStack Query | ^5.101.4 | 前端服务端状态缓存 | apps/frontend/package.json；apps/frontend/lib/queries/experiences/provider.tsx |
| Tailwind CSS | ^4 | UI 样式 | apps/frontend/package.json；apps/frontend/postcss.config.mjs |

TinyDB 4.8.2 仍在依赖中，但当前主业务数据路径使用 SQLite/SQLAlchemy；其剩余用途与移除条件为 [TODO]。

## 3) 开发工具链

| 工具 | 用途 | 证据 |
|------|------|------|
| pytest / pytest-asyncio | 后端单元、集成、异步测试 | apps/backend/pyproject.toml |
| Ruff | 后端 lint/format 缓存存在；完整规则来源为 [TODO] | apps/backend/.ruff_cache/；apps/backend/pyproject.toml |
| Vitest / Testing Library / jsdom | 前端单元与组件测试 | apps/frontend/package.json；apps/frontend/vitest.config.ts；apps/frontend/vitest.setup.ts |
| ESLint 9 / Prettier 3.8 | 前端 lint/format | apps/frontend/eslint.config.mjs；apps/frontend/.prettierrc |
| TypeScript strict | 静态类型检查 | apps/frontend/tsconfig.json |
| Docker / Compose | 一体化镜像与多进程本地部署 | Dockerfile；docker-compose.yml |
| GitHub Actions | 发布 tag 镜像；不是 PR 测试流水线 | .github/workflows/docker-publish.yml |
| pre-push hook | 后端全测、locale parity、可用时运行前端全测 | .githooks/pre-push |

## 4) 关键命令

~~~bash
# 后端
cd apps/backend
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

后端安装的唯一规范命令在仓库文档中并不完全一致（requirements 与 pyproject 并存），应标记为 [ASK USER]。

## 5) 环境与配置

- 后端配置源：apps/backend/.env.example、apps/backend/.env.sample、apps/backend/app/config.py、运行时生成的 apps/backend/data/config.json。
- 关键后端变量：LLM_PROVIDER、LLM_MODEL、LLM_API_KEY、QDRANT_*、PLAYWRIGHT_MCP_*、REQUEST_TIMEOUT_SECONDS、CORS_ORIGINS、HOST、PORT。
- 前端变量：BACKEND_ORIGIN、NEXT_PUBLIC_API_URL、NEXT_PUBLIC_REQUEST_TIMEOUT_MS，见 apps/frontend/.env.sample 与 apps/frontend/next.config.ts。
- 部署约束：生产镜像以非 root 用户运行；同时需要 SQLite 数据卷、Redis、Qdrant，并安装 Playwright Chromium，见 Dockerfile 与 docker-compose.yml。
- 超时约束：前端代理、客户端与后端请求超时应保持一致，见 apps/frontend/.env.sample、apps/frontend/next.config.ts、apps/backend/app/config.py。

## 6) 文档与实现偏差

- README.zh-CN.md 仍写 Next.js 15；manifest 是 Next.js ^16.2.6。
- docs/agent/coding-standards.md 写 Python 3.11+；运行时声明是 3.13。
- docs/agent/workflow.md 提到 Jest；当前测试框架是 Vitest。

## 7) Evidence

- apps/backend/pyproject.toml
- apps/backend/.python-version
- apps/frontend/package.json
- apps/frontend/tsconfig.json
- Dockerfile
- docker-compose.yml
- .githooks/pre-push
- .github/workflows/docker-publish.yml
