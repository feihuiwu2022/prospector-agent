"""Research task planning and evidence-aware status helpers.

The first release intentionally plans work without pretending that external
market data or an LLM has run. Provider adapters can be registered later.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4


AGENT_TASKS = (
    {
        "agentKey": "discovery",
        "title": "机会发现",
        "dimension": "demand",
        "question": "从已授权的日本市场来源中寻找有可追溯需求信号的商品机会。",
        "requiredEvidence": ["日本市场需求信号", "来源链接", "采集日期"],
        "modes": ["discovery", "active_research"],
    },
    {
        "agentKey": "demand",
        "title": "市场需求与趋势",
        "dimension": "demand",
        "question": "目标顾客是否持续表达该需求？需求证据来自哪里、时效如何？",
        "requiredEvidence": ["搜索/销量/趋势或消费者需求来源", "来源链接", "采集日期"],
        "modes": ["discovery", "active_research", "specified_product"],
    },
    {
        "agentKey": "competition",
        "title": "日本站竞品分析",
        "dimension": "competition",
        "question": "竞品数量、价格、品牌集中度和评论痛点如何？新卖家有哪些切入空间？",
        "requiredEvidence": ["日本站竞品页面或市场数据", "来源链接", "采集日期"],
        "modes": ["discovery", "active_research", "specified_product"],
    },
    {
        "agentKey": "sourcing",
        "title": "中国供应链线索",
        "dimension": "sourcing",
        "question": "有哪些可核实的中国供应商线索、采购成本、MOQ、交期和定制能力？",
        "requiredEvidence": ["供应商/采购来源", "报价或询价状态", "采集日期"],
        "modes": ["discovery", "active_research", "specified_product"],
    },
    {
        "agentKey": "economics",
        "title": "利润与履约测算",
        "dimension": "margin",
        "question": "按已输入的售价、采购、FBA、佣金、物流、广告和退货假设测算贡献利润。",
        "requiredEvidence": ["成本或费用依据/明确标为假设", "采集日期或假设日期"],
        "modes": ["discovery", "active_research", "specified_product"],
    },
    {
        "agentKey": "operations",
        "title": "运营与物流可行性",
        "dimension": "operations",
        "question": "体积、重量、易损性、售后、库存周转和履约复杂度是否适合当前模式？",
        "requiredEvidence": ["产品规格/物流或运营依据", "采集日期"],
        "modes": ["discovery", "active_research", "specified_product"],
    },
    {
        "agentKey": "compliance",
        "title": "日本合规与知识产权初筛",
        "dimension": "compliance",
        "question": "是否存在需进一步核实的进口、认证、标签、知识产权或平台限制？",
        "requiredEvidence": ["适用规则/官方来源或待核实标记", "采集日期"],
        "modes": ["discovery", "active_research", "specified_product"],
    },
    {
        "agentKey": "synthesis",
        "title": "综合研究与验证建议",
        "dimension": "summary",
        "question": "综合现有证据、缺口、利润假设与风险，给出可复核摘要和下一步小测计划。",
        "requiredEvidence": ["各专业任务结果", "来源引用", "未决问题"],
        "modes": ["discovery", "active_research", "specified_product"],
    },
)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def build_tasks(candidate_id: str, mode: str) -> list[dict]:
    tasks = []
    for template in AGENT_TASKS:
        if mode not in template["modes"]:
            continue
        tasks.append(
            {
                "id": str(uuid4()),
                "candidateId": candidate_id,
                "agentKey": template["agentKey"],
                "title": template["title"],
                "dimension": template["dimension"],
                "question": template["question"],
                "requiredEvidence": template["requiredEvidence"],
                "status": "blocked" if template["agentKey"] == "synthesis" else "waiting_for_evidence",
                "result": None,
                "createdAt": now_iso(),
                "updatedAt": now_iso(),
            }
        )
    return tasks


def refresh_task_statuses(candidate: dict) -> None:
    evidence = candidate.get("evidence", [])
    tasks = candidate.get("tasks", [])
    for task in tasks:
        key = task["agentKey"]
        if key == "synthesis":
            required = {"demand", "competition", "sourcing", "margin", "operations", "compliance"}
            covered = {item.get("dimension") for item in evidence if item.get("url") and item.get("observedAt")}
            task["status"] = "ready_for_review" if required.issubset(covered) else "blocked"
        else:
            has_source = any(
                item.get("dimension") == task.get("dimension") and item.get("url") and item.get("observedAt")
                for item in evidence
            )
            if has_source and task["status"] == "waiting_for_evidence":
                task["status"] = "evidence_added_review_required"
        task["updatedAt"] = now_iso()

