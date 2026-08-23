# 外部集成

## 1) 集成清单

| 系统 | 类型 | 用途 | 鉴权 | 关键度 | 证据 |
|------|------|------|------|--------|------|
| OpenAI/Anthropic/DeepSeek/Google/Groq/Ollama/OpenRouter | LLM API/本地服务 | 聊天、抽取、规划、生成、评估 | provider API key；Ollama 可本地无 key | 高 | apps/backend/pyproject.toml；apps/backend/app/llm.py |
| SQLite 主库 | DB | 业务对象、Run、Tool Call/Interaction、Outbox、API key ciphertext | 文件系统权限 | 高 | apps/backend/app/db_engine.py；apps/backend/app/models.py |
| SQLite LangGraph checkpoint | DB | Graph 执行位置和临时状态 | 文件系统权限 | 高（Agent 恢复） | apps/backend/app/config.py；apps/backend/app/ai_chat/graph/runner.py |
| Redis / ARQ | Queue/cache | 记忆压缩、简历索引任务 | 本地 Compose 未配置密码 | 中高 | docker-compose.yml；apps/backend/app/ai_chat/memory/worker.py |
| Qdrant | Vector DB | Experience dense+sparse 检索 | URL + 可选 API key | 高（Resume Generation） | apps/backend/app/resume_generation/retriever.py；config/backend.env.example |
| FastEmbed | 本地 embedding | Qdrant dense/sparse 表示 | 无 | 中 | apps/backend/app/resume_generation/retriever.py |
| Playwright MCP | MCP/浏览器 | JD URL 内容获取 | Compose 内部网络；本地 gateway 只绑定 127.0.0.1；无应用层 token | 中 | apps/backend/app/jd_import/sources/playwright_mcp.py；docker-compose.yml |
| Playwright Chromium | 本地浏览器 | HTML 简历转 PDF | 无 | 中 | apps/backend/app/pdf.py；Dockerfile |
| Next.js reverse proxy | HTTP proxy | /api、docs、OpenAPI 同源代理到 FastAPI | 应用无统一用户鉴权 | 高 | apps/frontend/next.config.ts |
| GHCR | Container registry | 发布多架构镜像 | GitHub GITHUB_TOKEN | 中 | .github/workflows/docker-publish.yml |

发布 workflow 的 semver tag 触发只生成版本标签；`latest` 仅在手工 dispatch 时生成。若 Compose 继续默认使用 `:latest`，发布流程必须明确它何时刷新。

## 2) 数据存储

| Store | 角色 | 访问层 | 关键风险 | 证据 |
|-------|------|--------|----------|------|
| 主 SQLite | 领域数据与 Runtime durable truth | SQLAlchemy repositories、database.py | 单机写并发；大量手写启动迁移 | apps/backend/app/db_engine.py；apps/backend/app/database.py |
| checkpoint SQLite | Graph position/temp state | AsyncSqliteSaver / LangGraphDriver | 与主库非原子；硬崩溃后需对账 | apps/backend/app/ai_chat/graph/runner.py |
| Tool Call 表 | 当前 Interaction request/resolution/result | ToolCallStore、approval/input/execution | 通用 Interaction 与 Tool 生命周期耦合 | apps/backend/app/ai_chat/models/models.py |
| Redis | ARQ 队列 | memory/outbox/index workers | 本地无 auth；任务与 DB 一致性依赖 outbox | docker-compose.yml；apps/backend/app/background_jobs/ |
| Qdrant | 向量索引 | resume_generation/retriever.py | 索引新鲜度、外部可用性 | apps/backend/app/resume_generation/index_worker.py |
| config.json + encrypted API key table | 非密钥配置 + 加密凭据 | app/config.py、crypto.py | Fernet secret 丢失后 key 无法解密 | apps/backend/app/config.py；apps/backend/app/crypto.py |
| TinyDB | 旧 `database.json` 的一次性启动迁移源 | migrate_tinydb_to_sqlite.py | 依赖仍随生产包安装；迁移完成后主流程不再读写 TinyDB | apps/backend/app/scripts/migrate_tinydb_to_sqlite.py；apps/backend/pyproject.toml |

主 SQLite 启用 foreign_keys、WAL 与 5 秒 busy_timeout，见 apps/backend/app/db_engine.py:20。

Compose 的 `resume-data` 卷同时承载 `resume_matcher.db`、`ai_chat_checkpoints.db`、`config.json`、`.secret_key`、uploads 与 logs。备份/恢复必须把主库、checkpoint 和 `.secret_key` 作为一组；否则已加密 provider keys 无法解密。备份本身也会继承日志中的履历正文 PII，需要访问控制、保留期和安全销毁策略。

## 3) 凭据处理

- 仓库跟踪的环境变量模板：config/backend.env.example、config/frontend.env.example；本地启动脚本把它们复制到应用目录。
- LLM API key 可从 LLM_API_KEY 注入；UI 保存的 provider keys 使用 Fernet 加密后存 SQLite，不写回普通 config.json。
- 加密 secret 位于 DATA_DIR 内、gitignored；若丢失/轮换失败，decrypt 返回空并记录 warning。不能只备份 SQLite 而遗漏同卷 `.secret_key`。
- Qdrant 支持 QDRANT_API_KEY；Compose 本地服务默认不启用鉴权。
- Redis Compose 默认无密码；部署到不可信网络前必须补鉴权/网络隔离。
- Playwright MCP 当前按本机/Compose 私网服务设计，没有远端 token/TLS 配置；若改成远端共享服务，需要另行设计认证与传输加密。

## 4) 可靠性与失败行为

- LLM：app/llm.py 实现 provider 解析、超时、JSON 修复重试、截断识别与部分 fallback；不是统一 circuit breaker。
- Qdrant：配置 qdrant_timeout_seconds，retriever 调用有超时；索引通过 ARQ worker 异步更新。
- Compose 只向应用/worker硬编码传递 QDRANT_URL，未透传 QDRANT_API_KEY、collection、embedding models 与 timeout；自定义这些设置时需要同步修改 Compose。Qdrant 也没有 healthcheck，index worker 只等待 service_started。
- FastEmbed 模型没有烘焙进镜像或挂载独立缓存卷；首次索引需要联网下载，重建 worker 容器可能重复下载并延迟 readiness。
- Playwright MCP：asyncio.wait_for 包裹调用，超时映射为 source_timeout；Compose 通过受限出口代理拒绝 loopback、私网、link-local、metadata 与保留地址，并把已解析的公网 IP 固定到实际连接，降低 DNS rebinding 风险。
- PDF：导航、selector 与 fonts wait 均有显式上限。
- SQLite：CAS 用于 Run 与 Tool Call 并发；WAL/busy_timeout 减少短锁冲突。
- Runtime：Interaction resolution 有 client_resolution_id 幂等；Graph checkpoint 与主库间没有分布式事务、attempt lease 或自动 reconciliation。
- 前端：代理与 fetch 有 30 秒到 30 分钟的有界超时；Runtime SSE 没有重连/replay。
- `/api/v1/health` 只证明 FastAPI 进程可响应；`/status` 只覆盖 LLM 与旧数据库统计，不检查 Redis、Qdrant、Playwright MCP 或两个 worker，不能作为二开主链的 readiness。

## 5) 可观测性

- 外部调用使用 Python logging；后端写 apps/backend/data/logs/backend.log，部署日志由容器 stdout/volume 决定。
- Resume Generation 的检索 trace 当前默认开启，并会把 Experience `page_content` 作为 `searchable_text` 以 INFO 级别写入上述持久日志；默认生产日志不应记录正文，应改为 ID/score 或显式诊断开关。
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
- config/backend.env.example
- docker-compose.yml
- apps/backend/e2e_monitor/README.md
