"""Evidence-aware scoring and profit calculations for Prospector."""

from __future__ import annotations

import math


EVIDENCE_DIMENSION_LABELS = {
    "demand": "需求与趋势",
    "competition": "竞品与市场",
    "sourcing": "供应链与报价",
    "margin": "利润假设",
    "operations": "运营与履约",
    "compliance": "合规与知识产权",
}

# These are four independent assessments. They are deliberately not weighted
# or collapsed into a single opportunity score. Risk is the only inverse axis:
# a higher risk score means more risk, while higher values on the others are
# more favorable.
SCORE_LABELS = {
    "potential": {"label": "机会潜力", "direction": "越高越好"},
    "resource_fit": {"label": "资源匹配度", "direction": "越高越匹配"},
    "risk": {"label": "风险水平", "direction": "越高风险越高"},
    "confidence": {"label": "结论置信度", "direction": "越高证据越充分"},
}

SCORE_EVIDENCE_DIMENSIONS = {
    "potential": {"demand", "competition", "margin"},
    "resource_fit": {"sourcing", "operations", "margin"},
    "risk": {"sourcing", "operations", "compliance"},
    "confidence": set(EVIDENCE_DIMENSION_LABELS),
}

PROFIT_FIELDS = (
    "sellingPriceJpy",
    "purchasePriceCny",
    "cnyToJpy",
    "inboundJpy",
    "dutyTaxJpy",
    "fbaFeeJpy",
    "referralRate",
    "adCostJpy",
    "returnReserveRate",
    "otherCostJpy",
)


def confidence_for(candidate: dict) -> dict:
    sources: dict[str, set[str]] = {}
    for item in candidate.get("evidence", []):
        dimension = item.get("dimension")
        if dimension and item.get("url") and item.get("observedAt"):
            sources.setdefault(dimension, set()).add(item["url"])
    return {
        dimension: {
            "count": len(urls),
            "status": "missing" if not urls else "single_source" if len(urls) == 1 else "cross_referenced",
        }
        for dimension, urls in sources.items()
    }


def calculate_scores(candidate: dict) -> dict:
    """Return separate score records and evidence status without aggregation."""
    evidence_by_id = {
        item.get("id"): item
        for item in candidate.get("evidence", [])
        if item.get("id") and item.get("url") and item.get("observedAt")
    }
    scores = candidate.get("scores", {})
    result = {}
    missing = []
    for key, metadata in SCORE_LABELS.items():
        record = scores.get(key, {})
        value = record.get("value")
        evidence_ids = [
            evidence_id
            for evidence_id in record.get("evidenceIds", [])
            if evidence_id in evidence_by_id
            and evidence_by_id[evidence_id].get("dimension") in SCORE_EVIDENCE_DIMENSIONS[key]
        ]
        complete = value is not None and bool(evidence_ids)
        if not complete:
            missing.append(key)
        result[key] = {
            **metadata,
            "value": value if complete else None,
            "rationale": record.get("rationale", ""),
            "evidenceIds": evidence_ids,
            "complete": complete,
        }
    return {"scores": result, "missingScores": missing, "complete": not missing}


def calculate_profit(assumptions: dict) -> dict:
    missing = [key for key in PROFIT_FIELDS if assumptions.get(key) in (None, "")]
    if missing:
        return {"complete": False, "missingFields": missing}

    values = {key: float(assumptions[key]) for key in PROFIT_FIELDS}
    if not all(math.isfinite(value) for value in values.values()):
        raise ValueError("利润假设必须是有效数字。")
    if any(value < 0 for value in values.values()):
        raise ValueError("成本、价格和费率不能小于 0。")
    if values["sellingPriceJpy"] <= 0 or values["cnyToJpy"] <= 0:
        raise ValueError("售价和汇率必须大于 0。")
    if values["referralRate"] < 0 or values["referralRate"] > 1:
        raise ValueError("佣金率必须在 0 到 1 之间。")
    if values["returnReserveRate"] < 0 or values["returnReserveRate"] > 1:
        raise ValueError("退货预留率必须在 0 到 1 之间。")

    price = values["sellingPriceJpy"]
    costs = {
        "采购成本": values["purchasePriceCny"] * values["cnyToJpy"],
        "头程": values["inboundJpy"],
        "关税及进口税费": values["dutyTaxJpy"],
        "FBA 费用": values["fbaFeeJpy"],
        "平台佣金": price * values["referralRate"],
        "广告": values["adCostJpy"],
        "退货预留": price * values["returnReserveRate"],
        "其他": values["otherCostJpy"],
    }
    contribution = price - sum(costs.values())
    remaining_for_ads = price - sum(value for key, value in costs.items() if key != "广告")
    return {
        "complete": True,
        "currency": "JPY",
        "sellingPrice": round(price, 2),
        "costs": {key: round(value, 2) for key, value in costs.items()},
        "contributionProfit": round(contribution, 2),
        "contributionMargin": round(contribution / price, 4),
        "breakEvenAdCost": round(max(0, remaining_for_ads), 2),
    }


def resource_fit(candidate: dict, profile: dict) -> str:
    """Return a separate advisory; deliberately excluded from score records."""
    budget = profile.get("budgetCny")
    purchase = candidate.get("assumptions", {}).get("purchasePriceCny")
    if budget in (None, "") or purchase in (None, ""):
        return "资料不足，无法匹配资源配置"
    try:
        if float(budget) <= 0 or float(purchase) < 0:
            return "资料不足，无法匹配资源配置"
    except (TypeError, ValueError):
        return "资料不足，无法匹配资源配置"
    if float(purchase) > float(budget):
        return "单件采购价已超过示例预算，请复核"
    return "预算初步可覆盖单件采购；MOQ、头程和周转仍待核实"
