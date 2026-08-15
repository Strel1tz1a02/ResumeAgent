- Windows 本地开发推荐使用一键启动脚本。首次运行或依赖变化时加 `-Setup`：
```bash
.\start-dev.ps1
.\start-dev.ps1 -Setup
```

脚本默认使用 `E:\MiniConda` 下的 `resume-matcher` 环境；它会创建缺失的
`.env`/`.env.local`，按需启动 Docker Desktop，并启动 Redis、Qdrant、后端、
Memory Worker、Resume Index Worker 和前端。若 Redis/Qdrant 已由其他方式运行：

```bash
.\start-dev.ps1 -SkipInfrastructure
```

手动启动时，先启动基础设施：

```bash
docker compose up -d redis qdrant
```

然后分别启动以下进程：

```bash
cd apps/backend
conda run -n resume-matcher python -m app.main
conda run -n resume-matcher arq app.ai_chat.memory.worker.WorkerSettings
conda run -n resume-matcher arq app.resume_generation.index_worker.WorkerSettings
cd apps/frontend
npm run dev
```

使用 Docker 全量启动时执行 `docker compose up -d`，`resume-index-worker` 会自动为
既有经历补建索引。
