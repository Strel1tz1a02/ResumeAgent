"""按候选人职业轨迹维护的固定合成简历生成黄金样本。"""

from __future__ import annotations

_BACKEND_PROFILE = [
    {
        "experience_id": 1,
        "kind": "work",
        "title": "支付平台稳定性改造",
        "organization": "星河科技",
        "role": "后端工程师",
        "start_date": "2024-03",
        "end_date": "2025-01",
        "background": "交易峰值期间支付网关延迟升高",
        "technologies": ["Python", "FastAPI", "Redis"],
        "tags": ["性能优化"],
        "completeness": 95,
        "updated_at": "2026-08-01T00:00:00+00:00",
        "evidence": [
            {
                "evidence_id": 101,
                "background": "大促期间接口超时",
                "action": "使用 FastAPI 异步接口与 Redis 缓存重构支付查询链路",
                "result": "P99 延迟从 420ms 降至 180ms，吞吐量提升 35%",
                "updated_at": "2026-08-01T00:00:00+00:00",
            }
        ],
    },
    {
        "experience_id": 4,
        "kind": "work",
        "title": "可观测性治理",
        "organization": "星河科技",
        "role": "后端工程师",
        "start_date": "2025-02",
        "is_current": True,
        "background": "线上告警噪声高且定位缓慢",
        "technologies": ["Prometheus", "Grafana", "OpenTelemetry"],
        "tags": ["SRE", "可观测性"],
        "completeness": 94,
        "updated_at": "2026-08-01T00:00:00+00:00",
        "evidence": [
            {
                "evidence_id": 104,
                "background": "故障发现和定位依赖人工排查",
                "action": "建设指标、链路追踪与分级告警看板",
                "result": "平均故障恢复时间 MTTR 从 50 分钟降至 18 分钟",
                "updated_at": "2026-08-01T00:00:00+00:00",
            }
        ],
    },
    {
        "experience_id": 7,
        "kind": "work",
        "title": "内部运营后台",
        "organization": "蓝帆软件",
        "role": "后端工程师",
        "start_date": "2023-05",
        "end_date": "2024-02",
        "background": "运营配置依赖研发重复编写后台接口",
        "technologies": ["Python", "FastAPI"],
        "tags": ["内部工具"],
        "completeness": 86,
        "updated_at": "2026-08-01T00:00:00+00:00",
        "evidence": [
            {
                "evidence_id": 107,
                "background": "相似的运营配置页面反复开发",
                "action": "使用 FastAPI 封装通用 CRUD 配置接口",
                "result": "每周节省 6 小时重复配置时间",
                "updated_at": "2026-08-01T00:00:00+00:00",
            }
        ],
    },
]

_RETRIEVAL_PROFILE = [
    {
        "experience_id": 2,
        "kind": "project",
        "title": "企业知识库 RAG",
        "role": "算法工程师",
        "start_date": "2025-02",
        "end_date": "2025-06",
        "background": "内部文档问答命中不稳定",
        "technologies": ["Qdrant", "BM25", "Embedding"],
        "tags": ["RAG", "混合检索"],
        "completeness": 92,
        "updated_at": "2026-08-01T00:00:00+00:00",
        "evidence": [
            {
                "evidence_id": 102,
                "background": "仅稠密检索漏掉产品型号和缩写",
                "action": "实现 dense+sparse 混合召回，并建立 Recall@10 离线评估集",
                "result": "Recall@10 从 0.72 提升至 0.89",
                "updated_at": "2026-08-01T00:00:00+00:00",
            }
        ],
    },
    {
        "experience_id": 22,
        "kind": "project",
        "title": "检索失败切片分析",
        "role": "算法工程师",
        "start_date": "2025-07",
        "end_date": "2025-09",
        "background": "总 Recall 指标无法解释具体漏召回类型",
        "technologies": ["Python"],
        "tags": ["离线评估", "错误分析"],
        "completeness": 90,
        "updated_at": "2026-08-02T00:00:00+00:00",
        "evidence": [
            {
                "evidence_id": 122,
                "background": "型号、缩写和长尾问法的失败原因混在总指标中",
                "action": "使用 Python 按查询类型构建固定难例切片并逐版本回归",
                "result": "将人工抽样分析改为可重复的离线评估流程",
                "updated_at": "2026-08-02T00:00:00+00:00",
            }
        ],
    },
    {
        "experience_id": 21,
        "kind": "project",
        "title": "客服意图分类",
        "role": "算法工程师",
        "start_date": "2024-09",
        "end_date": "2024-12",
        "background": "工单意图依赖人工归类",
        "technologies": ["Python", "Embedding"],
        "tags": ["文本分类"],
        "completeness": 87,
        "updated_at": "2026-08-02T00:00:00+00:00",
        "evidence": [
            {
                "evidence_id": 121,
                "background": "相近意图容易混淆",
                "action": "使用 Embedding 特征训练轻量分类器",
                "result": "离线分类准确率达到 91%",
                "updated_at": "2026-08-01T00:00:00+00:00",
            }
        ],
    },
]

_MIGRATION_PROFILE = [
    {
        "experience_id": 3,
        "kind": "work",
        "title": "结算系统迁移",
        "organization": "星河科技",
        "role": "项目负责人",
        "start_date": "2023-08",
        "end_date": "2024-02",
        "background": "老结算服务需要无停机迁移",
        "technologies": ["Kafka", "PostgreSQL"],
        "tags": ["跨团队协作", "迁移"],
        "completeness": 93,
        "updated_at": "2026-08-03T00:00:00+00:00",
        "evidence": [
            {
                "evidence_id": 103,
                "background": "产品、测试和运维存在依赖冲突",
                "action": "组织产品、测试、运维评审迁移窗口与回滚方案",
                "result": "按期完成上线，迁移期间零停机",
                "updated_at": "2026-08-03T00:00:00+00:00",
            }
        ],
    },
    {
        "experience_id": 32,
        "kind": "work",
        "title": "双写数据核对",
        "organization": "星河科技",
        "role": "后端工程师",
        "start_date": "2024-03",
        "end_date": "2024-08",
        "background": "迁移双写期间需要快速确认新旧账务一致",
        "technologies": ["Kafka", "PostgreSQL"],
        "tags": ["数据核对", "迁移"],
        "completeness": 91,
        "updated_at": "2026-08-03T00:00:00+00:00",
        "evidence": [
            {
                "evidence_id": 132,
                "background": "人工全量核对无法满足迁移窗口",
                "action": "基于 Kafka offset 与 PostgreSQL 校验和实现双写核对",
                "result": "将全量核对从 4 小时缩短至 45 分钟",
                "updated_at": "2026-08-03T00:00:00+00:00",
            }
        ],
    },
    {
        "experience_id": 31,
        "kind": "work",
        "title": "Kafka 积压告警治理",
        "organization": "蓝帆软件",
        "role": "后端工程师",
        "start_date": "2022-10",
        "end_date": "2023-03",
        "background": "消费延迟告警误报较多",
        "technologies": ["Kafka", "Prometheus"],
        "tags": ["监控"],
        "completeness": 88,
        "updated_at": "2026-08-03T00:00:00+00:00",
        "evidence": [
            {
                "evidence_id": 131,
                "background": "固定阈值无法适配业务峰谷",
                "action": "按 Topic 基线调整 Kafka consumer lag 告警规则",
                "result": "告警误报下降 60%",
                "updated_at": "2026-08-03T00:00:00+00:00",
            }
        ],
    },
]

_FRONTEND_PROFILE = [
    {
        "experience_id": 5,
        "kind": "project",
        "title": "设计系统",
        "role": "前端工程师",
        "start_date": "2024-01",
        "end_date": "2024-05",
        "background": "多个页面视觉规范不一致",
        "technologies": ["React", "Storybook"],
        "tags": ["前端", "设计系统"],
        "completeness": 91,
        "updated_at": "2026-08-04T00:00:00+00:00",
        "evidence": [
            {
                "evidence_id": 105,
                "background": "业务重复实现相近交互",
                "action": "沉淀 React 组件与 Storybook 文档",
                "result": "统一 40 个页面的交互规范",
                "updated_at": "2026-08-04T00:00:00+00:00",
            }
        ],
    },
    {
        "experience_id": 52,
        "kind": "work",
        "title": "组件可访问性检查",
        "organization": "木棉科技",
        "role": "前端工程师",
        "start_date": "2024-06",
        "end_date": "2024-12",
        "background": "组件上线前缺少统一的可访问性验证",
        "technologies": ["React", "Storybook", "axe-core"],
        "tags": ["设计系统", "可访问性"],
        "completeness": 90,
        "updated_at": "2026-08-04T00:00:00+00:00",
        "evidence": [
            {
                "evidence_id": 152,
                "background": "键盘操作与颜色对比问题依赖人工发现",
                "action": "在 Storybook 中接入 axe-core 自动检查 React 组件",
                "result": "上线前拦截 28 个可访问性问题",
                "updated_at": "2026-08-04T00:00:00+00:00",
            }
        ],
    },
    {
        "experience_id": 51,
        "kind": "project",
        "title": "活动落地页性能优化",
        "role": "前端工程师",
        "start_date": "2023-08",
        "end_date": "2023-10",
        "background": "营销页面首屏加载缓慢",
        "technologies": ["React", "Vite"],
        "tags": ["性能优化"],
        "completeness": 89,
        "updated_at": "2026-08-04T00:00:00+00:00",
        "evidence": [
            {
                "evidence_id": 151,
                "background": "大图和未拆分脚本阻塞首屏",
                "action": "使用 Vite 分包并优化 React 页面资源加载",
                "result": "LCP 从 3.1s 降至 1.8s",
                "updated_at": "2026-08-04T00:00:00+00:00",
            }
        ],
    },
]

_ANALYTICS_PROFILE = [
    {
        "experience_id": 6,
        "kind": "project",
        "title": "财务报表自动化",
        "role": "数据分析师",
        "start_date": "2023-03",
        "end_date": "2023-07",
        "background": "月度报表依赖手工汇总",
        "technologies": ["SQL", "Excel"],
        "tags": ["数据分析", "自动化"],
        "completeness": 90,
        "updated_at": "2026-08-05T00:00:00+00:00",
        "evidence": [
            {
                "evidence_id": 106,
                "background": "多份业务表需要人工复制合并",
                "action": "编写 SQL 汇总脚本并生成 Excel 报表",
                "result": "每月节省 6 小时人工整理时间",
                "updated_at": "2026-08-05T00:00:00+00:00",
            }
        ],
    },
    {
        "experience_id": 62,
        "kind": "work",
        "title": "门店数据质量检查",
        "organization": "远航零售",
        "role": "数据分析师",
        "start_date": "2023-08",
        "end_date": "2024-02",
        "background": "月结前需要逐门店核对异常数据",
        "technologies": ["SQL", "Excel"],
        "tags": ["数据质量", "自动化"],
        "completeness": 92,
        "updated_at": "2026-08-05T00:00:00+00:00",
        "evidence": [
            {
                "evidence_id": 162,
                "background": "人工核对耗时且容易漏掉异常门店",
                "action": "用 SQL 规则识别异常并生成 Excel 例外报表",
                "result": "将月结核对从 2 天缩短至 3 小时",
                "updated_at": "2026-08-05T00:00:00+00:00",
            }
        ],
    },
    {
        "experience_id": 61,
        "kind": "work",
        "title": "销售专题分析",
        "organization": "远航零售",
        "role": "数据分析师",
        "start_date": "2022-10",
        "end_date": "2023-02",
        "background": "区域团队需要理解季节性销售变化",
        "technologies": ["SQL", "Excel"],
        "tags": ["专题分析"],
        "completeness": 88,
        "updated_at": "2026-08-05T00:00:00+00:00",
        "evidence": [
            {
                "evidence_id": 161,
                "background": "现有周报缺少品类与区域拆分",
                "action": "使用 SQL 与 Excel 完成一次性销售专题分析",
                "result": "支持区域团队制定季度促销策略",
                "updated_at": "2026-08-05T00:00:00+00:00",
            }
        ],
    },
]

_BASE_GENERATION_THRESHOLDS = {
    "validation_valid": True,
    "model_validation_status": "completed",
    "minimum_requirement_coverage": 0.75,
    "minimum_grounded_number_precision": 1.0,
    "minimum_bullets": 1,
    "minimum_experience_precision": 1.0,
    "minimum_experience_recall": 1.0,
    "minimum_experience_f1": 1.0,
    "minimum_evidence_precision": 1.0,
    "minimum_evidence_recall": 1.0,
    "minimum_evidence_f1": 1.0,
    "minimum_technical_skill_precision": 1.0,
    "minimum_technical_skill_recall": 1.0,
    "minimum_technical_skill_f1": 1.0,
    "minimum_summary_fact_recall": 0.75,
    "minimum_required_section_recall": 1.0,
    "minimum_core_fact_recall": 0.85,
    "minimum_provenance_grounded_number_precision": 1.0,
    "minimum_judge_overall": 4,
    "minimum_judge_grounding": 4,
}

_DEFAULT_GENERATION_CONSTRAINTS = {
    "max_work_experiences": 3,
    "max_project_experiences": 2,
    "max_bullets_per_experience": 3,
    "top_k_per_task": 6,
    "max_search_rounds": 1,
    "min_coverage_ratio": 0.70,
}

_SYNTHETIC_REFERENCE_REVIEW = {
    "status": "synthetic_fixture_not_expert_reviewed",
    "expert_reviewed": False,
    "blind_reviewed": False,
    "reviewer_role": "fixture_author",
    "review_method": "provenance_and_scorer_self_check",
    "reviewed_at": None,
}

RESUME_GENERATION_CASES = [
    {
        "name": "backend-platform-engineer",
        "version": "2",
        "candidate_profile_id": "backend-platform-profile",
        "reference_status": "synthetic_fixture_not_expert_reviewed",
        "reference_review": {**_SYNTHETIC_REFERENCE_REVIEW},
        "jd_source": {
            "id": 1,
            "company": "云舟网络",
            "job_name": "高级后端工程师",
            "type": "backend",
            "location": "上海",
            "status": "confirmed",
            "revision": 0,
            "requirements": [
                {
                    "id": 1,
                    "priority": "required",
                    "content": "使用 Python 与 FastAPI 设计高并发 API",
                    "sort_order": 0,
                    "revision": 0,
                },
                {
                    "id": 2,
                    "priority": "required",
                    "content": "具备 Redis 缓存和性能优化经验",
                    "sort_order": 1,
                    "revision": 0,
                },
                {
                    "id": 3,
                    "priority": "required",
                    "content": "建设可观测性并降低线上故障恢复时间",
                    "sort_order": 2,
                    "revision": 0,
                },
                {
                    "id": 4,
                    "priority": "preferred",
                    "content": "了解 Kubernetes 容器编排",
                    "sort_order": 3,
                    "revision": 0,
                },
            ],
        },
        "experiences": _BACKEND_PROFILE,
        "constraints": {
            "max_work_experiences": 2,
            "max_project_experiences": 1,
            "max_bullets_per_experience": 3,
            "top_k_per_task": 6,
            "max_search_rounds": 1,
            "min_coverage_ratio": 0.70,
        },
        # 固定参考答案用于稳定回归，不要求模型逐字复现；稳定取舍由 oracle 的
        # Experience/Evidence ID 和核心事实组表达，不能冒充专家质量真值。
        "reference_resume": {
            "summary": (
                "后端工程师，具备 Python/FastAPI 异步 API、Redis 缓存性能优化"
                "和可观测性治理经验。"
            ),
            "workExperience": [
                {
                    "id": 1,
                    "title": "后端工程师",
                    "company": "星河科技",
                    "years": "2024-03 - 2025-01",
                    "description": [
                        (
                            "使用 FastAPI 异步接口与 Redis 缓存重构支付查询链路，"
                            "P99 延迟从 420ms 降至 180ms，吞吐量提升 35%"
                        )
                    ],
                },
                {
                    "id": 4,
                    "title": "后端工程师",
                    "company": "星河科技",
                    "years": "2025-02 - 至今",
                    "description": [
                        (
                            "建设指标、链路追踪与分级告警看板，平均故障恢复时间 "
                            "MTTR 从 50 分钟降至 18 分钟"
                        )
                    ],
                },
            ],
            "personalProjects": [],
            "additional": {
                "technicalSkills": [
                    "Python",
                    "FastAPI",
                    "Redis",
                    "Prometheus",
                    "Grafana",
                    "OpenTelemetry",
                ]
            },
        },
        "reference_provenance": {
            "summary_evidence_ids": [101, 104],
            "bullets": [
                {
                    "section": "workExperience",
                    "item_id": 1,
                    "bullet_index": 0,
                    "evidence_ids": [101],
                },
                {
                    "section": "workExperience",
                    "item_id": 4,
                    "bullet_index": 0,
                    "evidence_ids": [104],
                },
            ],
            "skills": [
                {"skill": "Python", "evidence_ids": [101]},
                {"skill": "FastAPI", "evidence_ids": [101]},
                {"skill": "Redis", "evidence_ids": [101]},
                {"skill": "Prometheus", "evidence_ids": [104]},
                {"skill": "Grafana", "evidence_ids": [104]},
                {"skill": "OpenTelemetry", "evidence_ids": [104]},
            ],
        },
        "oracle": {
            "supported_requirement_ids": [1, 2, 3],
            "unsupported_requirement_ids": [4],
            "expected_experience_ids": [1, 4],
            "expected_evidence_ids": [101, 104],
            "hard_negative_experience_ids": [7],
            "hard_negative_evidence_ids": [107],
            "core_fact_groups": [
                ["Python"],
                ["FastAPI"],
                ["Redis"],
                ["P99"],
                ["420ms"],
                ["180ms"],
                ["35%"],
                ["可观测性", "指标"],
                ["链路追踪", "OpenTelemetry"],
                ["分级告警"],
                ["MTTR", "故障恢复时间"],
                ["50分钟"],
                ["18分钟"],
            ],
            "summary_fact_groups": [
                ["Python"],
                ["FastAPI"],
                ["Redis"],
                ["可观测性"],
            ],
        },
        "thresholds": {**_BASE_GENERATION_THRESHOLDS, "minimum_bullets": 2},
        "requirement_groups": [
            ["Python", "FastAPI"],
            ["Redis"],
            ["可观测性", "链路追踪", "MTTR", "故障恢复"],
            ["Kubernetes", "K8s"],
        ],
        "forbidden_fragments": [
            "字节跳动",
            "腾讯",
            "10人团队",
            "营收翻倍",
            "Kubernetes",
            "K8s",
        ],
    },
    {
        "name": "retrieval-evaluation-engineer",
        "version": "2",
        "candidate_profile_id": "retrieval-evaluation-profile",
        "reference_status": "synthetic_fixture_not_expert_reviewed",
        "reference_review": {**_SYNTHETIC_REFERENCE_REVIEW},
        "jd_source": {
            "id": 2,
            "company": "星图智能",
            "job_name": "检索算法工程师",
            "type": "ai",
            "location": "北京",
            "status": "confirmed",
            "revision": 0,
            "requirements": [
                {
                    "id": 11,
                    "priority": "required",
                    "content": "具备稠密与稀疏混合检索实践",
                    "sort_order": 0,
                    "revision": 0,
                },
                {
                    "id": 12,
                    "priority": "required",
                    "content": "熟悉 Qdrant、BM25 和 Embedding",
                    "sort_order": 1,
                    "revision": 0,
                },
                {
                    "id": 13,
                    "priority": "required",
                    "content": "能够建设 Recall@10 等离线评估",
                    "sort_order": 2,
                    "revision": 0,
                },
                {
                    "id": 14,
                    "priority": "preferred",
                    "content": "有大模型微调经验",
                    "sort_order": 3,
                    "revision": 0,
                },
            ],
        },
        "experiences": _RETRIEVAL_PROFILE,
        "constraints": {
            **_DEFAULT_GENERATION_CONSTRAINTS,
            "max_project_experiences": 2,
        },
        "reference_resume": {
            "summary": (
                "检索算法工程师，具备 Qdrant、BM25、Embedding 混合召回与 "
                "使用 Python 建设 Recall@10 离线评估的经验。"
            ),
            "workExperience": [],
            "personalProjects": [
                {
                    "id": 2,
                    "name": "企业知识库 RAG",
                    "role": "算法工程师",
                    "years": "2025-02 - 2025-06",
                    "description": [
                        (
                            "实现 dense+sparse 混合召回并建设 Recall@10 评估集，"
                            "Recall@10 从 0.72 提升至 0.89"
                        )
                    ],
                },
                {
                    "id": 22,
                    "name": "检索失败切片分析",
                    "role": "算法工程师",
                    "years": "2025-07 - 2025-09",
                    "description": [
                        (
                            "使用 Python 按型号、缩写和长尾问法构建固定难例切片，"
                            "将人工抽样改为可重复的离线评估流程"
                        )
                    ],
                },
            ],
            "additional": {
                "technicalSkills": ["Qdrant", "BM25", "Embedding", "Python"]
            },
        },
        "reference_provenance": {
            "summary_evidence_ids": [102, 122],
            "bullets": [
                {
                    "section": "personalProjects",
                    "item_id": 2,
                    "bullet_index": 0,
                    "evidence_ids": [102],
                },
                {
                    "section": "personalProjects",
                    "item_id": 22,
                    "bullet_index": 0,
                    "evidence_ids": [122],
                },
            ],
            "skills": [
                {"skill": "Qdrant", "evidence_ids": [102]},
                {"skill": "BM25", "evidence_ids": [102]},
                {"skill": "Embedding", "evidence_ids": [102]},
                {"skill": "Python", "evidence_ids": [122]},
            ],
        },
        "oracle": {
            "supported_requirement_ids": [11, 12, 13],
            "unsupported_requirement_ids": [14],
            "expected_experience_ids": [2, 22],
            "expected_evidence_ids": [102, 122],
            "hard_negative_experience_ids": [21],
            "hard_negative_evidence_ids": [121],
            "core_fact_groups": [
                ["Qdrant"],
                ["BM25"],
                ["Embedding"],
                ["dense+sparse", "混合召回"],
                ["Recall@10"],
                ["0.72"],
                ["0.89"],
                ["Python"],
                ["型号"],
                ["缩写"],
                ["长尾问法"],
                ["可重复", "逐版本回归"],
            ],
            "summary_fact_groups": [
                ["Qdrant"],
                ["BM25"],
                ["Embedding"],
                ["Python"],
                ["Recall@10"],
            ],
        },
        "thresholds": {**_BASE_GENERATION_THRESHOLDS, "minimum_bullets": 2},
        "requirement_groups": [
            ["dense+sparse", "混合检索", "混合召回"],
            ["Qdrant", "BM25", "Embedding"],
            ["Recall@10", "离线评估"],
            ["大模型微调", "LLM 微调"],
        ],
        "forbidden_fragments": [
            "大模型微调",
            "LLM 微调",
            "百亿参数",
            "准确率 99%",
        ],
    },
    {
        "name": "zero-downtime-migration-lead",
        "version": "2",
        "candidate_profile_id": "migration-lead-profile",
        "reference_status": "synthetic_fixture_not_expert_reviewed",
        "reference_review": {**_SYNTHETIC_REFERENCE_REVIEW},
        "jd_source": {
            "id": 3,
            "company": "海川支付",
            "job_name": "平台迁移负责人",
            "type": "backend",
            "location": "深圳",
            "status": "confirmed",
            "revision": 0,
            "requirements": [
                {
                    "id": 21,
                    "priority": "required",
                    "content": "熟悉 Kafka 与 PostgreSQL",
                    "sort_order": 0,
                    "revision": 0,
                },
                {
                    "id": 22,
                    "priority": "required",
                    "content": "有零停机迁移和回滚方案经验",
                    "sort_order": 1,
                    "revision": 0,
                },
                {
                    "id": 23,
                    "priority": "required",
                    "content": "能够协调产品、测试和运维团队",
                    "sort_order": 2,
                    "revision": 0,
                },
                {
                    "id": 24,
                    "priority": "preferred",
                    "content": "熟悉 Terraform",
                    "sort_order": 3,
                    "revision": 0,
                },
            ],
        },
        "experiences": _MIGRATION_PROFILE,
        "constraints": {
            **_DEFAULT_GENERATION_CONSTRAINTS,
            "max_work_experiences": 2,
        },
        "reference_resume": {
            "summary": (
                "平台迁移负责人，具备 Kafka、PostgreSQL 零停机迁移、"
                "回滚设计与跨团队协作经验。"
            ),
            "workExperience": [
                {
                    "id": 3,
                    "title": "项目负责人",
                    "company": "星河科技",
                    "years": "2023-08 - 2024-02",
                    "description": [
                        (
                            "组织产品、测试、运维评审迁移窗口与回滚方案，"
                            "按期完成上线并实现迁移期间零停机"
                        )
                    ],
                },
                {
                    "id": 32,
                    "title": "后端工程师",
                    "company": "星河科技",
                    "years": "2024-03 - 2024-08",
                    "description": [
                        (
                            "基于 Kafka offset 与 PostgreSQL 校验和实现双写核对，"
                            "将全量核对从 4 小时缩短至 45 分钟"
                        )
                    ],
                },
            ],
            "personalProjects": [],
            "additional": {"technicalSkills": ["Kafka", "PostgreSQL"]},
        },
        "reference_provenance": {
            "summary_evidence_ids": [103, 132],
            "bullets": [
                {
                    "section": "workExperience",
                    "item_id": 3,
                    "bullet_index": 0,
                    "evidence_ids": [103],
                },
                {
                    "section": "workExperience",
                    "item_id": 32,
                    "bullet_index": 0,
                    "evidence_ids": [132],
                },
            ],
            "skills": [
                {"skill": "Kafka", "evidence_ids": [103]},
                {"skill": "PostgreSQL", "evidence_ids": [103]},
            ],
        },
        "oracle": {
            "supported_requirement_ids": [21, 22, 23],
            "unsupported_requirement_ids": [24],
            "expected_experience_ids": [3, 32],
            "expected_evidence_ids": [103, 132],
            "hard_negative_experience_ids": [31],
            "hard_negative_evidence_ids": [131],
            "core_fact_groups": [
                ["Kafka"],
                ["PostgreSQL"],
                ["产品、测试、运维", "跨团队"],
                ["回滚方案", "回滚设计"],
                ["零停机"],
                ["双写核对"],
                ["4小时", "4 小时"],
                ["45分钟", "45 分钟"],
            ],
            "summary_fact_groups": [
                ["Kafka"],
                ["PostgreSQL"],
                ["零停机"],
                ["跨团队"],
            ],
        },
        "thresholds": {**_BASE_GENERATION_THRESHOLDS, "minimum_bullets": 2},
        "requirement_groups": [
            ["Kafka", "PostgreSQL"],
            ["零停机", "回滚"],
            ["产品", "测试", "运维", "跨团队"],
            ["Terraform"],
        ],
        "forbidden_fragments": ["Terraform", "AWS", "百人团队", "成本降低 50%"],
    },
    {
        "name": "frontend-design-system-engineer",
        "version": "2",
        "candidate_profile_id": "frontend-design-system-profile",
        "reference_status": "synthetic_fixture_not_expert_reviewed",
        "reference_review": {**_SYNTHETIC_REFERENCE_REVIEW},
        "jd_source": {
            "id": 4,
            "company": "木棉设计",
            "job_name": "前端设计系统工程师",
            "type": "frontend",
            "location": "杭州",
            "status": "confirmed",
            "revision": 0,
            "requirements": [
                {
                    "id": 31,
                    "priority": "required",
                    "content": "使用 React 建设可复用组件",
                    "sort_order": 0,
                    "revision": 0,
                },
                {
                    "id": 32,
                    "priority": "required",
                    "content": "维护 Storybook 组件文档",
                    "sort_order": 1,
                    "revision": 0,
                },
                {
                    "id": 33,
                    "priority": "required",
                    "content": "推动多页面交互规范统一",
                    "sort_order": 2,
                    "revision": 0,
                },
                {
                    "id": 34,
                    "priority": "preferred",
                    "content": "熟练使用 TypeScript",
                    "sort_order": 3,
                    "revision": 0,
                },
            ],
        },
        "experiences": _FRONTEND_PROFILE,
        "constraints": {
            **_DEFAULT_GENERATION_CONSTRAINTS,
            "max_work_experiences": 1,
            "max_project_experiences": 1,
        },
        "reference_resume": {
            "summary": (
                "前端工程师，具备 React 设计系统、Storybook 组件文档与"
                "可访问性检查经验。"
            ),
            "workExperience": [
                {
                    "id": 52,
                    "title": "前端工程师",
                    "company": "木棉科技",
                    "years": "2024-06 - 2024-12",
                    "description": [
                        (
                            "在 Storybook 中接入 axe-core 自动检查 React 组件，"
                            "上线前拦截 28 个可访问性问题"
                        )
                    ],
                }
            ],
            "personalProjects": [
                {
                    "id": 5,
                    "name": "设计系统",
                    "role": "前端工程师",
                    "years": "2024-01 - 2024-05",
                    "description": [
                        "沉淀 React 组件与 Storybook 文档，统一 40 个页面的交互规范"
                    ],
                }
            ],
            "additional": {"technicalSkills": ["React", "Storybook", "axe-core"]},
        },
        "reference_provenance": {
            "summary_evidence_ids": [105, 152],
            "bullets": [
                {
                    "section": "workExperience",
                    "item_id": 52,
                    "bullet_index": 0,
                    "evidence_ids": [152],
                },
                {
                    "section": "personalProjects",
                    "item_id": 5,
                    "bullet_index": 0,
                    "evidence_ids": [105],
                },
            ],
            "skills": [
                {"skill": "React", "evidence_ids": [105, 152]},
                {"skill": "Storybook", "evidence_ids": [105, 152]},
                {"skill": "axe-core", "evidence_ids": [152]},
            ],
        },
        "oracle": {
            "supported_requirement_ids": [31, 32, 33],
            "unsupported_requirement_ids": [34],
            "expected_experience_ids": [5, 52],
            "expected_evidence_ids": [105, 152],
            "hard_negative_experience_ids": [51],
            "hard_negative_evidence_ids": [151],
            "core_fact_groups": [
                ["React"],
                ["Storybook"],
                ["40个页面", "40 个页面"],
                ["交互规范"],
                ["axe-core"],
                ["28个", "28 个"],
                ["可访问性"],
            ],
            "summary_fact_groups": [
                ["React"],
                ["Storybook"],
                ["设计系统"],
                ["可访问性"],
            ],
        },
        "thresholds": {**_BASE_GENERATION_THRESHOLDS, "minimum_bullets": 2},
        "requirement_groups": [
            ["React"],
            ["Storybook"],
            ["交互规范", "设计系统"],
            ["TypeScript"],
        ],
        "forbidden_fragments": ["TypeScript", "Vue", "100 个组件", "阿里巴巴"],
    },
    {
        "name": "reporting-automation-analyst",
        "version": "2",
        "candidate_profile_id": "reporting-automation-profile",
        "reference_status": "synthetic_fixture_not_expert_reviewed",
        "reference_review": {**_SYNTHETIC_REFERENCE_REVIEW},
        "jd_source": {
            "id": 5,
            "company": "远航零售",
            "job_name": "数据分析师",
            "type": "data",
            "location": "广州",
            "status": "confirmed",
            "revision": 0,
            "requirements": [
                {
                    "id": 41,
                    "priority": "required",
                    "content": "使用 SQL 自动汇总业务数据",
                    "sort_order": 0,
                    "revision": 0,
                },
                {
                    "id": 42,
                    "priority": "required",
                    "content": "能够生成和维护 Excel 报表",
                    "sort_order": 1,
                    "revision": 0,
                },
                {
                    "id": 43,
                    "priority": "required",
                    "content": "通过自动化减少人工整理时间",
                    "sort_order": 2,
                    "revision": 0,
                },
                {
                    "id": 44,
                    "priority": "preferred",
                    "content": "熟悉 Power BI",
                    "sort_order": 3,
                    "revision": 0,
                },
            ],
        },
        "experiences": _ANALYTICS_PROFILE,
        "constraints": {
            **_DEFAULT_GENERATION_CONSTRAINTS,
            "max_work_experiences": 1,
            "max_project_experiences": 1,
        },
        "reference_resume": {
            "summary": "数据分析师，具备 SQL、Excel 报表自动化与数据质量检查经验。",
            "workExperience": [
                {
                    "id": 62,
                    "title": "数据分析师",
                    "company": "远航零售",
                    "years": "2023-08 - 2024-02",
                    "description": [
                        (
                            "用 SQL 规则识别异常并生成 Excel 例外报表，"
                            "将月结核对从 2 天缩短至 3 小时"
                        )
                    ],
                }
            ],
            "personalProjects": [
                {
                    "id": 6,
                    "name": "财务报表自动化",
                    "role": "数据分析师",
                    "years": "2023-03 - 2023-07",
                    "description": [
                        "编写 SQL 汇总脚本并生成 Excel 报表，每月节省 6 小时人工整理时间"
                    ],
                }
            ],
            "additional": {"technicalSkills": ["SQL", "Excel"]},
        },
        "reference_provenance": {
            "summary_evidence_ids": [106, 162],
            "bullets": [
                {
                    "section": "workExperience",
                    "item_id": 62,
                    "bullet_index": 0,
                    "evidence_ids": [162],
                },
                {
                    "section": "personalProjects",
                    "item_id": 6,
                    "bullet_index": 0,
                    "evidence_ids": [106],
                },
            ],
            "skills": [
                {"skill": "SQL", "evidence_ids": [106]},
                {"skill": "Excel", "evidence_ids": [106]},
            ],
        },
        "oracle": {
            "supported_requirement_ids": [41, 42, 43],
            "unsupported_requirement_ids": [44],
            "expected_experience_ids": [6, 62],
            "expected_evidence_ids": [106, 162],
            "hard_negative_experience_ids": [61],
            "hard_negative_evidence_ids": [161],
            "core_fact_groups": [
                ["SQL"],
                ["Excel"],
                ["每月"],
                ["6小时", "6 小时"],
                ["人工整理时间"],
                ["数据质量"],
                ["2天", "2 天"],
                ["3小时", "3 小时"],
            ],
            "summary_fact_groups": [
                ["SQL"],
                ["Excel"],
                ["报表自动化"],
                ["数据质量"],
            ],
        },
        "thresholds": {**_BASE_GENERATION_THRESHOLDS, "minimum_bullets": 2},
        "requirement_groups": [
            ["SQL"],
            ["Excel"],
            ["节省", "自动化", "人工整理时间"],
            ["Power BI", "PowerBI"],
        ],
        "forbidden_fragments": ["Power BI", "PowerBI", "Tableau", "节省 100 小时"],
    },
]
