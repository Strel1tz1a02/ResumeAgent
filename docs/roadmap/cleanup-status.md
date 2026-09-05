# 整理进度

最后更新：2026-09-05

## 已完成

- [x] 建立当前产品范围和架构文档入口
- [x] 移除 Application Tracker 页面、路由、Schema、专属 API、组件和测试
- [x] 移除 Tracker 自动创建副作用
- [x] 移除 Resume Wizard 页面、路由、Schema、service、API 和测试
- [x] Resume Builder 隐藏 Cover Letter、Outreach、Interview Prep Tab
- [x] Cover Letter / Outreach 生成和 Cover Letter PDF 端点返回 410
- [x] 清理主线前端格式问题并恢复 lint
- [x] 固化退休端点契约测试
- [x] 审计并删除唯一历史 Application 记录，备份后移除 `applications` 表及 ORM 注册

## 当前保留的兼容内容

- Application CRUD 方法和 ORM 模型：已删除，数据库不会再创建该表。
- 简历记录中的 `cover_letter`、`outreach_message`、`interview_prep` 字段：只读/兼容用途，不得新增依赖。
- Interview Prep 后端按需端点：历史兼容用途，前端无入口；后续应迁移或删除。
- 设置页相关旧开关和 Prompt 字段：仍被现有配置契约测试覆盖，需先确定配置迁移。
- 上游 API 历史说明：由 `docs/README.md` 标记为参考，不代表当前产品契约。

## 最近一次本地审计

审计文件：`apps/backend/data/resume_matcher.db`（2026-09-05）。授权后删除唯一历史 Application 记录并生成 `resume_matcher.db.pre-cleanup.bak`，随后移除 `applications` 表；简历中的三个旧字段仍保留为空，供兼容读取。

## 下一批删除条件

1. 使用 `python apps/backend/app/scripts/audit_legacy_data.py <sqlite-path>` 复核旧字段数量。
2. 迁移旧字段或明确删除后，再移除兼容读取路径。
3. 将历史字段迁移到受控 legacy 表或明确丢弃，并更新读取路径。
4. 删除设置页旧配置、旧响应字段、Interview Prep 端点和不再被引用的服务/组件。
5. 全量运行后端、前端、契约和评测；确认 OpenAPI 与导航没有旧入口。
