# 当前产品范围清单

本文件是二次开发的裁剪依据。没有列入“当前主线”的功能，不应继续新增代码、页面或测试。

## 当前主线（必须可用）

| 能力 | 用户结果 | 现有入口 |
|---|---|---|
| 资料与经历导入 | 将简历、项目材料变成可确认的事实与 Evidence | `experiences`、简历上传 |
| JD 导入与澄清 | 得到结构化岗位要求；歧义由用户确认 | `jd-imports`、`app/jd_import` |
| 证据检索 | 为要求找到带来源的经历证据 | `resume_generation/retriever.py` |
| 简历生成与审核 | 生成岗位版本，逐条查看来源和 diff 后确认 | `resume-generation`、生成 API |
| 版本与导出 | 保留原始事实，导出确认后的 PDF | resume print 路由 |
| 可靠任务 | 长任务可暂停、恢复、重试且不重复写入 | `workflow_runtime`、Outbox、ARQ |
| 质量评估 | 对召回、真实性、覆盖率和禁止事实做可复现评估 | `tests/evals` |

## 已完成退出

以下能力已从运行入口、专属页面/API/组件、数据库模型和专属测试移除：Application Tracker、Resume Wizard。

## 暂停开发（代码暂留，禁止扩展）

这些能力可能有复用代码，但不属于第一版产品闭环：复杂模板 builder、自定义章节体系、面试准备、多厂商设置页、全量国际化、自动投递。Tracker 已完成退出，不再列入暂停开发范围。

## 计划移除（需逐条完成整链路清理）

上游 README 的赞助/社区/营销内容、只服务旧主简历模型的逻辑、旧 ATS 总分链路、无调用的 enrichment/improver/refiner 流程、已完成迁移的启动时兼容逻辑，以及 cover-letter/outreach/interview-prep 的后端端点、配置字段和历史响应字段。

## 删除前检查

每个条目都必须检查：前端导航、API 路由、service/Prompt、数据库读写、Worker/事件、配置、专属测试、文档和素材。共享能力先迁移到主线，再删除旧流程。

删除完成后运行：

```powershell
git grep -n "退休功能关键词"
python -m pytest apps/backend/tests/unit apps/backend/tests/integration -q
```

测试环境缺少 PostgreSQL、Redis、Qdrant、模型或浏览器时，报告为“未验证”，不能记为通过。
