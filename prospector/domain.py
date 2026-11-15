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

# Same-currency scenarios: a domestic price gap between two platforms of one
# market, where purchase and sale are both quoted in the same currency.
SAME_CURRENCY_FIELDS = (
    "sellingPrice",
    "purchasePrice",
    "currency",
    "referralRate",
    "adCost",
    "fulfillmentCost",
    "otherCost",
    "returnReserveRate",
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
    if assumptions.get("sellingPrice") not in (None, ""):
        return calculate_same_currency_profit(assumptions)
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


UNIFIED_PROFIT_FIELDS = (
    "sellingPrice",
    "sellingCurrency",
    "purchasePrice",
    "purchaseCurrency",
    "referralRate",
    "adCost",
    "fulfillmentCost",
    "returnReserveRate",
)

CROSS_CURRENCY_FIELDS = ("fxRate", "inboundCost", "dutyTaxCost")


BATCH_FIELDS = ("batchQty", "cashCycleDays")


def calculate_batch_plan(assumptions: dict, profit: dict) -> dict:
    """First-batch cash and turnover, derived from the profit scenario.

    No sales forecast is invented: the operator states how many units the
    first batch holds and over how many days the cash should come back.
    """
    missing = [key for key in BATCH_FIELDS if assumptions.get(key) in (None, "")]
    if missing:
        return {"complete": False, "missingFields": missing}
    try:
        quantity = float(assumptions["batchQty"])
        cycle_days = float(assumptions["cashCycleDays"])
    except (TypeError, ValueError) as exc:
        raise ValueError("首批数量与回本周期必须是有效数字。") from exc
    if quantity <= 0 or cycle_days <= 0:
        return {"complete": False, "missingFields": missing}
    purchase_in_selling_currency = profit.get("purchasePriceInSellingCurrency")
    if purchase_in_selling_currency is None:
        purchase_in_selling_currency = float(assumptions.get("purchasePrice", 0) or 0)
    cash_used = round(purchase_in_selling_currency * quantity, 2)
    units_per_day = round(quantity / cycle_days, 3)
    contribution = profit.get("contributionProfit")
    daily_contribution = round(units_per_day * contribution, 2) if contribution is not None else None
    return {
        "complete": True,
        "currency": profit.get("currency", ""),
        "batchQty": quantity,
        "cashCycleDays": cycle_days,
        "cashUsed": cash_used,
        "unitsPerDay": units_per_day,
        "dailyContribution": daily_contribution,
        "monthlyContribution": round(daily_contribution * 30, 2) if daily_contribution is not None else None,
        "note": "回本周期与首批数量由你设定；系统不预测销量速度。",
    }


def calculate_profit(assumptions: dict) -> dict:
    """Pick the profit model that matches the recorded assumptions.

    Two models exist: the unified one (any currency pair, used by the price
    gap scanner) and the original JPY-only model kept for candidates that
    were saved with the legacy field names.  Neither model ever fills a
    missing input with a default.
    """
    if assumptions.get("sellingPrice") not in (None, ""):
        return calculate_unified_profit(assumptions)
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
        "model": "cross_border_jpy",
        "currency": "JPY",
        "sellingPrice": round(price, 2),
        "costs": {key: round(value, 2) for key, value in costs.items()},
        "contributionProfit": round(contribution, 2),
        "contributionMargin": round(contribution / price, 4),
        "breakEvenAdCost": round(max(0, remaining_for_ads), 2),
    }


def calculate_unified_profit(assumptions: dict) -> dict:
    """Profit for one scenario, in the selling currency.

    Same-currency scenarios (two platforms of one market) only need the two
    prices and the fees.  Cross-currency scenarios additionally need the
    exchange rate and the inbound cost and duty, because no page carries
    those numbers.
    """
    missing = [key for key in UNIFIED_PROFIT_FIELDS if assumptions.get(key) in (None, "")]
    selling_currency = str(assumptions.get("sellingCurrency", "")).upper()
    purchase_currency = str(assumptions.get("purchaseCurrency", "")).upper()
    if selling_currency and selling_currency not in {"CNY", "JPY"}:
        missing.append("sellingCurrency")
    if purchase_currency and purchase_currency not in {"CNY", "JPY"}:
        missing.append("purchaseCurrency")
    if selling_currency and purchase_currency and selling_currency != purchase_currency:
        missing.extend(key for key in CROSS_CURRENCY_FIELDS if assumptions.get(key) in (None, ""))
    if missing:
        return {"complete": False, "missingFields": list(dict.fromkeys(missing)), "model": "unified"}

    price = _finite(assumptions["sellingPrice"], "售价")
    purchase = _finite(assumptions["purchasePrice"], "采购价")
    if price <= 0 or purchase < 0:
        return {"complete": False, "missingFields": ["sellingPrice", "purchasePrice"], "model": "unified"}

    referral_rate = _rate(assumptions["referralRate"])
    return_rate = _rate(assumptions["returnReserveRate"])
    purchase_in_selling_currency = purchase
    if selling_currency != purchase_currency:
        fx_rate = _finite(assumptions["fxRate"], "汇率")
        if fx_rate <= 0:
            return {"complete": False, "missingFields": ["fxRate"], "model": "unified"}
        purchase_in_selling_currency = purchase * fx_rate

    costs = {
        "采购成本": purchase_in_selling_currency,
        "平台佣金": price * referral_rate,
        "广告": _finite(assumptions.get("adCost", 0) or 0, "广告成本"),
        "履约/运费": _finite(assumptions.get("fulfillmentCost", 0) or 0, "履约费用"),
        "退货预留": price * return_rate,
        "其他成本": _finite(assumptions.get("otherCost", 0) or 0, "其他成本"),
    }
    if selling_currency != purchase_currency:
        costs["头程与入仓"] = _finite(assumptions.get("inboundCost", 0) or 0, "头程费用")
        costs["关税及进口税费"] = _finite(assumptions.get("dutyTaxCost", 0) or 0, "关税及进口税费")
    contribution = price - sum(costs.values())
    remaining_for_ads = price - sum(value for key, value in costs.items() if key != "广告")
    return {
        "complete": True,
        "model": "unified",
        "currency": selling_currency,
        "sellingPrice": round(price, 2),
        "purchasePriceInSellingCurrency": round(purchase_in_selling_currency, 2),
        "costs": {key: round(value, 2) for key, value in costs.items()},
        "contributionProfit": round(contribution, 2),
        "contributionMargin": round(contribution / price, 4),
        "breakEvenAdCost": round(max(0, remaining_for_ads), 2),
    }


def _finite(value, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label}必须是有效数字。") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label}必须是有效数字。")
    return number


def _rate(value) -> float:
    number = _finite(value, "费率")
    if number < 0 or number > 1:
        raise ValueError("费率必须在 0 到 1 之间。")
    return number


PILOT_FIELDS = ("windowDays", "spend", "clicks", "orders", "refunds", "revenue", "netProfit")

DEFAULT_PILOT_THRESHOLDS = {
    "minOrders": 30,
    "maxRefundRate": 0.05,
    "minNetMargin": 0.1,
}


def evaluate_pilot(pilot: dict, thresholds: dict | None = None) -> dict:
    """Judge a real small-batch test against the operator's thresholds.

    The numbers come from the operator's own order and advertising data; the
    verdict is a comparison against thresholds, never a forecast.
    """
    limits = {**DEFAULT_PILOT_THRESHOLDS, **(thresholds or {})}
    missing = [key for key in PILOT_FIELDS if (pilot or {}).get(key) in (None, "")]
    if missing:
        return {"complete": False, "missingFields": missing, "verdict": "待补数据"}

    def number(key: str) -> float:
        try:
            return float(pilot[key])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key} 必须是有效数字。") from exc

    window_days = number("windowDays")
    spend = number("spend")
    clicks = number("clicks")
    orders = number("orders")
    refunds = number("refunds")
    revenue = number("revenue")
    net_profit = number("netProfit")
    if window_days <= 0 or clicks < 0 or orders < 0 or refunds < 0:
        return {"complete": False, "missingFields": ["windowDays", "clicks", "orders", "refunds"], "verdict": "待补数据"}

    conversion_rate = orders / clicks if clicks else None
    refund_rate = refunds / orders if orders else None
    net_margin = net_profit / revenue if revenue else None
    ad_cost_per_order = spend / orders if orders else None

    findings = []
    if orders < float(limits.get("minOrders", 0)):
        findings.append(f"订单量 {orders:g} 低于门槛 {limits.get('minOrders')}，样本不足，暂不判断。")
    if refund_rate is not None and refund_rate > float(limits.get("maxRefundRate", 1)):
        findings.append(f"退款率 {refund_rate * 100:.1f}% 高于门槛 {float(limits.get('maxRefundRate', 0)) * 100:.1f}%，先解决质量问题。")
    if net_margin is not None and net_margin < float(limits.get("minNetMargin", 0)):
        findings.append(f"净利率 {net_margin * 100:.1f}% 低于门槛 {float(limits.get('minNetMargin', 0)) * 100:.1f}%，成本结构不成立。")

    if orders < float(limits.get("minOrders", 0)):
        verdict = "待补数据"
        recommendation = "继续测"
    elif refund_rate is not None and refund_rate > float(limits.get("maxRefundRate", 1)):
        verdict = "质量不达标"
        recommendation = "停止"
    elif net_margin is not None and net_margin < float(limits.get("minNetMargin", 0)):
        verdict = "成本不成立"
        recommendation = "调整"
    else:
        verdict = "达标"
        recommendation = "继续"

    return {
        "complete": True,
        "verdict": verdict,
        "recommendation": recommendation,
        "findings": findings,
        "metrics": {
            "windowDays": window_days,
            "spend": spend,
            "clicks": clicks,
            "orders": orders,
            "refunds": refunds,
            "revenue": revenue,
            "netProfit": net_profit,
            "conversionRate": round(conversion_rate, 4) if conversion_rate is not None else None,
            "refundRate": round(refund_rate, 4) if refund_rate is not None else None,
            "netMargin": round(net_margin, 4) if net_margin is not None else None,
            "adCostPerOrder": round(ad_cost_per_order, 2) if ad_cost_per_order is not None else None,
        },
        "thresholds": limits,
        "note": "结论只基于你录入的真实订单与投放数据；门槛由你设定。",
    }


def resource_fit(candidate: dict, profile: dict) -> str:
    """Return a separate advisory; deliberately excluded from score records."""
    budget = profile.get("budgetCny")
    assumptions = candidate.get("assumptions", {})
    purchase = assumptions.get("purchasePriceCny")
    if budget in (None, "") or purchase in (None, ""):
        return "资料不足，无法匹配资源配置"
    try:
        if float(budget) <= 0 or float(purchase) < 0:
            return "资料不足，无法匹配资源配置"
    except (TypeError, ValueError):
        return "资料不足，无法匹配资源配置"
    if float(purchase) > float(budget):
        return "单件采购价已超过示例预算，请复核"

    # A first batch needs cash, not just one unit.
    batch_qty = assumptions.get("batchQty")
    purchase_in_selling = assumptions.get("purchasePrice")
    if batch_qty in (None, "") or purchase_in_selling in (None, ""):
        return "预算初步可覆盖单件采购；填入首批数量后可核对资金占用，MOQ、头程和周转仍待核实"
    try:
        quantity = float(batch_qty)
        unit_price = float(purchase_in_selling)
    except (TypeError, ValueError):
        return "预算初步可覆盖单件采购；填入首批数量后可核对资金占用，MOQ、头程和周转仍待核实"
    if quantity <= 0 or unit_price < 0:
        return "预算初步可覆盖单件采购；填入首批数量后可核对资金占用，MOQ、头程和周转仍待核实"
    cash_needed = quantity * unit_price
    if cash_needed > float(budget):
        return f"首批 {quantity:g} 件约需 {cash_needed:,.0f}（采购币种），已超过可投入预算 {float(budget):,.0f}，请下调批量或调整预算"
    return f"预算可覆盖首批 {quantity:g} 件约 {cash_needed:,.0f}（采购币种）；MOQ、头程和周转仍待核实"
