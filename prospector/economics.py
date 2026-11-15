"""Landed-cost estimate for a cross-market price-gap candidate."""

from __future__ import annotations

import math
from datetime import date


def estimate_cross_market_profit(source_listing: dict, target_listing: dict, assumptions: dict) -> dict:
    """Calculate an estimate from recorded prices and explicit user assumptions.

    This function intentionally does not look up exchange rates, tariffs,
    marketplace fees, or shipping prices. The caller must provide those values.
    """
    required = (
        "sourceCurrencyToJpy",
        "fxObservedAt",
        "inboundJpy",
        "dutyTaxJpy",
        "fulfillmentJpy",
        "referralRate",
        "adCostJpy",
        "returnReserveRate",
        "otherCostJpy",
    )
    if any(assumptions.get(key) in (None, "") for key in required):
        raise ValueError("价差估算需要显式填写汇率日期、头程、税费、履约、佣金、广告、退货预留和其他成本。")
    try:
        date.fromisoformat(str(assumptions["fxObservedAt"])[:10])
    except ValueError as exc:
        raise ValueError("汇率观察日期必须使用 YYYY-MM-DD 格式。") from exc

    source_price = _number(source_listing.get("price"), "采购平台价格", minimum=0)
    target_price = _number(target_listing.get("price"), "销售平台价格", minimum=0)
    fx_to_jpy = _number(assumptions.get("sourceCurrencyToJpy"), "换算为日元的汇率", minimum=0)
    if source_price <= 0 or target_price <= 0 or fx_to_jpy <= 0:
        raise ValueError("采购价、销售价和汇率必须大于 0。")

    inbound = _number(assumptions.get("inboundJpy"), "头程费用", minimum=0)
    duty = _number(assumptions.get("dutyTaxJpy"), "关税及进口税费", minimum=0)
    fulfillment = _number(assumptions.get("fulfillmentJpy"), "履约费用", minimum=0)
    referral_rate = _number(assumptions.get("referralRate"), "平台佣金率", minimum=0)
    advertising = _number(assumptions.get("adCostJpy"), "广告费用", minimum=0)
    return_rate = _number(assumptions.get("returnReserveRate"), "退货预留率", minimum=0)
    other = _number(assumptions.get("otherCostJpy"), "其他费用", minimum=0)
    if referral_rate > 1 or return_rate > 1:
        raise ValueError("平台佣金率和退货预留率必须在 0 到 1 之间。")

    source_cost_jpy = source_price * fx_to_jpy
    gross_gap = target_price - source_cost_jpy
    fee = target_price * referral_rate
    returns = target_price * return_rate
    costs = {
        "采购成本": source_cost_jpy,
        "头程": inbound,
        "关税及进口税费": duty,
        "履约费用": fulfillment,
        "平台佣金": fee,
        "广告": advertising,
        "退货预留": returns,
        "其他": other,
    }
    contribution = target_price - sum(costs.values())
    return {
        "currency": "JPY",
        "sourceCurrency": str(source_listing.get("currency", "")),
        "targetCurrency": str(target_listing.get("currency", "")),
        "sourceCostJpy": round(source_cost_jpy, 2),
        "grossPriceGapJpy": round(gross_gap, 2),
        "grossGapVsSource": round(gross_gap / source_cost_jpy, 4),
        "estimatedCostsJpy": {key: round(value, 2) for key, value in costs.items()},
        "estimatedContributionJpy": round(contribution, 2),
        "estimatedContributionMargin": round(contribution / target_price, 4),
        "assumptionQuality": "user_supplied_estimate",
        "fxObservedAt": assumptions.get("fxObservedAt") or None,
    }


def _number(value, label: str, minimum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label}必须是有效数字。") from exc
    if not math.isfinite(number) or number < minimum:
        raise ValueError(f"{label}不能小于 {minimum:g}。")
    return number
