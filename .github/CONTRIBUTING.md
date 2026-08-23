# 参与 ResumeAgent

感谢你愿意帮助改进 ResumeAgent。修复缺陷、补充测试、完善文档和提出可复现的问题都很有价值。

- 使用问题、明确缺陷和功能建议请使用 [Issue 模板](https://github.com/Strel1tz1a02/resume-agent/issues/new/choose)。
- 安全漏洞不要公开提交 Issue，请阅读 [安全政策](SECURITY.md)。

项目的开发分支是 `main`，Pull Request 也应以 `main` 为目标分支。较大的功能、数据结构变更或界面重构，建议先开 Issue 对齐范围。

## 技术栈与环境要求

仓库由两个主要应用组成：

- `apps/backend`：Python 3.13、FastAPI、uv、pytest
- `apps/frontend`：Node.js 22、Next.js、npm、Vitest

推荐安装：

- Git
- Python 3.13
- [uv](https://docs.astral.sh/uv/)
- Node.js 22 与 npm
- Docker Engine / Docker Desktop（含 Compose v2；运行完整依赖栈时需要）

## 获取代码

1. 在 GitHub 上 [Fork 仓库](https://github.com/Strel1tz1a02/resume-agent/fork)。
2. 克隆自己的 Fork，并添加上游仓库：

   ```bash
   git clone https://github.com/<你的用户名>/resume-agent.git
   cd resume-agent
   git remote add upstream https://github.com/Strel1tz1a02/resume-agent.git
   ```

3. 从最新的 `main` 创建分支：

   ```bash
   git fetch upstream
   git switch -c fix/short-description upstream/main
   ```

## 启动项目

### 方式一：Docker Compose

这是验证完整服务栈最直接的方式：

```bash
docker compose up --build
```

服务就绪后打开 <http://localhost:3000>。停止并移除容器可运行：

```bash
docker compose down
```

首次构建需要下载镜像和依赖，耗时会比后续启动长。应用数据存放在 Docker volume 中，`docker compose down` 不会删除这些 volume。

### 方式二：本地开发

完整本地开发需要基础设施、两个后台 worker、FastAPI 和 Next.js。仅启动 FastAPI 与 Next.js 只能用于基础 UI/API 开发，记忆、经历索引、PDF 和 URL 导入等功能不会完整工作。更多配置与故障排查见 [完整安装说明](../SETUP.zh-CN.md)。

先在仓库根目录创建本地配置。macOS/Linux：

```bash
cp config/backend.env.example apps/backend/.env
cp config/frontend.env.example apps/frontend/.env.local
```

Windows PowerShell：

```powershell
Copy-Item config/backend.env.example apps/backend/.env
Copy-Item config/frontend.env.example apps/frontend/.env.local
```

需要 Redis、Qdrant 或网页导入能力时，可先启动配套服务：

```bash
docker compose up -d redis qdrant playwright-mcp-gateway
```

安装后端依赖和 PDF 渲染所需的 Chromium：

```bash
cd apps/backend
uv sync --extra dev
uv run playwright install chromium
```

终端 1，启动 FastAPI 后端：

```bash
cd apps/backend
uv run app
```

终端 2，启动 AI 对话记忆 worker：

```bash
cd apps/backend
uv run arq app.ai_chat.memory.worker.WorkerSettings
```

终端 3，启动经历索引 worker：

```bash
cd apps/backend
uv run arq app.resume_generation.index_worker.WorkerSettings
```

终端 4，启动 Next.js 前端（首次运行先安装依赖）：

```bash
cd apps/frontend
npm ci
npm run dev
```

前端默认地址为 <http://localhost:3000>，后端默认地址为 <http://localhost:8000>。LLM 提供商可在应用设置页配置；请勿把 API Key 写入代码、提交到 Git，或粘贴到公开 Issue。

## 验证改动

请至少运行与你改动相关的检查。CI 会执行以下默认检查：

| 范围 | 在对应目录运行 |
| --- | --- |
| 后端测试 | `cd apps/backend && uv run pytest` |
| 前端静态检查 | `cd apps/frontend && npm run lint` |
| 前端测试 | `cd apps/frontend && npm run test` |
| 前端生产构建 | `cd apps/frontend && npm run build` |
| 中英文词条结构 | 在仓库根目录运行 `python scripts/check_locale_parity.py` |

如果修改了 `apps/frontend/messages/*.json`，必须同时更新所有语言文件并运行 locale parity 检查。修复缺陷时，优先添加能在修复前失败、修复后通过的回归测试。

## 依赖与代码风格

- 后端依赖由 `apps/backend/pyproject.toml` 管理，添加依赖请在该目录使用 `uv add`。
- 前端依赖由 `apps/frontend/package.json` 和 `package-lock.json` 管理；变更依赖时请一并提交 lockfile。
- 前端格式和规则以 `npm run lint` 为准。
- 保持改动聚焦，不要顺手重排无关文件或提交生成文件、本地数据库、`.env`、API Key。

## 提交 Pull Request

1. 同步上游并解决冲突。
2. 运行适用的测试、lint、构建和 locale parity 检查。
3. 推送分支并向 `Strel1tz1a02/resume-agent:main` 发起 Pull Request。
4. 在 PR 中说明动机、关键改动和验证方式；有界面变化时附截图。
5. 使用 `Closes #123` 或 `Fixes #123` 关联 Issue。

提交后请留意 CI 与评审反馈。所有检查通过并不代表一定合并，但能让评审者可靠地复现和验证你的改动。
