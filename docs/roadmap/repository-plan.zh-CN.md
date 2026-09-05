# 项目整理与迁移方案

## 1. 当前判断

这是二次开发项目，原项目的功能和设计不自动构成新产品需求。先裁剪产品范围，再整理存活代码；不为退出范围的功能维持兼容。首次检查开始时 `git status --short` 为空。

用户已明确：旧设计中的非必要部分可以去除。以下为修订后的处置方案，尚未执行功能或数据删除。后文迁移表只适用于裁剪后仍有业务价值的部分，不要求全量搬迁旧代码。

| 当前证据 | 判断 | 后续处理 |
|---|---|---|
| `apps/backend/app/workflow_runtime/` 及 `tests/unit/test_agent_runtime_boundaries.py` | 已有横向 Workflow、工具生命周期、审批和依赖边界测试 | 保留；不另造一套 Agent Runtime |
| `app/experience/`、`app/jd_import/`、`app/resume_generation/` | 已有业务模块化基础 | 沿现有业务边界整理 |
| `app/resume_generation/retriever.py` | 已有 Qdrant dense/sparse 混合检索基础 | 抽出知识检索适配层，避免重复建设 |
| `app/background_jobs/`、memory/index Worker | 已有 Outbox、Redis、ARQ、稳定 job ID 和过期重投机制 | 补齐故障验证与多实例并发，不从零重写队列 |
| `app/ai_chat/checkpoint/factory.py` | 当前使用 SQLite checkpointer | 先保留行为，后迁移持久化适配器 |
| `app/database.py`，799 行 | 保留 TinyDB 时代字典接口的 SQLite facade；含进程内锁 | 暂作兼容层；按业务逐步替换，明确分布式约束 |
| `app/routers/resumes.py`，2056 行 | 简历旧路径职责集中 | 拆用例，避免只把大文件切成多个杂物文件 |
| `tests/evals/README.md` | 已有多能力评估、来源校验、独立 Judge 配置和报告范围声明 | 继续建设；不能把合成回归结果称为招聘质量证明 |
| `.github/workflows/docker-publish.yml` | 当前看到发布工作流，未见 PR 测试门禁工作流 | 增加 CI，发布前必须引用通过的验证结果 |
| `docker-compose.yml` | 已有 Worker 和浏览器出口隔离；应用仍共享本地数据卷 | 有部署基础，不等于高可用或租户隔离 |
| `docs/agent/`、`docs/codebase/`、`docs/superpowers/`、多个启动说明 | 新旧说明并存，阅读路径不清晰 | 一个当前入口，历史文档保留但标明状态 |

当前路由入口和所检查模型中未看到完整的用户/租户授权链路。这是本轮静态检查的发现，不是全仓安全审计结论。

## 2. 整理原则

1. 保留 monorepo；先模块化单体，按运行负载拆进程，再按真实边界拆服务。
2. 以业务组织代码：经历、职位、简历、求职计划；横向 Runtime 不认识业务名。
3. 每项数据只有一个写入归属。其他模块通过公开服务或事件访问，禁止直接修改对方表。
4. 只抽取已经存在重复或独立生命周期的能力。不要给每个 CRUD 强制套四层目录。
5. 兼容只服务活跃客户端和需要保留的数据。退休功能可以连同 API、专属测试和依赖一起移除，没有使用者的旧接口不建立长期兼容层。
6. 现有文件名不为整齐而全部改名。特别是 `workflow_runtime`，已经有清晰边界就先保留。

### 2.1 功能处置清单

当前已检查前后端入口和模块。下表是产品范围建议；删除实现前需追踪共享调用，不能按目录名整包删除。

| 处置 | 功能或设计 | 具体策略 |
|---|---|---|
| 保留并做深 | 经历库、Evidence、JD 导入、证据驱动简历生成 | 构成唯一主线；保留事实与版本，不要求保留所有旧实现 |
| 保留并收敛 | Runtime、审批、Outbox、检索、核心 eval | 裁掉无调用抽象和适配器；历史投入不是保留理由 |
| 提取复用 | 简历编辑、PDF 渲染、打印路由 | 只提取新流程所需能力，先保留一个稳定模板 |
| 替换 | 唯一主简历、`is_master` 为中心的业务假设 | 以个人证据库作为事实中心；旧简历仅作为资料来源 |
| 合并后删除 | `/tailor`、`/resume-wizard` 与 `/resume-generation` 并行流程 | 收敛到证据生成；迁入必要补问交互，删除重复编排 |
| 候选删除 | 旧 enrichment、improver/refiner、关键词 ATS 总分链路 | 用证据补充、覆盖矩阵和统一改写接替；检查是否被主线共享调用 |
| 首期移除 | 求职信、outreach 邮件、旧面试准备生成及专属打印入口 | 有真实用户需求时再作为独立用例或 Skill 恢复 |
| 裁剪后置 | 独立 builder、复杂自定义章节、模板扩展体系 | 保留审核、轻量编辑与导出，暂不维护复杂排版产品 |
| 收敛后置 | tracker/applications | 初期只需轻量状态；后续归入 Campaign，避免两套进度模型 |
| 收缩支持面 | 多厂商模型适配、用户模型设置页、双语维护 | 只维护已验证的模型路径；配置归管理员；中文优先，不必立即拆除复用中的国际化基础 |
| 删除失效内容 | 上游营销/赞助展示、无关素材、过期设计和重复启动说明 | 根 README 重写为本产品入口；保留 LICENSE、适用版权/NOTICE 和上游来源说明 |
| 退出启动路径 | TinyDB 导入与已完成的历史迁移兼容 | 核实数据需求后做一次性迁移；不继续启动时自动执行 |

### 2.2 删除与替换规则

- 功能按整链路清理：导航/页面 → API → service/Prompt → 专属测试 → 依赖/配置 → 文档/素材。共享渲染器、Schema 等先提取再删除旧流程。
- 记录每项的业务理由、替代入口、调用者、数据影响和验收项。无替代需求的功能直接退休；主线仍需的能力先完成接替。
- 退休功能专属测试可随功能移除；主线和共享安全/事务测试保留或迁移，不能为通过 CI 删除有效失败测试。
- 验收检查导航死链、退休 API、残留调用和专属依赖；保留功能执行契约和 E2E 回归。
- 旧代码和失效设计可由 Git 历史追溯，不必全部搬到 archive。只保留仍能解释当前决策的历史材料。
- 用户资料、密钥、工作树和备份不属于功能删除范围。删除旧模块不意味着清空其历史数据。

## 3. 目标目录

以下是逐步到达的目标，不是要求立即创建全部空目录。

```text
Resume-Matcher/
  apps/
    frontend/
      app/                       # Next.js 路由、页面装配
      features/                  # experiences/jobs/resumes/conversations/campaigns
      components/ui/             # 无业务语义的通用组件
      lib/api/                   # 生成的 API 客户端与统一错误处理
      messages/                  # 国际化
      tests/
    backend/
      app/
        main.py                  # HTTP 应用装配
        bootstrap.py             # 依赖与 Workflow 注册
        modules/
          identity/              # 用户、workspace、成员、授权
          experience/            # 经历、证据、事实版本
          jobs/                  # 职位、要求、导入 Workflow
          resumes/               # 生成、编辑、版本、导出
          conversations/         # 会话、消息、展示投影
          campaigns/             # 多职位任务和求职进度
          knowledge/             # 索引、检索、关系图查询
        workflow_runtime/        # Graph、Run、Tool、Interaction 的通用协议
        infrastructure/
          db/                    # 引擎、事务、持久化适配器
          queue/                 # Outbox、派发、消费者适配器
          llm/                   # 模型调用、限额、模型路由
          mcp/                   # 连接、超时、适配、协议契约
          sandbox/               # 浏览器和文档处理隔离
          storage/               # 对象存储
          observability/         # trace、日志、指标
        entrypoints/             # agent/index/document Worker 启动入口
      migrations/                # 版本化数据库迁移
      tests/
        unit/
        integration/
        contract/
        e2e/
  contracts/                     # HTTP、事件、Workflow 输入输出契约
  evals/                         # 后续从已有 eval 渐进迁入
    datasets/
    scorers/
    suites/
  training/                      # 到训练阶段才建立；独立依赖环境
    data/
    sft/
    rl/
    configs/
  skills/                        # 产品自己的技能包及 manifest
  deploy/
    compose/
    monitoring/
    kubernetes/                  # 出现需求后再建立
  scripts/                       # 开发、测试、演示、数据备份入口
  docs/
    README.md                    # 当前唯一文档导航，后续建立
    product/
    architecture/
    adr/
    runbooks/
    roadmap/                     # 本轮方案
    archive/                     # 已被取代的历史方案
```

简单模块使用 `router.py / schemas.py / service.py / repository.py / models.py` 即可。复杂模块再增加 `workflows/`、`domain/`。SQLAlchemy 模型归所属业务模块，不都塞进全局 `models.py`。

依赖方向：路由调用业务服务；业务服务调用 Runtime 或基础设施端口；具体适配器在装配入口注入。Runtime 不导入业务、HTTP 或 ORM；业务模块禁止依赖另一个模块的内部仓储。

## 4. 现有路径如何迁移

| 现有路径 | 目标归属 | 迁移要求 |
|---|---|---|
| `app/experience/` | `modules/experience/` | 保留证据 ID、修订号、现有审批语义 |
| `app/jd_import/` | `modules/jobs/imports/` | 保留来源定位、批量提问、信息归属澄清 |
| `app/resume_generation/` 中生成相关代码 | `modules/resumes/generation/` | 保留事实约束、provenance 与评估口径 |
| retriever/indexing/index_worker | `modules/knowledge/` 与 Worker 入口 | 先定义公开接口，再移动；不要复制检索器 |
| `app/ai_chat/` | `modules/conversations/` | 拆开会话数据与横向持久化适配器；不再把 Runtime 装配藏在聊天模块中 |
| `app/workflow_runtime/` | 保留 | 延续已有架构边界测试，增量验证依赖方向 |
| `app/routers/` 与 `app/services/` | 对应业务模块 | 首先拆简历上传、生成、版本确认、导出四条用例 |
| `app/database.py` | 临时兼容 facade | 逐调用点替换，最后删除，禁止新模块继续依赖 |
| `app/background_jobs/` | `infrastructure/queue/` | 保留 Outbox 数据和去重键；消费者业务逻辑回到所属模块 |
| `app/scripts/migrate_*` | `migrations/` 加历史迁移工具 | 先做已应用版本基线，不能直接重跑所有历史脚本 |
| frontend 的业务 components/hooks/types | `features/<业务>/` | app 只负责装配；共享 UI 不含业务请求 |
| 现有 `tests/evals/` | 先保留，P3 再迁 `evals/` | 普通回归和真实付费 eval 继续分离 |

## 5. 迁移批次

| 批次 | 工作 | 退出标准 |
|---|---|---|
| R0 范围与基线 | 固定当前版本；建立保留/删除/替换清单；检查主线、调用关系和数据 | 清单可追到文件与共享依赖；报告明确通过/失败/未执行 |
| R1 开发入口 | 根文档链接统一；确认 Python/Node 与锁文件；一个 dev/check/demo 入口；样例数据与真实数据分开 | 新目录或新机器可按说明启动；清楚列出外部依赖与离线模式 |
| R2 裁剪与整理 | 先移除退休入口和专属实现，再整理存活业务模块 | 主线回归通过；退休功能无残留调用；接口变化及数据影响有清单 |
| R3 基础设施归位 | 装配、DB、Queue、LLM、MCP 边界；完善 import 边界测试；统一 frontend API client | Runtime 无反向依赖；未增加重复状态机或数据库写入者 |
| R4 文档与兼容收尾 | 删除失效文档、素材和兼容代码；保留必要历史决策 | 当前入口可追到真实代码；清理有调用点、数据和测试证据 |

每批单独形成可 review 的 diff，附变更原因、影响路径、验证结果和回退方法。默认不提交 Git。

不要自动删除 `.worktrees`、`.git-upstream-backup`、`.venv` 或数据目录；先核实用途。无关历史设计可以退出工作树，不必一律保留。

## 6. 依赖、迁移和开发规范

- 后端以 `pyproject.toml + uv.lock` 为依赖源；若部署仍需 requirements，则由锁文件生成并校验，禁止维护两份独立清单。训练环境和 vLLM 环境单独锁定，避免挤进业务 API 环境。
- 前端保留 `package-lock.json`，CI 使用确定性安装；不要顺手升级框架大版本。
- 配置区分 dev/test/staging/prod，提供一个不含密钥的示例；启动时校验缺失项，日志不输出密钥。
- 数据库初始化和迁移退出应用启动路径，改成独立发布步骤。先验证已有 SQLite 到 PostgreSQL 的数据映射、外键、索引、JSON、时间戳与修订语义。
- 每次迁移前备份并演练恢复。Schema 采用先扩展、再切换、后删除；切换后已有新写入时，不能简单改回旧数据库连接声称回滚成功。
- 核心业务接口统一错误码、request_id、分页和并发版本校验；活跃客户端同步迁移，退休功能接口不强制保留兼容期。
- 接口客户端可由 OpenAPI 生成；事件 Schema 必须版本化，不能把 Python 内部类直接当跨服务契约。
- 根开发说明规定中文文档、模块地图、禁止依赖和测试入口。复杂目录才补局部说明，避免每层复制同一份规范。

## 7. 最先做的实际变更

第一批先做：**主线与删除清单、存活功能基线、无关功能整链路裁剪**。随后统一开发入口并整理留下的模块。先减少要维护的东西，再降低剩余部分的维护成本。
