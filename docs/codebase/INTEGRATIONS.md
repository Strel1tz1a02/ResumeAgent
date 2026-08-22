# 外部集成

## 1) 集成清单

| 系统 | 类型 | 用途 | 鉴权 | 关键度 | 证据 |
|------|------|------|------|--------|------|
| OpenAI/Anthropic/DeepSeek/Google/Groq/Ollama/OpenRouter | LLM API/本地服务 | 聊天、抽取、规划、生成、评估 | provider API key；Ollama 可本地无 key | 高 | apps/backend/pyproject.toml；apps/backend/app/llm.py |
| SQLite 主库 | DB | 业务对象、Run、Tool Call/Interaction、Outbox、API key ciphertext | 文件系统权限 | 高 | apps/backend/app/db_engine.py；apps/backend/app/models.py |
| SQLite LangGraph checkpoint | DB | Graph 执行位置和临时状态 | 文件系统权限 | 高（Agent 恢复） | apps/backend/app/config.py；apps/backend/app/ai_chat/graph/runner.py |
| Redis / ARQ | Queue/cache | 记忆压缩、简历索引任务 | 本地 Compose 未配置密码 | 中高 | docker-compose.yml；apps/backend/app/ai_chat/memory/worker.py |
| Qdrant | Vector DB | Experience dense+sparse 检索 | URL + 可选 API key | 高（Resume Generation） | apps/backend/app/resume_generation/retriever.py；apps/backend/.env.example |
| FastEmbed | 本地 embedding | Qdrant dense/sparse 表示 | 无 | 中 | apps/backend/app/resume_generation/retriever.py |
| Playwright MCP | MCP/浏览器 | JD URL 内容获取 | URL；服务端鉴权策略为 [TODO] | 中 | apps/backend/app/jd_import/sources/playwright_mcp.py |
| Playwright Chromium | 本地浏览器 | HTML 简历转 PDF | 无 | 中 | apps/backend/app/pdf.py；Dockerfile |
| Next.js reverse proxy | HTTP proxy | /api、docs、OpenAPI 同源代理到 FastAPI | 应用无统一用户鉴权 | 高 | apps/frontend/next.config.ts |
| GHCR | Container registry | 发布多架构镜像 | GitHub GITHUB_TOKEN | 中 | .github/workflows/docker-publish.yml |

## 2) 数据存储

| Store | 角色 | 访问层 | 关键风险 | 证据 |
|-------|------|--------|----------|------|
| 主 SQLite | 领域数据与 Runtime durable truth | SQLAlchemy repositories、database.py | 单机写并发；大量手写启动迁移 | apps/backend/app/db_engine.py；apps/backend/app/database.py |
| checkpoint SQLite | Graph position/temp state | AsyncSqliteSaver / LangGraphDriver | 与主库非原子；硬崩溃后需对账 | apps/backend/app/ai_chat/graph/runner.py |
| Tool Call 表 | 当前 Interaction request/resolution/result | ToolCallStore、approval/input/execution | 通用 Interaction 与 Tool 生命周期耦合 | apps/backend/app/ai_chat/models/models.py |
| Redis | ARQ 队列 | memory/outbox/index workers | 本地无 auth；任务与 DB 一致性依赖 outbox | docker-compose.yml；apps/backend/app/background_jobs/ |
| Qdrant | 向量索引 | resume_generation/retriever.py | 索引新鲜度、外部可用性 | apps/backend/app/resume_generation/index_worker.py |
| config.json + encrypted API key table | 非密钥配置 + 加密凭据 | app/config.py、crypto.py | Fernet secret 丢失后 key 无法解密 | apps/backend/app/config.py；apps/backend/app/crypto.py |
| TinyDB | 遗留依赖/迁移源 | 精确剩余生产用途 [TODO] | 双存储心智负担 | apps/backend/pyproject.toml；README.zh-CN.md |

主 SQLite 启用 foreign_keys、WAL 与 5 秒 busy_timeout，见 apps/backend/app/db_engine.py:20。

## 3) 凭据处理

- 环境变量来源：apps/backend/.env.example、apps/backend/.env.sample、config/backend.env.example。
- LLM API key 可从 LLM_API_KEY 注入；UI 保存的 provider keys 使用 Fernet 加密后存 SQLite，不写回普通 config.json。
- 加密 secret 位于 DATA_DIR 内、gitignored；若丢失/轮换失败，decrypt 返回空并记录 warning。
- Qdrant 支持 QDRANT_API_KEY；Compose 本地服务默认不启用鉴权。
- Redis Compose 默认无密码；部署到不可信网络前必须补鉴权/网络隔离。
- Playwright MCP 的远端认证和 TLS 要求没有在仓库中定义，[TODO]。

## 4) 可靠性与失败行为

- LLM：app/llm.py 实现 provider 解析、超时、JSON 修复重试、截断识别与部分 fallback；不是统一 circuit breaker。
- Qdrant：配置 qdrant_timeout_seconds，retriever 调用有超时；索引通过 ARQ worker 异步更新。
- Playwright MCP：asyncio.wait_for 包裹调用，超时映射为 source_timeout。
- PDF：导航、selector 与 fonts wait 均有显式上限。
- SQLite：CAS 用于 Run 与 Tool Call 并发；WAL/busy_timeout 减少短锁冲突。
- Runtime：Interaction resolution 有 client_resolution_id 幂等；Graph checkpoint 与主库间没有分布式事务、attempt lease 或自动 reconciliation。
- 前端：代理与 fetch 有 30 秒到 30 分钟的有界超时；Runtime SSE 没有重连/replay。

## 5) 可观测性

- 外部调用使用 Python logging；后端写 apps/backend/data/logs/backend.log，部署日志由容器 stdout/volume 决定。
- Run、Tool Call、Outbox 和失败 code 提供业务级审计线索。
- e2e_monitor 可生成持久证据包，但它是 opt-in report，不是门禁。
- 没有发现统一 metrics、distributed tracing、SLO 或 alert 配置。
- 缺口：无法直接观测 stale running、checkpoint/Run 漂移、事件丢失/重放、Interaction 等待时长。

## 6) Evidence

- apps/backend/app/config.py
- apps/backend/app/crypto.py
- apps/backend/app/db_engine.py
- apps/backend/app/llm.py
- apps/backend/app/ai_chat/graph/runner.py
- apps/backend/app/jd_import/sources/playwright_mcp.py
- apps/backend/app/resume_generation/retriever.py
- apps/frontend/next.config.ts
- docker-compose.yml
- apps/backend/e2e_monitor/README.md

