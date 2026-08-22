"""按能力域维护的人工黄金样本。"""

from __future__ import annotations

_GENERATION_EXPERIENCES = [
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
        "experience_id": 3,
        "kind": "work",
        "title": "结算系统迁移",
        "organization": "星河科技",
        "role": "项目负责人",
        "start_date": "2023-08",
        "end_date": "2024-02",
        "background": "老结算服务需要无停机迁移",
        "technologies": ["Kafka", "PostgreSQL"],
        "tags": ["跨团队协作"],
        "completeness": 90,
        "updated_at": "2026-08-01T00:00:00+00:00",
        "evidence": [
            {
                "evidence_id": 103,
                "background": "产品、测试和运维存在依赖冲突",
                "action": "组织产品、测试、运维评审迁移窗口与回滚方案",
                "result": "按期完成上线，迁移期间零停机",
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
        "start_date": "2024-06",
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
        "experience_id": 5,
        "kind": "project",
        "title": "设计系统",
        "role": "前端工程师",
        "start_date": "2024-01",
        "end_date": "2024-05",
        "background": "多个页面视觉规范不一致",
        "technologies": ["React", "Storybook"],
        "tags": ["前端"],
        "completeness": 88,
        "updated_at": "2026-08-01T00:00:00+00:00",
        "evidence": [
            {
                "evidence_id": 105,
                "action": "沉淀 React 组件与 Storybook 文档",
                "result": "统一 40 个页面的交互规范",
                "updated_at": "2026-08-01T00:00:00+00:00",
            }
        ],
    },
    {
        "experience_id": 6,
        "kind": "project",
        "title": "财务报表自动化",
        "role": "数据分析师",
        "start_date": "2023-03",
        "end_date": "2023-07",
        "background": "月度报表依赖手工汇总",
        "technologies": ["SQL", "Excel"],
        "tags": ["数据分析"],
        "completeness": 85,
        "updated_at": "2026-08-01T00:00:00+00:00",
        "evidence": [
            {
                "evidence_id": 106,
                "action": "编写 SQL 汇总脚本并生成 Excel 报表",
                "result": "每月节省 6 小时人工整理时间",
                "updated_at": "2026-08-01T00:00:00+00:00",
            }
        ],
    },
]

RESUME_GENERATION_CASES = [
    {
        "name": "backend-platform-engineer",
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
        "experiences": _GENERATION_EXPERIENCES,
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
    }
]
