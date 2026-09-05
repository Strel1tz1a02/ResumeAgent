# 当前文档入口

这是二次开发版本的文档入口。`docs/agent/`、`docs/codebase/` 和 `docs/superpowers/` 中的内容可能描述上游或历史实现，除非本页或代码明确引用，否则不作为当前需求依据。

## 先读

- [产品范围清单](roadmap/scope-manifest.zh-CN.md)：哪些功能属于当前产品，哪些已暂停或计划移除。
- [项目整理方案](roadmap/repository-plan.zh-CN.md)：目录、模块职责、删除和迁移规则。
- [产品与技术路线](roadmap/development-plan.zh-CN.md)：业务闭环、Agent、RAG、训练、部署和验收。
- [整理进度](roadmap/cleanup-status.md)：已移除内容、兼容保留项和下一批删除条件。

## 当前代码事实

| 区域 | 当前职责 | 修改原则 |
|---|---|---|
| `apps/frontend/app` | 页面和打印入口 | 页面只装配 feature；不要恢复已移除的 tracker/wizard 入口 |
| `apps/frontend/components` | 页面 feature 与通用 UI | 业务组件归 feature；`components/ui` 不访问 API |
| `apps/frontend/lib/api` | 后端客户端与类型 | 请求统一经过 client；退休功能不得重新导出 |
| `apps/backend/app/routers` | HTTP 适配层 | 只做鉴权、校验、调用用例和响应映射 |
| `apps/backend/app/services` | 业务用例与模型编排 | 不直接承担跨模块数据库写入 |
| `apps/backend/app/workflow_runtime` | 横向 Workflow、Run、Tool、Interaction 协议 | 不导入业务模块、ORM 或 HTTP |
| `apps/backend/app/resume_generation` | 证据检索、规划、生成、索引 Worker | 事实来源必须可追溯；生成不能覆盖事实 |
| `apps/backend/app/jd_import` | JD 解析、来源、澄清和持久化 | 来源引用与用户确认优先于猜测 |
| `apps/backend/app/experience` | 经历、Evidence 和修订 | 事实库是唯一事实归属 |
| `apps/backend/app/background_jobs` | Outbox 与任务派发 | 业务消费者归属业务模块，投递可重复 |
| `apps/backend/tests` | 分层回归与质量评估 | mock、真实集成和 eval 必须标明边界 |

## 修改前检查

1. 先查 [scope-manifest](roadmap/scope-manifest.zh-CN.md)，确认需求属于主线。
2. 用 `rg` 找现有调用和测试，确认是新增、替换还是删除。
3. 先写输入输出、状态和数据归属，再改实现。
4. 跨模块改动同时更新 API/事件契约和故障恢复测试。
5. 运行与改动对应的测试；外部依赖缺失时记录为未验证。

## 已完成的裁剪

Application Tracker 和旧 Resume Wizard 已从运行入口、页面、专属 API/组件及专属测试移除。Resume Builder 也不再展示 cover-letter、outreach、interview-prep Tab。数据库中可能存在历史表、兼容方法和旧内容字段，需在数据迁移方案确定后再删除。

## 运行入口

- 后端：`apps/backend` 下使用项目锁定的 Python 环境运行 `python -m pytest`。
- 前端：`apps/frontend` 下使用 `npm test`、`npm run lint` 和 `npm run build`。
- 本地依赖：根目录 `docker-compose.yml`；真实模型、浏览器 MCP、Redis 和 Qdrant 的可用性必须单独验证。
