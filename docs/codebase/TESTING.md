# 测试模式

## 1) 测试栈与命令

- 后端：pytest >=8、pytest-asyncio >=0.24；本次仓库 venv 实际为 pytest 9.1.1。
- 前端：Vitest ^4.1.8、Testing Library、jsdom。
- API 集成测试：HTTPX AsyncClient；Graph 恢复测试使用真实 LangGraph + SQLite checkpoint。

~~~bash
# 后端默认全量（排除 eval）
cd apps/backend
uv run pytest

# 后端分层
uv run pytest tests/unit
uv run pytest tests/integration

# 显式 LLM eval（需要 provider key，结果可能非确定）
uv run pytest -m eval

# 前端
cd apps/frontend
npm test
npx tsc --noEmit --incremental false
npm run lint

# opt-in agentic E2E
RM_E2E_MONITOR=1 uv run python -m e2e_monitor sweep
~~~

coverage 命令和阈值未在 manifest/CI 中定义，[TODO]。

## 2) 测试布局

- 后端：apps/backend/tests/unit、integration、evals；pytest 自动发现 test_*.py、Test*、test_*。
- Agent memory 还包含 app/ai_chat/memory/tests；由于 pytest testpaths 仅为 tests，默认全量命令不会自动发现这组内嵌测试。
- 前端：apps/frontend/tests/*.test.ts(x)，全局 setup 为 apps/frontend/vitest.setup.ts。
- E2E monitor：apps/backend/e2e_monitor；隔离 DATA_DIR，产物写入 artifacts/e2e-monitor。
- 本地门禁：.githooks/pre-push；需要手工 git config core.hooksPath .githooks 才生效。

## 3) Scope matrix

| 范围 | 覆盖 | 典型目标 | 说明 |
|------|------|----------|------|
| 单元 | 是 | protocol、state machine、context、services、React/API clients | 主体覆盖 |
| 集成 | 是 | FastAPI + SQLite repository/API、Resume/JD 端点 | 多为隔离临时库 |
| Graph 协议 | 是 | 真实 interrupt/resume/recover、GraphOutcome | Experience 最完整 |
| 并发/幂等 | 是 | Tool Call CAS、重复 resolution、取消竞态 | Experience/Tool suite 较强 |
| 前端组件 | 是 | hooks、workspaces、API/SSE parser | Runtime parser 缺独立测试文件 |
| E2E | 可选 | 真应用 + LLM + PDF + 证据报告 | 非 CI、非阻断 |
| 性能/负载 | 否 | [TODO] | 扫描未发现性能测试配置 |
| 硬崩溃/多进程恢复 | 不完整 | stale running、DB/checkpoint 对账 | 当前最重要缺口 |

## 4) Mock 与隔离策略

- 后端服务/LLM 通常 monkeypatch 模块函数或注入 fake model/driver/repository。
- Runtime 核心既有纯协议测试，也有真实 SQLite checkpoint 的 Graph 测试。
- 数据库测试使用临时目录/临时 SQLite，并重置 dependency container。
- 前端用 Testing Library、vi.mock 与 mocked fetch；SSE 测试构造 fragmented ReadableStream。
- eval 测试默认 deselect，避免常规测试触网或调用付费模型。
- 常见风险：fake runner 可证明服务幂等，却不能证明真实 Graph + checkpoint + DB 的跨进程一致性。

## 5) 本次验证结果（2026-08-17）

| 验证 | 结果 | 备注 |
|------|------|------|
| 后端默认全量 | 751 passed、1 skipped、11 deselected，106.35s | Python 3.12.13 venv；项目目标为 3.13 |
| 后端统一改造定向集 | 104 passed | protocol/context/driver/state + Experience/JD/Resume |
| 前端全量 | 36 files、271 tests passed | Vitest 有 CJS/ESM config warning |
| 前端统一改造定向集 | 5 files、21 tests passed | Experience/JD/Resume API 与 UI |
| TypeScript | 通过 | npx tsc --noEmit --incremental false |
| ESLint/Prettier | 失败 1 项 | lib/api/config.ts:14 的既有 CR 行尾问题，不在本轮 diff |
| git diff --check | 无 whitespace error | 有 LF -> CRLF 工作树警告 |

2026-08-20 针对 Runtime 七项抽象补充验证：

| 验证 | 结果 | 备注 |
|------|------|------|
| 后端 Graph/Run/Interaction/Event/Context/Tool/Adapter 定向集 | 97 passed，16.23s | Python 3.12.13；覆盖 7 个单元测试文件 |
| 前端 Runtime SSE 与 Experience/JD/Resume API 定向集 | 3 files、14 tests passed，20.25s | Vitest 仍报告 CJS/ESM config warning |

不能由绿测推出“完整崩溃恢复已完成”：stale running、JD 聚合结果补发、恢复阶段 output.delta 持久化和事件 replay 没有对应测试。

## 6) Runtime 关键回归证据

- 协议与 envelope：tests/unit/test_ai_chat_protocol.py。
- Runtime 不反向依赖领域：tests/unit/test_agent_runtime_boundaries.py。
- Driver 真 interrupt/resume：tests/unit/test_graph_runner_protocol.py。
- Context 顺序、边界、预算：tests/unit/test_context_assembler.py。
- Run ID/状态机：tests/unit/test_run_state.py。
- Experience 真实 suspend/approve/reject/replay/cancel：tests/unit/test_experience_ai_chat.py。
- JD question_batch：tests/unit/test_jd_import_graph.py、test_jd_import_questions.py。
- Resume 生命周期迁移/API 幂等：tests/unit/test_resume_generation_run_migration.py、tests/integration/test_resume_generation_api.py。
- 前端 fragmented SSE：apps/frontend/tests/experience-ai-chat.test.ts。

## 7) CI 与质量信号

- GitHub Actions 仅在 tag/manual 时构建并推送镜像，没有 PR test/lint/typecheck。
- pre-push 运行 backend pytest、locale parity、可用时运行 Vitest；Node 不可用时会跳过前端。
- pre-push 明确不运行 tsc/next build，并可由 --no-verify 绕过；当前 clone 的 hooksPath 是否启用应在交付时再次确认。
- 无 coverage threshold、mutation testing、性能门禁。
- Vitest 警告：vitest.config.ts 作为 CommonJS 加载但使用 ESM；未来 Vite config loader 变化可能升级为阻断。

## 8) Evidence

- apps/backend/pyproject.toml
- apps/frontend/package.json
- apps/frontend/vitest.config.ts
- .githooks/pre-push
- .github/workflows/docker-publish.yml
- apps/backend/e2e_monitor/README.md
- apps/backend/tests/unit/test_experience_ai_chat.py
- apps/frontend/tests/experience-ai-chat.test.ts
