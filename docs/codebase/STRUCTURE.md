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
- Agent composition root：apps/backend/app/main.py 注册 ExperienceAdapter 与 JDImportAdapter；Resume Generation 仍由独立 router/service 入口管理。

## 3) 模块边界

| 边界 | 应拥有 | 不应拥有 |
|------|--------|----------|
| app/ai_chat/protocol.py、graph/driver.py、run_state.py、streaming/、context/ | 通用命令、事件、Graph 执行端口、Run 状态规则、上下文组装 | Experience/JD/Resume 的节点、字段与业务校验 |
| app/ai_chat/services/ai_chat_service.py | 对话型 Runtime 编排、持久化后发事件、恢复与幂等协调 | 直接导入业务 graph 或识别业务 checkpoint key |
| app/ai_chat/adapters/ | 业务 Graph 插件契约与注册 | 具体业务拓扑 |
| app/experience/ | 经历修订、证据、审批 Graph 与领域工具 | Runtime 生命周期写入规则 |
| app/jd_import/ | JD 来源、抽取、澄清问题、落库 Graph | Runtime 事件协议 |
| app/resume_generation/ | 简历计划、检索、生成、产物状态 | 将 previewed/confirmed 混入通用 Run 状态 |
| app/ai_chat/repositories/、各领域 repository | 持久化与 CAS | HTTP/SSE 序列化 |
| apps/frontend/lib/api/ | transport、统一 Runtime SSE envelope 解析 | 页面展示状态 |
| apps/frontend/components/ | 领域事件投影与 UI 交互 | 重复实现 SSE envelope parser |

边界测试 apps/backend/tests/unit/test_agent_runtime_boundaries.py 禁止 Runtime 反向导入 Experience/JD/Resume，也禁止业务事件名和 checkpoint 私有 key 回流到 Runtime。

## 4) Agent Runtime 深层地图

~~~text
HTTP router
  -> AiChatService
     -> AdapterRegistry -> business Adapter -> business Graph
     -> GraphRunner -> GraphDriver -> RuntimeEvent | GraphOutcome
     -> RunLifecycleService / RunStateMachine
     -> RunRepository + ToolCallStore + LangGraph checkpoint
  -> runtime_sse_response
  -> frontend parseRuntimeSse
~~~

- 首次输入：app/ai_chat/types/adapter_input.py。
- 外部解决等待：app/ai_chat/protocol.py 的 ResolveInteractionCommand。
- checkpoint 唤醒：同文件的 GraphResumeCommand，只含 run_id 与 interaction_id。
- 图执行：app/ai_chat/graph/driver.py 与 runner.py。
- 对话生命周期原子收口：app/ai_chat/services/run_lifecycle.py。
- 统一模型上下文：app/ai_chat/context/assembler.py。
- 统一事件与 SSE：app/ai_chat/streaming/events.py、sse.py。

## 5) 命名与组织规则

- Python 文件、函数、变量使用 snake_case；类/协议使用 PascalCase；测试使用 test_*.py。
- TypeScript 组件文件多用 kebab-case，组件/类型用 PascalCase；测试使用 *.test.ts 或 *.test.tsx。
- 后端按领域 + 层混合组织：experience/adapters、graph、repositories、routers、services。
- 前端按 App Router 页面 + components/lib API 分层。
- TypeScript 使用 @/* -> 项目根的路径别名，见 apps/frontend/tsconfig.json。
- Python 使用 app.* 绝对导入；通用 Runtime 目前仍物理位于 app.ai_chat 下，这是命名/包边界债务。

## 6) Evidence

- docs/codebase/.codebase-scan.txt
- apps/backend/app/main.py
- apps/backend/app/ai_chat/adapters/base.py
- apps/backend/app/ai_chat/graph/runner.py
- apps/backend/app/ai_chat/graph/driver.py
- apps/backend/tests/unit/test_agent_runtime_boundaries.py
- apps/frontend/tsconfig.json
- docker-compose.yml

