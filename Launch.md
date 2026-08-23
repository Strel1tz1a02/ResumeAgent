# ResumeAgent 启动速查

完整说明见 [中文安装指南](SETUP.zh-CN.md) / [English setup guide](SETUP.md)。

## 普通用户

```bash
docker compose up --build -d
```

打开 <http://localhost:3000>，进入设置配置模型提供商。

## Windows 本地开发

```powershell
./start-dev.ps1 -Setup
```

后续启动使用 `./start-dev.ps1`。脚本会启动 Redis、Qdrant、Playwright MCP、FastAPI、两个 ARQ worker 与 Next.js。

## 手动开发

```bash
docker compose up -d redis qdrant playwright-mcp-gateway
```

然后在不同终端分别启动：

```bash
# 终端 1
cd apps/backend
uv run app
```

```bash
# 终端 2
cd apps/backend
uv run arq app.ai_chat.memory.worker.WorkerSettings
```

```bash
# 终端 3
cd apps/backend
uv run arq app.resume_generation.index_worker.WorkerSettings
```

```bash
# 终端 4
cd apps/frontend
npm run dev
```
