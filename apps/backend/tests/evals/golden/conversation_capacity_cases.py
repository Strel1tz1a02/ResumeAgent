"""正常简历协作对话的硬容量评测数据。"""

from __future__ import annotations

from typing import Any

_REPRESENTATIVE_DOMAIN_DATA: dict[str, object] = {
    "experience": {
        "experience_id": 7,
        "kind": "work",
        "title": "支付平台稳定性改造",
        "organization": "星河科技",
        "role": "后端工程师",
        "location": "上海",
        "start_date": "2024-03",
        "end_date": "2025-01",
        "is_current": False,
        "background": "负责支付网关接口优化，解决大促期间超时问题。",
        "technologies": [
            "Python",
            "FastAPI",
            "Redis",
            "Prometheus",
            "Grafana",
            "OpenTelemetry",
        ],
        "evidence_items": [
            {
                "id": 71,
                "background": "大促期间支付查询接口超时",
                "action": "使用 FastAPI 异步接口和 Redis 缓存重构查询链路",
                "result": "P99 延迟从 420ms 降至 180ms，吞吐量提升 35%",
            },
            {
                "id": 72,
                "background": "线上告警噪声高且故障定位缓慢",
                "action": "建设指标、链路追踪与分级告警看板",
                "result": "平均故障恢复时间从 50 分钟降至 18 分钟",
            },
        ],
    },
    "job_description": {
        "title": "高级后端平台工程师",
        "location": "上海",
        "requirements": [
            "熟练使用 Python 并具备 FastAPI 生产经验",
            "具备 Redis、高并发服务和性能优化经验",
            "具备可观测性、故障治理和平台稳定性经验",
            "了解 Kubernetes 生产运维",
        ],
    },
    "preferences": {
        "language": "zh",
        "page_limit": 1,
        "delivery": "先 Markdown，确认后再导出 PDF",
        "truthfulness": "不得补造经历或指标",
    },
    "scope": {"field": "background"},
    "scope_status": "complete",
    "scope_revision": 12,
}


_SETUP_TURNS: tuple[tuple[str, str], ...] = (
    (
        "我准备申请高级后端平台工程师，请先按这个方向处理后面的简历。",
        "目标岗位确定为高级后端平台工程师，后续内容会优先证明平台稳定性、性能优化和服务治理能力。",
    ),
    (
        "工作地点只考虑上海，不需要为其他城市准备版本。",
        "地点范围更新为上海，岗位标题和交付版本都会保持一致，不再扩展其他城市。",
    ),
    (
        "请用中文沟通，但 FastAPI、Redis 这类技术名保留英文。",
        "后续说明使用中文，技术专有名词保留常用英文写法，并统一大小写。",
    ),
    (
        "简历控制在一页，不能为了塞内容把字号缩得很小。",
        "采用一页版本，优先压缩重复和弱相关内容，不牺牲核心证据与可读性。",
    ),
    (
        "没有提供的经历和数字都不能补，缺什么就直接告诉我。",
        "事实边界设为只使用现有材料，无来源职责、技术和指标都会明确标成缺口。",
    ),
    (
        "JD 强调 Python、FastAPI、Redis、可观测性和 Kubernetes，请先拆成检查项。",
        "JD 已拆成语言与框架、缓存和并发、可观测性、稳定性及 Kubernetes 五类要求。",
    ),
    (
        "支付平台经历里，我负责的是接口和缓存改造，不是整个系统架构。",
        "职责范围限定为接口与缓存改造，后续使用“负责”描述具体工作，不写成主导整体架构。",
    ),
    (
        "P99 延迟是从 420ms 降到 180ms，吞吐量提升 35%，不要改数字对象。",
        "性能指标按原值记录，P99 延迟和吞吐量分别对应查询链路，不与其他结果混用。",
    ),
    (
        "告警改造后 MTTR 从 50 分钟降到 18 分钟，这条可以写。",
        "MTTR 结果有明确基线与终值，可以与指标、链路追踪和分级告警行动一起呈现。",
    ),
    (
        "先给 Markdown 草稿，内容确认以后再导出 PDF。",
        "交付顺序确定为先审 Markdown、再生成 PDF，排版不会提前改变已确认内容。",
    ),
)


_REVIEW_TARGETS: tuple[tuple[str, str], ...] = (
    ("Summary 第一行", "高级后端平台工程师定位与现有后端经历"),
    ("Summary 第二行", "性能优化、稳定性与可观测性证据"),
    ("目标职位标题", "已确认的岗位方向和上海地点"),
    ("支付平台首条经历", "FastAPI 接口优化与 P99 延迟证据"),
    ("故障恢复经历", "告警处理链路与 MTTR 证据"),
    ("Redis 相关表述", "缓存使用场景和已有性能结果"),
    ("异步处理表述", "FastAPI 异步接口与峰值流量事实"),
    ("缓存改造表述", "Redis 缓存重构的真实职责范围"),
    ("链路追踪表述", "OpenTelemetry 的真实使用场景"),
    ("Prometheus 技能", "指标采集和分级告警证据"),
    ("Grafana 技能", "监控看板与故障定位证据"),
    ("Python 技能", "工作经历中的实际开发内容"),
    ("FastAPI 技能", "支付网关接口改造证据"),
    ("Redis 技能", "缓存和性能优化证据"),
    ("可观测性关键词", "指标、日志和追踪的直接证据"),
    ("支付平台背景", "大促期间超时问题和职责范围"),
    ("职责动词", "负责接口和缓存改造这一事实边界"),
    ("P99 延迟数字", "420ms 降至 180ms 的对象与方向"),
    ("吞吐量数字", "查询链路吞吐量提升 35%"),
    ("MTTR 数字", "50 分钟降至 18 分钟的恢复指标"),
    ("公司名称", "星河科技这一原始事实"),
    ("任职日期", "2024-03 至 2025-01 的原始记录"),
    ("工作地点", "已确认只保留上海的版本范围"),
    ("经历排序", "JD 相关性优先且日期仍清楚"),
    ("bullet 长度", "一页限制下保留行动与结果"),
    ("技能小节", "只保留与当前 JD 相关且有证据的技能"),
    ("Kubernetes 缺口", "材料中没有 Kubernetes 生产经历"),
    ("JD 要求映射", "每项要求对应的经历与直接证据"),
    ("Markdown 交付", "先确认文字再导出 PDF 的流程"),
)


_REVIEW_PASSES: tuple[tuple[str, str, str], ...] = (
    (
        "岗位相关性",
        "判断内容是否直接帮助招聘者确认岗位匹配，不要只替换关键词",
        "先判断该内容是否值得占据一页空间，再比较它对平台稳定性、性能优化和服务治理的证明力度。弱相关背景会缩短，能够直接回答 JD 的行动与结果会前置；如果只出现相近技术名而没有真实使用场景，只能视为部分相关，不能包装成直接经验。",
    ),
    (
        "事实接地",
        "逐项核对主体、动作、技术和结果是否都能回到现有材料",
        "检查会沿着主体、职责、技术、对象和结果逐项回溯。任何一句若无法指出对应材料，就保留更克制的安全版本或进入缺口清单；不会根据常见做法推断团队规模、系统架构、用户量或额外指标，也不会把工具名称自动升级为生产能力。",
    ),
    (
        "职责边界",
        "确认动词准确区分主导、负责、参与和协助",
        "动词强度必须与现有职责一致。负责某个接口或缓存链路，不等于主导整个系统架构；参与监控建设，也不能写成独立搭建完整平台。候选句会明确行动对象与责任范围，让招聘者能理解真实贡献，同时避免因为措辞过弱而掩盖已经确认的工作。",
    ),
    (
        "数字边界",
        "检查所有数字的对象、单位、基线和时间范围，不新增推算值",
        "所有指标都要绑定具体对象和变化方向。P99 延迟、吞吐量与 MTTR 分别核对，不能交换基线、单位或结果；没有原始数字的内容只描述可验证行动和定性影响。若一句话无法容纳完整口径，会拆分表达，而不是省略导致数字含义发生变化。",
    ),
    (
        "因果清晰度",
        "检查行动与结果之间是否存在材料支持的直接关系",
        "候选表达会把问题、行动和结果连成一条可追问的证据链。技术名只有在解释解决方式时才保留，不能靠堆叠名词制造因果；如果现有材料只能证明参与某项行动，结果就按团队或项目口径克制描述，不暗示全部改善由个人单独完成。",
    ),
    (
        "篇幅压缩",
        "在一页限制下删除铺垫和重复，但保留关键行动与结果",
        "压缩顺序是先删空泛开场，再合并重复背景，最后比较弱相关内容。公司、日期、职责边界和已确认指标不会为了缩短而省略到失真；同一证据若已在经历中完整呈现，Summary 只做概括，技能小节只保留名称，从而减少跨小节重复。",
    ),
    (
        "快速扫描",
        "检查招聘者能否快速识别对象、方法和结果",
        "句子前半段应尽快出现关键行动与对象，后半段给出方法或结果。连续技术名会改成与动作绑定的表达，长背景会移到必要位置；调整只改变信息顺序和节奏，不改变事实、指标或岗位方向。若一条包含两个独立成果，会比较价值后拆分或取舍。",
    ),
    (
        "跨小节一致性",
        "核对 Summary、技能、经历和缺口清单是否采用同一事实口径",
        "同一能力在不同小节中必须保持相同职责与证据强度。Summary 不能概括正文没有的优势，技能名必须能在经历里找到真实场景，缺口项也不能被其他段落偷偷写成已掌握。重复内容只保留必要呼应，避免用多种说法制造虚假的证据数量。",
    ),
    (
        "关键词自然度",
        "确认 JD 关键词出现在真实行动语境中，而不是机械堆叠",
        "关键词是否保留取决于经历能否证明，而不是出现频率。FastAPI、Redis 和 OpenTelemetry 应与具体对象、行动和结果连用；Kubernetes 没有生产事实，只能明确列为缺口。相邻经验可以如实说明迁移性，但不能直接改写成 JD 要求的技术经历。",
    ),
    (
        "最终风险",
        "检查旧设置、夸大职责和无依据技术是否重新进入成稿",
        "收口检查会优先寻找诚信风险，再检查岗位、地点、日期和交付范围。发现疑点只标注来源缺失，不临时补全；已经确认的句子除非存在明确错误，否则不做风格性重写。最终版本继续保持中文、一页、上海岗位范围，并先交付 Markdown。",
    ),
)


_REVIEW_STAGES = (
    "初稿",
    "第一轮修改稿",
    "JD 补充版",
    "最终投递版",
)


_COMMON_ASSISTANT_SUFFIX = (
    "本轮只处理当前检查项，不改变已经确认的岗位方向、地点、事实和交付范围。"
    "我会给出可直接使用的修改、保留理由以及仍需补充的唯一证据；如果现有写法已经准确，"
    "就保持原句，不为制造变化而重写。完成后还会快速核对相邻小节，确保本轮调整没有引入"
    "新的重复、冲突或无来源信息。具体建议会说明修改前后的差异及证据出处，方便逐句确认；"
    "证据不足时保留安全写法，不用更强动词或模糊形容词掩盖缺口。"
)


def _build_turns() -> list[dict[str, Any]]:
    turns: list[dict[str, Any]] = []
    for user, conclusion in _SETUP_TURNS:
        round_number = len(turns) + 1
        turns.append(
            {
                "round": round_number,
                "user": user,
                "assistant": (
                    f"结论：{conclusion}我会先把本轮确认应用到相关小节，再检查它是否"
                    "影响已有内容的排序、篇幅或证据强度。没有材料支持的内容保持为空，"
                    "尚未确认的问题不会被当成最终决定。"
                    f"{_COMMON_ASSISTANT_SUFFIX}"
                ),
            }
        )

    for stage in _REVIEW_STAGES:
        for focus, instruction, detail in _REVIEW_PASSES:
            for target, evidence in _REVIEW_TARGETS:
                round_number = len(turns) + 1
                turns.append(
                    {
                        "round": round_number,
                        "user": (
                            f"继续检查{stage}的{target}。这一轮只看{focus}：{instruction}。"
                            "请给出结论、具体修改和仍缺的证据，不要改动其他已确认内容。"
                        ),
                        "assistant": (
                            f"结论：{stage}的{target}按{focus}复核，当前依据限定为{evidence}。"
                            f"{detail}{_COMMON_ASSISTANT_SUFFIX}"
                        ),
                    }
                )
                if len(turns) == 1000:
                    return turns
    return turns


_TURNS = _build_turns()


_WORKLOAD_PROFILE: dict[str, object] = {
    "length_unit": "unicode_characters",
    "user_target_chars": {"minimum": 20, "maximum": 180},
    "assistant_target_chars": {"minimum": 250, "maximum": 900},
    "minimum_matching_turn_ratio": 0.9,
    "description": (
        "连续简历协作对话，包含事实确认、JD 对齐、逐项审阅与定稿检查；"
        "不使用随机串、高熵锚点或无意义字符填充。"
    ),
}


CONVERSATION_CAPACITY_CASES: list[dict[str, object]] = [
    {
        "name": "realistic_resume_workflow",
        "version": 4,
        "max_rounds": 300,
        "hard_max_rounds": 1000,
        "workload_profile": _WORKLOAD_PROFILE,
        "representative_domain_data": _REPRESENTATIVE_DOMAIN_DATA,
        "turns": _TURNS,
    }
]


def _validate_case(case: dict[str, object]) -> None:
    turns = case["turns"]
    if not isinstance(turns, list) or len(turns) != 1000:
        raise ValueError("正常对话样本必须恰好包含 1000 轮")
    if case["max_rounds"] != 300 or case["hard_max_rounds"] != 1000:
        raise ValueError("默认轮数必须为 300，样本上限必须为 1000")
    if len({str(turn["user"]) for turn in turns}) != len(turns):
        raise ValueError("用户消息不得重复")
    if len({str(turn["assistant"]) for turn in turns}) != len(turns):
        raise ValueError("助手回复不得重复")

    for expected_round, turn in enumerate(turns, 1):
        if set(turn) != {"round", "user", "assistant"}:
            raise ValueError(f"第 {expected_round} 轮字段不符合容量测试最小结构")
        if turn["round"] != expected_round:
            raise ValueError("turn round 必须从 1 到 1000 连续递增")
        if not str(turn["user"]).strip() or not str(turn["assistant"]).strip():
            raise ValueError(f"第 {expected_round} 轮消息不能为空")

    profile = case["workload_profile"]
    if not isinstance(profile, dict):
        raise TypeError("workload_profile 必须是对象")
    required = int(len(turns) * float(profile["minimum_matching_turn_ratio"]))
    user_matches = sum(20 <= len(str(turn["user"])) <= 180 for turn in turns)
    assistant_matches = sum(250 <= len(str(turn["assistant"])) <= 900 for turn in turns)
    if user_matches < required or assistant_matches < required:
        raise ValueError(
            "对话长度未达到 workload_profile: "
            f"user={user_matches}, assistant={assistant_matches}, required={required}"
        )


for _case in CONVERSATION_CAPACITY_CASES:
    _validate_case(_case)
