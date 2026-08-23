# 代码库关注点

## 1) Top risks（按优先级）

| 严重度 | 关注点 | 证据 | 影响 | 建议动作 |
|--------|--------|------|------|----------|
| 高 | 无用户鉴权却存在配置、密钥与全库破坏端点 | routers/config.py:568-639；docker-compose.yml | 一旦 Web 端口暴露到不可信网络，任意访问者可读写业务数据、改模型配置、清 key/重置库 | 明确只允许本机/可信网；公网化前先加 authn/authz、CSRF/资源所有权与高风险操作再认证 |
| 高 | “重置全部数据”没有覆盖二开领域、API keys 与持久日志 | database.py:762-795；routers/config.py:612-639；resume_generation/retriever.py:216-239,348-351；main.py:84-99 | UI/API 声称删除所有内容，但加密 API keys、Experience、Evidence、JD、Qdrant 和可能含经历正文的 backend.log 仍保留，造成隐私与一致性误判 | 建立所有表/索引/文件清单；明确 key 是否保留，清库后清 Qdrant 与受管日志，并加入隐私删除集成测试；若不能保证则修正文案 |
| 高 | 硬崩溃后没有通用的 stale current Run 认领入口 | ai_chat_service.py:124-159,330-464；graph/runner.py:82-111 | 带持久 Interaction 的 resolve 路径可 recover，但无待解决 Interaction 的 current Run 仍会阻塞新消息 | 增加 run attempt、owner lease/heartbeat、启动/按需 reconciliation 与显式 recover API |
| 高 | Run DB 与 checkpoint DB 非原子且缺系统对账 | db_engine.py；graph/runner.py；run_lifecycle.py | checkpoint 已前进但 Run 未 settle，或反向漂移 | 定义恢复矩阵，以 attempt/version 对账并测试每个 crash window |
| 高 | 事件不是 durable log | streaming/sse.py:36；frontend/runtime-events.ts | sequence 每条连接重置；断线/刷新无法去重、续传或补发 terminal event | durable event/outbox、全 Run 序号、cursor/Last-Event-ID、前端 replay reducer |
| 高 | JD 完成后的聚合结果无法可靠补发 | jd_import/graph/builder.py:280；ai_chat_service.py:382 | JD 已落库但 settle/event 前崩溃时，恢复只可能补发 Tool Result，不会重建 persisted_ids/errors | 将领域 Result 持久化为独立 artifact/result record，再由 Runtime 投递 |
| 高 | Resume Generation 无 checkpoint/recover | resume_generation/service.py:105；resume_generation/graph.py | preview 执行中进程死亡会永久 running，只能人工修库 | 要么声明同步短任务且提供安全重跑，要么接入 attempt/checkpoint/恢复 |
| 中高 | Interaction 仍依附 Tool Call，且一次只允许一个 active interrupt | models/models.py:125；adapters/base.py:54；graph/driver.py:103 | 非工具交互需要 pseudo-tool；无法支持并行审批/子 Agent join | 抽 InteractionStore，定义 InteractionSet 与 join 语义 |
| 中高 | 恢复阶段 output.delta 未聚合/持久化 | ai_chat_service.py:430；run_lifecycle.py:101 | 新业务若恢复后继续生成文本，会出现 UI 有流但消息/Memory 无事实 | start/resume 共用同一 output collector 与 settle 路径 |
| 中 | 前端 pending Interaction 只在组件内存 | use-experience-ai-chat.tsx；jd-import-workspace.tsx | 刷新/离页后无法重建审批或问题批次 UI | 增加 GET run/pending-interactions 与 hydration |
| 中高 | 当前 origin HEAD 实际缺测试门禁 | .githooks/pre-push；git config core.hooksPath；.github/workflows/ci.yml；git status | pre-push 在 HEAD 丢失 executable bit、本 clone 未启用 hooksPath；新 ci.yml 又尚未跟踪 | 先决定 CI 策略并提交 ci.yml；恢复 hook 可执行位并在 fresh clone 验证；不要把本地绿测等同远端门禁 |
| 中高 | 发布坐标在 HEAD 与工作树之间不闭环 | pyproject.toml；package.json；docker-compose.yml；docker-publish.yml；git status | HEAD 仍是 1.2.0/Next 16.2.6，Compose 拉上游镜像，publish workflow 又使用旧的无横线仓库名；当前工作树已把版本、Compose、publish workflow 与新 README 统一，但改动尚未提交，fresh clone 仍可能运行错误镜像 | 把版本、Compose 镜像、publish workflow、README/SETUP 与 CI 作为一个原子发布变更提交，并决定 tag 是否自动刷新 latest |

一句话判断：当前已经统一了“正常路径控制面”，尚未统一“失败后的恢复控制面”。

## 2) 技术债

扫描器在排除测试目录后没有发现生产源码中的 TODO/FIXME/HACK；下面的问题来自可执行路径、依赖清单、测试缺口与 Git/部署状态，不是把注释数量当作风险代理。

| 债务 | 原因 | 位置 | 不处理的风险 | 建议 |
|------|------|------|--------------|------|
| 通用 Runtime 仍命名为 app.ai_chat | 从既有聊天框架增量抽取 | app/ai_chat/protocol.py、graph/driver.py、run_state.py、context/ | Resume/未来业务误以为必须依赖 Chat；边界继续模糊 | 稳定接口后迁到 app/agent_runtime，ai_chat 保留 conversation coordinator |
| BaseAdapter 暴露 LangGraph StateGraph | 当前只支持 LangGraph | ai_chat/adapters/base.py；graph/runner.py | Driver 看似抽象但构建/编译仍锁定 LangGraph | 若确有多引擎需求，再抽 CompiledGraph/GraphFactory 端口；否则明确这是 LangGraph harness |
| 没有 RuntimeCommand discriminated union | 实现依靠 Python 类型分派 | ai_chat/protocol.py | wire schema/跨语言生成困难 | 定义带 type/version 的 command envelope |
| 前后端手写弱类型事件 | 先统一 envelope，未引入 schema 生成 | streaming/events.py；frontend/runtime-events.ts | payload 漂移只在运行时暴露 | 以 JSON Schema/OpenAPI 生成 core event union，payload.kind 由业务扩展 |
| RunLifecycleService 仍对话特化 | 原子提交包含 assistant message/tool delivery | ai_chat/services/run_lifecycle.py | 误把它当通用生命周期会迫使 Resume 适配聊天概念 | 保留通用 RunCoordinator/StateMachine，conversation lifecycle 作为插件 |
| 启动时手写迁移持续增长 | 项目历史采用幂等 SQL | app/db_engine.py | 顺序、回滚、并发升级越来越难 | 评估 Alembic 或至少版本化 migration registry |
| 公共 session/outbox 存在领域反向耦合 | 从 Experience 增量抽出公共能力 | experience/repositories/session.py；experience/services/*；resume_generation/indexing.py | 新领域继续复用会让依赖方向变得含糊 | 把 request session/outbox 端口移到公共 persistence/background 包 |
| 两份 Python 依赖清单漂移 | pyproject 与 requirements 并行维护 | apps/backend/pyproject.toml；requirements.txt | requirements 安装缺 mcp、pdfminer.six，环境不可复现 | 只保留一个规范 manifest，并提交 uv.lock 或自动生成 requirements |
| 旧文档漂移 | 版本/架构快速变化 | docs/agent/；.claude/CLAUDE.md；agent-development-reading-path.zh-CN.md | 新成员会按旧 Python/Jest/无 CI 策略和已删除路径理解系统 | 以 docs/codebase 为事实基线，修正旧文档或明确标成历史 |

## 3) 安全关注

| 风险 | OWASP | 证据 | 当前缓解 | 缺口 |
|------|-------|------|----------|------|
| 应用 API 无用户鉴权/资源所有权 | A01 Broken Access Control | routers/config.py:568-639；各 router 仅注入 DB session | README 仅在文档上定位为本地单用户，没有技术性访问控制缓解 | Compose Web 端口默认绑定所有接口；公开确认串与 CORS 都不能替代用户身份和权限 |
| 保存 LLM 配置或调用 `/config/llm-test` 时接受任意 api_base，并可能附带已存 provider key | A10 SSRF / 敏感信息泄露 | routers/config.py:143-210；llm.py:447-451 | provider/schema 校验与请求超时 | 保存配置后会立即后台探活；公网攻击者可让后端携带用户 key 请求其服务器。必须鉴权并限制目标，且不得把已存密钥隐式复用于不可信 endpoint |
| 本地 Redis/Qdrant 默认无鉴权 | A05 Security Misconfiguration | docker-compose.yml | 宿主机端口只绑定 127.0.0.1，Compose 网络隔离 | 多机/反向代理部署需密钥、TLS、ACL；Compose 未透传 QDRANT_API_KEY |
| JD URL 抓取 SSRF | A10 SSRF | jd_import/sources/url_policy.py；docker/mcp-egress-proxy.mjs | Compose 出口代理拒绝内网/metadata/保留地址并 pin resolved IP，已覆盖 DNS rebinding 主路径 | 若关闭 PLAYWRIGHT_MCP_EGRESS_SECURED 或改用远端 MCP，必须重新证明同等网络边界 |
| API key 加密 secret 丢失或备份拆组 | N/A | app/crypto.py；docker-compose.yml 的 resume-data | Fernet at rest、secret gitignored、原子写 | resume-data 同时含主库、checkpoint、config、`.secret_key`、uploads 与 logs；必须成组备份，否则 ciphertext 不可恢复，且备份会继承日志 PII；缺少备份/轮换/销毁 runbook |
| LLM 输入与检索 trace 包含用户/履历内容 | N/A | app/llm.py；context/assembler.py；resume_generation/graph.py:103-117；retriever.py:202-239,348-351；main.py:84-99 | LLM 有 trust boundary 标记与 LOG_LLM 开关；普通 `retrieve()` 关闭 trace | 生产 Resume Generation 路径调用 `retrieve_with_trace()`，默认在 INFO 日志写 `page_content/searchable_text`；应只记 ID/score，正文需显式诊断开关和保留期，provider 策略与用户告知仍为 [TODO] |

## 4) 性能与扩展性

| 关注点 | 证据 | 当前症状 | 扩展风险 | 建议 |
|--------|------|----------|----------|------|
| SQLite 多写者 + checkpoint 独立库 | db_engine.py；graph/runner.py | 通过 WAL/busy_timeout 缓解 | 多进程/高并发下锁竞争与一致性恢复复杂 | 明确单实例容量；压测后决定 Postgres/checkpoint backend |
| Resume snapshot 加载可能 N+1 | resume_generation/service.py:295 | 每个 Experience 查询 evidence | Experience 数量增加后预览延迟线性放大 | 批量 preload evidence 或 repository join |
| 超大模块职责集中 | routers/resumes.py 2056 LOC；services/improver.py 1514；app/llm.py 1107；JD workspace 947 | 修改影响面大 | 难并行开发、回归成本高 | 按 use case/provider/view-state 拆分，先补 characterization tests |
| 无性能门禁 | docs/codebase/.codebase-scan.txt | 无基准 | LLM、PDF、Qdrant、Graph 改动可能静默退化 | 建立关键路径 latency/token/DB query benchmark |
| SSE 无 backpressure/replay | streaming/sse.py；runtime-events.ts | 当前短流可接受 | 长流/重连会重复或丢事件 | bounded queue、heartbeat、cursor 与 consumer lag 指标 |
| 健康检查不覆盖二开依赖 | routers/health.py；docker-compose.yml | `/health` 恒定 healthy，`/status` 不检查 Redis、Qdrant、MCP 与 workers | 容器绿但 Memory、索引、URL 导入仍不可用 | 增加分层 readiness，并让 Compose 依赖真实检查而不是仅进程存活 |

## 5) Fragile / high-churn areas

| 区域 | 为什么脆弱 | 90 天 churn 信号 | 安全改法 |
|------|------------|-----------------|----------|
| tests/unit/test_experience_ai_chat.py | 同时覆盖 Graph、DB、恢复、并发 | 22 commits | 保留真实组合测试，新增 crash-window 参数化测试 |
| frontend Experience library/page | 大组件 + cache + editor + AI chat | test 22、page 16 | 先抽 view model/runtime reducer，再拆 UI |
| app/db_engine.py | 启动、engine、迁移集中 | 15 | 每个迁移有独立回归测试，避免改旧迁移 |
| Tool Call 生命周期 | Interaction、幂等、执行、delivery 交叉 | test_tool_call_service 14 | CAS invariant 测试先行，逐步抽 Interaction |
| Graph runner/runtime | 统一控制面的中心边界 | runner 12、runtime 11 | 维持 import boundary + real checkpoint contract tests |
| experience/graph/builder.py | 节点、approval、结果事件交叉 | 11 | 业务 Graph 保持本地，禁止 Runtime 加业务分支 |
| app/main.py | router、adapter、生产依赖 composition root | 11 | 只做注册，不放领域逻辑；补生产装配契约测试 |

来源：docs/codebase/.codebase-scan.txt 的 HIGH-CHURN FILES；扫描包含工作树生成物，因此这里只采用可核验的源码路径。

## 6) [ASK USER] Questions

1. [ASK USER] “中断恢复”是否只要求重复 resolve 幂等，还是必须覆盖进程硬崩溃、浏览器刷新、断线重连与事件补发？这决定下一阶段是补 API，还是建设 attempt/lease/reconciliation/event log。
2. [ASK USER] failed/cancelled 是真正终态，还是允许人工/系统恢复到 running/completed？设计稿与 RunStateMachine 当前实现相反。
3. [ASK USER] Resume Generation 保持同步 REST 是产品决策，还是也应进入统一 SSE、取消、checkpoint 与恢复控制面？
4. [ASK USER] 是否现在拆独立 InteractionStore，还是接受 approval/question_batch 继续依附 Tool Call，等出现第三类交互再拆？
5. [ASK USER] “事件统一”的完成口径是单一 envelope/parser，还是还包含共享强类型 schema、全 Run sequence、replay/dedupe 与 UI hydration？
6. [ASK USER] 是否把通用模块从 app.ai_chat 物理上移到 app.agent_runtime？建议先稳定恢复语义，再移动包，避免一次改两类边界。
7. [ASK USER] ContextAssembler 的统一范围仅限新 Agent Runtime，还是要覆盖旧 resumes/improver/refiner 的全部 LLM 调用？
8. [ASK USER] 是否将 docs/codebase 作为代码事实源，并同步修正 README/docs/agent 中的 Next.js、Python、数据库和测试框架信息？
9. [ASK USER] 当前新增的 PR CI 与旧文档“明确不做 PR CI”的策略冲突；最终希望保留 PR CI，还是仅保留 main push/本地门禁？
10. [ASK USER] 产品是否继续限定为本地单用户/可信网络？若计划公网或多用户，鉴权与高风险配置端点加固必须先于功能扩展。

## 7) Intent vs reality 摘要

- 设计意图“业务拥有 Graph，Runtime 拥有控制面”已实现，且 boundary test 固化。
- “统一事件”已达到一套 envelope/parser，但未达到 durable/replayable event stream。
- “统一中断恢复”已达到 Interaction resolution 幂等与 checkpoint 唤醒，但未达到进程级恢复。
- “统一状态”已达到每类状态单一事实源，不是单表；failed/cancelled 策略仍冲突。
- “所有业务接入”中 Experience/JD 完整度高，Resume Generation 仍是部分接入。
- 当前工作树已新增 CI，但尚未提交；旧文档与 hook 状态不能证明 origin/main 有自动测试门禁。
- 品牌、版本、镜像名和启动文档目前只在工作树中趋于一致；origin/main 的 fresh clone 与镜像发布坐标仍未形成可复现闭环。

## 8) Evidence

- docs/codebase/.codebase-scan.txt
- docs/superpowers/specs/2026-08-17-agent-runtime-unification-design.zh-CN.md
- apps/backend/app/ai_chat/services/ai_chat_service.py
- apps/backend/app/ai_chat/services/run_lifecycle.py
- apps/backend/app/ai_chat/graph/driver.py
- apps/backend/app/ai_chat/protocol.py
- apps/backend/app/ai_chat/models/models.py
- apps/backend/app/jd_import/graph/builder.py
- apps/backend/app/resume_generation/service.py
- apps/backend/app/resume_generation/graph.py
- apps/backend/app/resume_generation/retriever.py
- apps/backend/app/main.py
- apps/frontend/lib/api/runtime-events.ts
- docker/mcp-egress-proxy.mjs
- .github/workflows/ci.yml
- .github/workflows/docker-publish.yml
- git log / git status / git config core.hooksPath（2026-08-23）
