# 代码库关注点

## 1) Top risks（按优先级）

| 严重度 | 关注点 | 证据 | 影响 | 建议动作 |
|--------|--------|------|------|----------|
| 高 | 硬崩溃后 stale running 无法被新执行者认领 | ai_chat_service.py:133-159,378-414；test_experience_ai_chat.py:2102 | Run 被永久视为活跃；新消息/重复 resolution 均被挡 | 增加 run attempt、owner lease/heartbeat、启动与按需 reconciliation、显式 recover API |
| 高 | Run DB 与 checkpoint DB 非原子且缺系统对账 | db_engine.py；graph/runner.py；run_lifecycle.py | checkpoint 已前进但 Run 未 settle，或反向漂移 | 定义恢复矩阵，以 attempt/version 对账并测试每个 crash window |
| 高 | 事件不是 durable log | streaming/sse.py:36；frontend/runtime-events.ts | sequence 每条连接重置；断线/刷新无法去重、续传或补发 terminal event | durable event/outbox、全 Run 序号、cursor/Last-Event-ID、前端 replay reducer |
| 高 | JD 完成后的聚合结果无法可靠补发 | jd_import/graph/builder.py:280；ai_chat_service.py:382 | JD 已落库但 settle/event 前崩溃时，恢复只可能补发 Tool Result，不会重建 persisted_ids/errors | 将领域 Result 持久化为独立 artifact/result record，再由 Runtime 投递 |
| 高 | Resume Generation 无 checkpoint/recover | resume_generation/service.py:105；resume_generation/graph.py | preview 执行中进程死亡会永久 running，只能人工修库 | 要么声明同步短任务且提供安全重跑，要么接入 attempt/checkpoint/恢复 |
| 中高 | Interaction 仍依附 Tool Call，且一次只允许一个 active interrupt | models/models.py:125；adapters/base.py:54；graph/driver.py:103 | 非工具交互需要 pseudo-tool；无法支持并行审批/子 Agent join | 抽 InteractionStore，定义 InteractionSet 与 join 语义 |
| 中高 | 恢复阶段 output.delta 未聚合/持久化 | ai_chat_service.py:430；run_lifecycle.py:101 | 新业务若恢复后继续生成文本，会出现 UI 有流但消息/Memory 无事实 | start/resume 共用同一 output collector 与 settle 路径 |
| 中 | 前端 pending Interaction 只在组件内存 | use-experience-ai-chat.tsx；jd-import-workspace.tsx | 刷新/离页后无法重建审批或问题批次 UI | 增加 GET run/pending-interactions 与 hydration |
| 中 | 大规模统一改造仍未提交 | git status --short；git diff --stat | 89 项工作树变动，核心新文件仍 untracked，容易漏交付/难审查 | 分层检查清单后形成一个可审查 commit 或按迁移阶段拆分 |

一句话判断：当前已经统一了“正常路径控制面”，尚未统一“失败后的恢复控制面”。

## 2) 技术债

| 债务 | 原因 | 位置 | 不处理的风险 | 建议 |
|------|------|------|--------------|------|
| 通用 Runtime 仍命名为 app.ai_chat | 从既有聊天框架增量抽取 | app/ai_chat/protocol.py、graph/driver.py、run_state.py、context/ | Resume/未来业务误以为必须依赖 Chat；边界继续模糊 | 稳定接口后迁到 app/agent_runtime，ai_chat 保留 conversation coordinator |
| BaseAdapter 暴露 LangGraph StateGraph | 当前只支持 LangGraph | ai_chat/adapters/base.py；graph/runner.py | Driver 看似抽象但构建/编译仍锁定 LangGraph | 若确有多引擎需求，再抽 CompiledGraph/GraphFactory 端口；否则明确这是 LangGraph harness |
| 没有 RuntimeCommand discriminated union | 实现依靠 Python 类型分派 | ai_chat/protocol.py | wire schema/跨语言生成困难 | 定义带 type/version 的 command envelope |
| 前后端手写弱类型事件 | 先统一 envelope，未引入 schema 生成 | streaming/events.py；frontend/runtime-events.ts | payload 漂移只在运行时暴露 | 以 JSON Schema/OpenAPI 生成 core event union，payload.kind 由业务扩展 |
| RunLifecycleService 仍对话特化 | 原子提交包含 assistant message/tool delivery | ai_chat/services/run_lifecycle.py | 误把它当通用生命周期会迫使 Resume 适配聊天概念 | 保留通用 RunCoordinator/StateMachine，conversation lifecycle 作为插件 |
| 启动时手写迁移持续增长 | 项目历史采用幂等 SQL | app/db_engine.py | 顺序、回滚、并发升级越来越难 | 评估 Alembic 或至少版本化 migration registry |
| 旧文档漂移 | 版本/架构快速变化 | README.zh-CN.md；docs/agent/ | 新成员按 Next 15、Python 3.11、TinyDB、Jest 理解系统 | 确定 docs/codebase 是否为事实基线，并修正/链接旧文档 |

## 3) 安全关注

| 风险 | OWASP | 证据 | 当前缓解 | 缺口 |
|------|-------|------|----------|------|
| 应用 API 无用户鉴权/资源所有权 | A01 Broken Access Control | routers/config.py:578,630；各 router 仅注入 DB session | 产品定位偏本地单用户；CORS 可配置 | 一旦公网/多用户部署，配置、简历、API key 等均需 authn/authz |
| 本地 Redis/Qdrant 默认无鉴权 | A05 Security Misconfiguration | docker-compose.yml | 默认 Compose 网络隔离 | 不应直接暴露端口到不可信网络；生产需密钥/TLS/ACL |
| URL 抓取存在 DNS 校验到浏览器访问的时间窗 | A10 SSRF | jd_import/sources/url_policy.py；playwright_mcp.py | 禁止凭据、非 80/443、内网/metadata、非 global IP | 若解析后仍按 hostname 导航，DNS rebinding 的绑定/二次校验策略需确认 [TODO] |
| API key 加密 secret 丢失 | N/A | app/crypto.py | Fernet at rest、secret gitignored、原子写 | 缺少备份/轮换 runbook；丢失后 ciphertext 不可恢复 |
| LLM 输入包含用户/履历内容 | N/A | app/llm.py；context/assembler.py | trust boundary 标记、LOG_LLM 开关 | provider 数据保留策略、日志脱敏与用户告知未集中定义 [TODO] |

## 4) 性能与扩展性

| 关注点 | 证据 | 当前症状 | 扩展风险 | 建议 |
|--------|------|----------|----------|------|
| SQLite 多写者 + checkpoint 独立库 | db_engine.py；graph/runner.py | 通过 WAL/busy_timeout 缓解 | 多进程/高并发下锁竞争与一致性恢复复杂 | 明确单实例容量；压测后决定 Postgres/checkpoint backend |
| Resume snapshot 加载可能 N+1 | resume_generation/service.py:295 | 每个 Experience 查询 evidence | Experience 数量增加后预览延迟线性放大 | 批量 preload evidence 或 repository join |
| 超大模块职责集中 | routers/resumes.py 2056 LOC；services/improver.py 1514；app/llm.py 1107；JD workspace 947 | 修改影响面大 | 难并行开发、回归成本高 | 按 use case/provider/view-state 拆分，先补 characterization tests |
| 无性能门禁 | docs/codebase/.codebase-scan.txt | 无基准 | LLM、PDF、Qdrant、Graph 改动可能静默退化 | 建立关键路径 latency/token/DB query benchmark |
| SSE 无 backpressure/replay | streaming/sse.py；runtime-events.ts | 当前短流可接受 | 长流/重连会重复或丢事件 | bounded queue、heartbeat、cursor 与 consumer lag 指标 |

## 5) Fragile / high-churn areas

| 区域 | 为什么脆弱 | 90 天 churn 信号 | 安全改法 |
|------|------------|-----------------|----------|
| tests/unit/test_experience_ai_chat.py | 同时覆盖 Graph、DB、恢复、并发 | 21 commits | 保留真实组合测试，新增 crash-window 参数化测试 |
| frontend Experience library/page | 大组件 + cache + editor + AI chat | test 21、page 16 | 先抽 view model/runtime reducer，再拆 UI |
| app/db_engine.py | 启动、engine、迁移集中 | 14 | 每个迁移有独立回归测试，避免改旧迁移 |
| Tool Call service/repository | Interaction、幂等、执行、delivery 交叉 | tests 13、repo 12 | CAS invariant 测试先行，逐步抽 Interaction |
| Graph runner/runtime | 本轮统一的中心边界 | runner 11、runtime 10 | 维持 import boundary + real checkpoint contract tests |
| experience/graph/builder.py | 节点、approval、结果事件交叉 | 10 | 业务 Graph 保持本地，禁止 Runtime 加业务分支 |
| app/main.py | router、adapter、worker composition root | 10 | 只做注册，不放领域逻辑 |

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

## 7) Intent vs reality 摘要

- 设计意图“业务拥有 Graph，Runtime 拥有控制面”已实现，且 boundary test 固化。
- “统一事件”已达到一套 envelope/parser，但未达到 durable/replayable event stream。
- “统一中断恢复”已达到 Interaction resolution 幂等与 checkpoint 唤醒，但未达到进程级恢复。
- “统一状态”已达到每类状态单一事实源，不是单表；failed/cancelled 策略仍冲突。
- “所有业务接入”中 Experience/JD 完整度高，Resume Generation 仍是部分接入。

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
- apps/frontend/lib/api/runtime-events.ts
- .github/workflows/docker-publish.yml
- git status --short；git diff --stat（2026-08-17）
