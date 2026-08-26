# 代码库结构

## 1) 顶层地图

| 路径 | 作用 | 证据 |
|------|------|------|
| apps/backend/ | FastAPI 后端、领域模块、worker、迁移、测试 | apps/backend/pyproject.toml；apps/backend/app/main.py |
| apps/frontend/ | Next.js App Router 前端、组件、API 客户端、测试 | apps/frontend/package.json；apps/frontend/app/ |
| config/ | 非密钥应用配置 | apps/backend/app/config.py |
| docker/ | 容器启动辅助文件 | Dockerfile；docker-compose.yml |
| scripts/ | 仓库级检查与开发脚本 | scripts/check_locale_parity.py；.githooks/pre-push |
| docs/agent/ | 面向开发者的架构、工作流、LLM 文档 | docs/agent/README.md |
| docs/superpowers/specs/ | 功能设计与决策记录 | docs/superpowers/specs/2026-08-17-agent-runtime-unification-design.zh-CN.md |
| docs/superpowers/plans/ | 实施计划 | docs/superpowers/plans/2026-08-17-agent-runtime-unification.md |
| docs/codebase/ | 本次代码事实地图 | docs/codebase/.codebase-scan.txt |
| assets/ | README/演示媒体 | README.zh-CN.md |
| .github/、.githooks/ | 发布、安全、贡献规范与本地质量门禁 | .github/workflows/docker-publish.yml；.githooks/pre-push |

.worktrees/、.git-upstream-backup/、缓存、日志和 node_modules 是工作环境/生成物，不应当作产品源码边界；扫描器把它们计入了规模统计。

## 2) 入口点

- 后端主入口：apps/backend/app/main.py；pyproject 脚本 app = app.main:main。
- 前端主入口：apps/frontend/app/；Next.js 脚本位于 apps/frontend/package.json。
- 后台进程：apps/backend/app/ai_chat/memory/worker.py 与 apps/backend/app/resume_generation/index_worker.py；Compose 以 ARQ worker 启动。
- 数据库初始化/迁移入口：apps/backend/app/db_engine.py；启动时执行幂等 SQL 迁移，没有 Alembic 目录。
- Agent composition root：apps/backend/app/main.py 注册 ExperienceWorkflow 与 JDImportWorkflow；Resume Generation 仍由独立 router/service 入口管理。

## 3) 模块边界

| 边界 | 应拥有 | 不应拥有 |
|------|--------|----------|
| app/workflow_runtime/ | 可按需组合的 Graph、Run、InteractionCoordinator、中断恢复、事件、模型上下文、Tool 生命周期/持久化端口与内存 Store | Conversation 持久化、HTTP/SSE、具体业务语义 |
| app/ai_chat/services/conversation_service.py | Conversation 入口：创建、用户/模型轮次、关闭与删除 | Interaction 恢复或被一次性 Workflow 依赖 |
| app/ai_chat/services/conversation_execution.py | ConversationService 私有的新轮次协调器：Run、消息与幂等编排 | Interaction resolve/recover/resume 或对 Router 暴露第二套服务入口 |
| app/ai_chat/workflow.py + app/workflow_runtime/graph/catalog.py | 对话 Workflow 契约与唯一 Workflow 目录 | 具体业务拓扑 |
| app/experience/ | 经历修订、证据、审批 Graph 与领域工具 | Runtime 生命周期写入规则 |
| app/jd_import/ | JD 来源、抽取、澄清问题、落库 Graph | Runtime 事件协议 |
| app/resume_generation/ | 简历计划、检索、生成、产物状态 | 将 previewed/confirmed 混入通用 Run 状态 |
| app/ai_chat/persistence/ | Conversation 对 ToolCallStore、InteractionStore 的 SQLAlchemy 实现 | Tool/Interaction 生命周期算法或业务 Graph |
| app/ai_chat/repositories/、各领域 repository | Conversation/领域持久化与 CAS | HTTP/SSE 序列化 |
| apps/frontend/lib/api/ | transport、统一 Runtime SSE envelope 解析 | 页面展示状态 |
| apps/frontend/components/ | 领域事件投影与 UI 交互 | 重复实现 SSE envelope parser |

边界测试 apps/backend/tests/unit/test_agent_runtime_boundaries.py 禁止 workflow_runtime 反向导入 Conversation 或具体业务，也禁止业务事件名和 checkpoint 私有 key 回流到对话编排。

## 4) Agent Runtime 深层地图

~~~text
HTTP router
  ├-> ConversationService
     -> WorkflowCatalog.get -> ConversationWorkflow -> Workflow
     -> CheckpointedWorkflowExecutor.stream(thread_id=run_id) -> GraphExecutor
     -> Workflow.init_state -> Business Graph
     -> ToolLifecycleService -> ToolCallStore port -> SQLAlchemy implementation
     -> ConversationTurnCoordinator / ConversationRunWriter / RunStateMachine
     -> RunRepository + ToolCallStore + LangGraph checkpoint
  └-> InteractionCoordinator
     -> Workflow.resolve_interaction -> ToolLifecycle
     -> InteractionStore port -> ConversationInteractionStore
     -> GraphExecutor recover/resume -> RunStateMachine
  -> runtime_sse_response
  -> frontend parseRuntimeSse

one-shot workflow
  -> 按需组合 GraphExecutor / RunStateMachine / ContextAssembler / RuntimeEvent
  -> ToolLifecycleService + InMemoryToolCallStore（或自定义 Store）
  -> 不依赖 ConversationService
~~~

- 每轮输入：app/ai_chat/types/conversation_input.py；新输入创建新 Run 与独立 checkpoint。
- 外部解决等待：app/workflow_runtime/protocol.py 的 ResolveInteractionCommand。
- checkpoint 唤醒：同文件的 GraphResumeCommand，只含 run_id 与 interaction_id。
- 图执行：app/workflow_runtime/graph/driver.py 与 runner.py。
- Workflow 状态：业务实现 `init_state()`，每个新 Run 初始化一次；Interaction resume 只恢复同一 Run checkpoint。
- Conversation 多轮：ConversationService、Run/Message 持久化和 MemoryService 共同负责，不存在 ConversationGraph。
- Tool 风险：由具体 `ToolOperation.risk` 声明，审批策略只负责路由规则与展示载荷。
- Tool 保存：`workflow_runtime/tools/persistence.py` 定义端口；Runtime 提供内存实现，Conversation 在 `ai_chat/persistence/` 注入 SQLAlchemy 实现。
- Conversation 生命周期：app/ai_chat/services/conversation_service.py。
- Interaction 固化与恢复：app/workflow_runtime/interactions.py；Conversation 只实现 app/ai_chat/persistence/interaction_store.py。
- Conversation 新 Run 原子收口：app/ai_chat/services/conversation_run_writer.py。
- 统一模型上下文：app/workflow_runtime/context.py。
- 通用事件：app/workflow_runtime/events.py；Conversation SSE：app/ai_chat/streaming/sse.py。

## 5) 命名与组织规则

- Python 文件、函数、变量使用 snake_case；类/协议使用 PascalCase；测试使用 test_*.py。
- TypeScript 组件文件多用 kebab-case，组件/类型用 PascalCase；测试使用 *.test.ts 或 *.test.tsx。
- 后端按领域 + 层混合组织：experience/workflow.py、graph、repositories、routers、services。
- 前端按 App Router 页面 + components/lib API 分层。
- TypeScript 使用 @/* -> 项目根的路径别名，见 apps/frontend/tsconfig.json。
- Python 使用 app.* 绝对导入；业务直接导入所需的 app.workflow_runtime 能力，不注入 Runtime 聚合对象。

## 6) Evidence

- docs/codebase/.codebase-scan.txt
- apps/backend/app/main.py
- apps/backend/app/ai_chat/workflow.py
- apps/backend/app/workflow_runtime/graph/catalog.py
- apps/backend/app/workflow_runtime/graph/runner.py
- apps/backend/app/workflow_runtime/graph/driver.py
- apps/backend/app/ai_chat/services/conversation_execution.py
- apps/backend/tests/unit/test_agent_runtime_boundaries.py
- apps/frontend/tsconfig.json
- docker-compose.yml

