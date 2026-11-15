"""Unit tests for the same-spec price gap scanner.

No network and no model calls: the search backend is replaced by a fixture so
the parsing, grouping and gap rules can be checked deterministically.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import prospector.agent_pricegap as pricegap  # noqa: E402


FIXTURE = [
    {
        "ref": "S1",
        "platformId": "taobao",
        "title": "OPPO K13s 12GB+256GB 官方旗舰店",
        "url": "https://item.taobao.com/item.htm?id=1",
        "snippet": "限时到手价 ¥1899 元，12GB+256GB 星辰黑 全国联保",
    },
    {
        "ref": "S2",
        "platformId": "jd",
        "title": "OPPO K13s 12+256 京东自营",
        "url": "https://item.jd.com/2.html",
        "snippet": "京东价 2099.00元，12GB+256GB 官方标配",
    },
    {
        "ref": "S3",
        "platformId": "pdd",
        "title": "OPPO K13s 手机 8+128",
        "url": "https://mobile.yangkeduo.com/3.html",
        "snippet": "拼单价 ￥1299 8GB+128GB",
    },
    {
        "ref": "S4",
        "platformId": "xianyu",
        "title": "K13s 12GB+256GB 二手",
        "url": "https://www.goofish.com/5",
        "snippet": "￥1650 12GB+256GB 个人闲置",
    },
    {
        "ref": "S5",
        "platformId": "taobao",
        "title": "OPPO K13s 评测文章",
        "url": "https://www.zhihu.com/4",
        "snippet": "售价约 1999 元，但摘要没有写容量",
    },
]


class PriceGapScanTest(unittest.TestCase):
    def setUp(self) -> None:
        self.original = pricegap.search_many
        pricegap.search_many = lambda plan: list(FIXTURE)

    def tearDown(self) -> None:
        pricegap.search_many = self.original

    def test_only_same_configuration_rows_are_compared(self) -> None:
        result = pricegap.run_price_gap_scan("OPPO K13s", "中国", "中国")
        groups = {group["config"]: group for group in result["groups"]}
        self.assertIn("12G+256G", groups)
        self.assertTrue(groups["12G+256G"]["comparable"])
        # A different memory/storage combination never joins that group.
        self.assertIn("8G+128G", groups)
        self.assertFalse(groups["8G+128G"]["comparable"])
        self.assertFalse(groups["未识别配置"]["comparable"])

    def test_gap_is_computed_against_the_lowest_same_spec_price(self) -> None:
        result = pricegap.run_price_gap_scan("OPPO K13s", "中国", "中国")
        group = next(item for item in result["groups"] if item["config"] == "12G+256G")
        self.assertEqual(group["lowestPlatform"], "淘宝 / 天猫")
        self.assertEqual(group["highestPlatform"], "京东")
        self.assertEqual(group["grossGap"], 200.0)
        jd = next(row for row in group["rows"] if row["platformId"] == "jd")
        self.assertEqual(jd["gapToLowest"], 200.0)

    def test_second_hand_prices_are_background_only(self) -> None:
        result = pricegap.run_price_gap_scan("OPPO K13s", "中国", "中国")
        group = next(item for item in result["groups"] if item["config"] == "12G+256G")
        xianyu = next(row for row in group["rows"] if row["platformId"] == "xianyu")
        self.assertTrue(xianyu["secondHand"])
        self.assertEqual(xianyu["gapToLowest"], 0.0)
        self.assertNotEqual(group["lowestPlatform"], "闲鱼（个人二手）")

    def test_opportunity_uses_cheapest_and_dearest_first_hand_platforms(self) -> None:
        result = pricegap.run_price_gap_scan("OPPO K13s", "中国", "中国")
        opportunities = result["opportunities"]
        self.assertEqual(len(opportunities), 1)
        item = opportunities[0]
        self.assertEqual(item["sourcePlatform"], "淘宝 / 天猫")
        self.assertEqual(item["targetPlatform"], "京东")
        self.assertEqual(item["grossGap"], 200.0)

    def test_rows_without_the_model_are_dropped(self) -> None:
        result = pricegap.run_price_gap_scan("OPPO K13s", "中国", "中国")
        titles = [row["title"] for group in result["groups"] for row in group["rows"]]
        self.assertTrue(all("OPPO" in title or "K13s" in title for title in titles))

    def test_cross_border_market_reports_without_computing_fx(self) -> None:
        result = pricegap.run_price_gap_scan("OPPO K13s", "中国", "日本")
        self.assertIsNotNone(result["crossBorder"])
        self.assertIn("汇率", result["crossBorder"]["note"])

    def test_cross_market_pairs_keep_both_currencies(self) -> None:
        jp_rows = [
            {
                "ref": "J1",
                "platformId": "amazon_jp",
                "title": "OPPO K13s 12GB+256GB",
                "url": "https://www.amazon.co.jp/dp/B0TEST",
                "snippet": "価格 32,800円 12GB+256GB",
            },
        ]
        pricegap.search_many = lambda plan: list(FIXTURE) + list(jp_rows)
        result = pricegap.run_price_gap_scan("OPPO K13s", "中国", "日本")
        cross = result["crossBorder"]
        self.assertEqual(cross["sourceCountry"], "日本")
        self.assertTrue(cross["rows"])
        self.assertTrue(cross["pairs"])
        pair = cross["pairs"][0]
        self.assertEqual(pair["sourceCurrency"], "JPY")
        self.assertEqual(pair["targetCurrency"], "CNY")
        candidates = pricegap.build_price_gap_candidates(
            "OPPO K13s", result, {"market": "中国", "sourcingCountry": "日本"}
        )
        cross_candidate = next(item for item in candidates if "跨市场价差" in item["name"])
        self.assertEqual(cross_candidate["assumptions"]["sellingCurrency"], "CNY")
        self.assertEqual(cross_candidate["assumptions"]["purchaseCurrency"], "JPY")

    def test_sellers_are_clustered_inside_a_configuration(self) -> None:
        result = pricegap.run_price_gap_scan("OPPO K13s", "中国", "中国")
        group = next(item for item in result["groups"] if item["config"] == "12G+256G")
        sellers = group["sellers"]
        self.assertTrue(sellers)
        self.assertEqual(sellers[0]["priceLow"], min(row["priceLow"] for row in sellers))
        self.assertTrue(all("." in seller["seller"] for seller in sellers))

    def test_price_history_summary_flags_moves_over_the_threshold(self) -> None:
        history = [
            {"platform": "京东", "config": "12G+256G", "price": 2099, "currency": "CNY", "observedAt": "2026-11-01"},
            {"platform": "京东", "config": "12G+256G", "price": 1799, "currency": "CNY", "observedAt": "2026-11-08"},
        ]
        summary = pricegap.summarize_price_history(history)
        self.assertEqual(len(summary["series"]), 1)
        self.assertEqual(summary["series"][0]["observations"], 2)
        self.assertEqual(len(summary["alerts"]), 1)
        alert = summary["alerts"][0]
        self.assertEqual(alert["direction"], "降价")
        self.assertAlmostEqual(alert["changePct"], -0.1429, places=3)

    def test_promo_pages_are_marked_and_a_durable_gap_is_computed(self) -> None:
        promo_rows = [
            {
                "ref": "S1",
                "platformId": "taobao",
                "title": "OPPO K13s 12GB+256GB 官方旗舰店",
                "url": "https://item.taobao.com/item.htm?id=1",
                "snippet": "百亿补贴 到手价 ¥1899 元，12GB+256GB",
            },
            {
                "ref": "S2",
                "platformId": "jd",
                "title": "OPPO K13s 12+256 京东自营",
                "url": "https://item.jd.com/2.html",
                "snippet": "京东价 2099.00元，12GB+256GB 官方标配",
            },
        ]
        pricegap.search_many = lambda plan: list(promo_rows)
        result = pricegap.run_price_gap_scan("OPPO K13s", "中国", "中国")
        group = next(item for item in result["groups"] if item["config"] == "12G+256G")
        taobao = next(row for row in group["rows"] if row["platformId"] == "taobao")
        self.assertTrue(taobao["promoSignals"])
        # Only one platform has a non-promotional price, so no durable gap can be claimed.
        self.assertIsNone(group["durableGap"])
        self.assertTrue(any("促销" in caveat for caveat in group["caveats"]))

    def test_store_pairs_name_the_cheapest_and_dearest_shop(self) -> None:
        result = pricegap.run_price_gap_scan("OPPO K13s", "中国", "中国")
        group = next(item for item in result["groups"] if item["config"] == "12G+256G")
        pairs = group["storePairs"]
        self.assertTrue(pairs)
        pair = pairs[0]
        self.assertLessEqual(pair["sourcePrice"], pair["targetPrice"])
        self.assertEqual(pair["gap"], round(pair["targetPrice"] - pair["sourcePrice"], 2))
        self.assertTrue(pair["sourceSeller"])

    def test_gap_history_compares_two_platforms_over_time(self) -> None:
        history = [
            {"platform": "京东", "config": "12G+256G", "price": 2099, "observedAt": "2026-11-01"},
            {"platform": "淘宝 / 天猫", "config": "12G+256G", "price": 1899, "observedAt": "2026-11-01"},
            {"platform": "京东", "config": "12G+256G", "price": 1999, "observedAt": "2026-11-08"},
            {"platform": "淘宝 / 天猫", "config": "12G+256G", "price": 1899, "observedAt": "2026-11-08"},
        ]
        summary = pricegap.gap_history(history, "12G+256G", "淘宝 / 天猫", "京东")
        self.assertEqual(summary["observations"], 2)
        self.assertEqual(summary["series"][0]["gap"], 200.0)
        self.assertEqual(summary["series"][1]["gap"], 100.0)
        self.assertEqual(summary["medianGap"], 150.0)

    def test_platform_profit_needs_cost_assumptions(self) -> None:
        result = pricegap.run_price_gap_scan("OPPO K13s", "中国", "中国")
        without = pricegap.compute_platform_profit(result["groups"], {}, "CNY")
        self.assertFalse(without["complete"])
        self.assertIn("referralRate", without["missingFields"])
        self.assertTrue(without["rows"])
        self.assertIsNone(without["rows"][0]["contributionProfit"])

        with_costs = pricegap.compute_platform_profit(
            result["groups"],
            {"referralRate": 0.05, "adCost": 20, "fulfillmentCost": 15, "returnReserveRate": 0.02},
            "CNY",
        )
        self.assertTrue(with_costs["complete"])
        row = next(item for item in with_costs["rows"] if item["platform"] == "京东")
        expected = 2099 - 1899 - 2099 * 0.05 - 20 - 15 - 2099 * 0.02
        self.assertAlmostEqual(row["contributionProfit"], round(expected, 2), places=1)
        self.assertEqual(row["purchasePlatform"], "淘宝 / 天猫")

    def test_platform_fees_can_differ_per_platform(self) -> None:
        result = pricegap.run_price_gap_scan("OPPO K13s", "中国", "中国")
        assumptions = {
            "referralRate": 0.05,
            "adCost": 20,
            "fulfillmentCost": 15,
            "returnReserveRate": 0.02,
            "byPlatform": {"jd": {"referralRate": 0.10}},
        }
        computed = pricegap.compute_platform_profit(result["groups"], assumptions, "CNY")
        jd = next(item for item in computed["rows"] if item["platformId"] == "jd")
        default_only = pricegap.compute_platform_profit(
            result["groups"],
            {"referralRate": 0.05, "adCost": 20, "fulfillmentCost": 15, "returnReserveRate": 0.02},
            "CNY",
        )
        jd_default = next(item for item in default_only["rows"] if item["platformId"] == "jd")
        # A higher commission for this platform lowers its contribution.
        self.assertLess(jd["contributionProfit"], jd_default["contributionProfit"])
        expected_jd = 2099 - 1899 - 2099 * 0.10 - 20 - 15 - 2099 * 0.02
        self.assertAlmostEqual(jd["contributionProfit"], round(expected_jd, 2), places=1)

    def test_purchase_checklist_keeps_unknowns_explicit(self) -> None:
        result = pricegap.run_price_gap_scan("OPPO K13s", "中国", "中国")
        candidates = pricegap.build_price_gap_candidates(
            "OPPO K13s", result, {"market": "中国", "sourcingCountry": "中国"}
        )
        checklist = candidates[0]["purchaseChecklist"]
        self.assertTrue(checklist)
        items = " ".join(item["item"] for item in checklist)
        self.assertIn("起订量", items)
        self.assertIn("阶梯价", items)
        # Every unknown must be marked, never silently assumed.
        for item in checklist:
            self.assertIn(item["status"], {"已有线索", "待确认", "待执行"})

    def test_checklist_ticks_advance_the_workflow(self) -> None:
        from prospector.agent_pricegap import advance_candidate_workflow, merge_purchase_checklist

        result = pricegap.run_price_gap_scan("OPPO K13s", "中国", "中国")
        candidate = pricegap.build_price_gap_candidates(
            "OPPO K13s", result, {"market": "中国", "sourcingCountry": "中国"}
        )[0]
        stages = {stage["id"]: stage for stage in candidate["workflowStages"]}
        self.assertEqual(stages["sourcing"]["status"], "partial")

        updates = [{"id": item["id"], "done": True} for item in candidate["purchaseChecklist"]]
        candidate["purchaseChecklist"] = merge_purchase_checklist(candidate["purchaseChecklist"], updates)
        advance_candidate_workflow(candidate)
        stages = {stage["id"]: stage for stage in candidate["workflowStages"]}
        self.assertEqual(stages["sourcing"]["status"], "completed")
        # Costs are still missing, so the economics stage stays open.
        self.assertEqual(stages["economics"]["status"], "partial")

    def test_checklist_merging_keeps_server_wording(self) -> None:
        from prospector.agent_pricegap import merge_purchase_checklist

        existing = [{"id": "moq", "item": "确认起订量与阶梯价", "status": "待确认"}]
        merged = merge_purchase_checklist(existing, [{"id": "moq", "done": True, "item": "改成别的"}])
        self.assertTrue(merged[0]["done"])
        self.assertEqual(merged[0]["item"], "确认起订量与阶梯价")

    def test_three_purchase_price_scenarios_are_reported(self) -> None:
        result = pricegap.run_price_gap_scan("OPPO K13s", "中国", "中国")
        computed = pricegap.compute_platform_profit(
            result["groups"],
            {"referralRate": 0.05, "adCost": 20, "fulfillmentCost": 15, "returnReserveRate": 0.02},
            "CNY",
        )
        row = next(item for item in computed["rows"] if item["platformId"] == "jd")
        keys = [scenario["key"] for scenario in row["scenarios"]]
        self.assertIn("lowest_visible", keys)
        self.assertIn("median", keys)
        # A higher purchase basis never yields a higher profit.
        by_price = sorted(row["scenarios"], key=lambda item: item["purchasePrice"])
        profits = [scenario["contributionProfit"] for scenario in by_price]
        self.assertEqual(profits, sorted(profits, reverse=True))

    def test_batch_plan_needs_quantity_and_cycle(self) -> None:
        from prospector.domain import calculate_batch_plan, calculate_profit

        assumptions = {
            "sellingPrice": 2099,
            "sellingCurrency": "CNY",
            "purchasePrice": 1899,
            "purchaseCurrency": "CNY",
            "referralRate": 0.05,
            "adCost": 20,
            "fulfillmentCost": 15,
            "returnReserveRate": 0.02,
        }
        profit = calculate_profit(assumptions)
        incomplete = calculate_batch_plan(assumptions, profit)
        self.assertFalse(incomplete["complete"])
        plan = calculate_batch_plan({**assumptions, "batchQty": 60, "cashCycleDays": 30}, profit)
        self.assertTrue(plan["complete"])
        self.assertEqual(plan["cashUsed"], 113940.0)
        self.assertEqual(plan["unitsPerDay"], 2.0)
        self.assertAlmostEqual(plan["dailyContribution"], round(2 * profit["contributionProfit"], 2), places=2)

    def test_rejected_reasons_become_avoid_constraints(self) -> None:
        from prospector.agent_discovery import _rejected_constraints

        data = {
            "candidates": [
                {"name": "折叠晾衣架", "humanReview": {"decision": "reject", "reason": "同质化严重，没有价格优势"}},
                {"name": "宠物饮水器", "humanReview": {"decision": "continue", "reason": "补来源"}},
                {"name": "无理由候选", "humanReview": {"decision": "reject", "reason": ""}},
            ]
        }
        constraints = _rejected_constraints(data)
        self.assertEqual(len(constraints), 1)
        self.assertIn("折叠晾衣架", constraints[0])
        self.assertIn("同质化严重", constraints[0])

    def test_budget_check_uses_the_first_batch(self) -> None:
        from prospector.domain import resource_fit

        candidate = {
            "assumptions": {
                "purchasePriceCny": 1899,
                "purchasePrice": 1899,
                "batchQty": 100,
            }
        }
        profile = {"budgetCny": 100000}
        note = resource_fit(candidate, profile)
        self.assertIn("首批", note)
        self.assertIn("超过可投入预算", note)

        comfortable = resource_fit({**candidate, "assumptions": {**candidate["assumptions"], "batchQty": 10}}, profile)
        self.assertIn("预算可覆盖首批", comfortable)

    def test_pilot_verdict_follows_the_operator_thresholds(self) -> None:
        from prospector.domain import evaluate_pilot

        thresholds = {"minOrders": 30, "maxRefundRate": 0.05, "minNetMargin": 0.1}
        incomplete = evaluate_pilot({"orders": 10}, thresholds)
        self.assertFalse(incomplete["complete"])
        self.assertEqual(incomplete["verdict"], "待补数据")

        passing = evaluate_pilot({
            "windowDays": 14, "spend": 3000, "clicks": 3000, "orders": 40,
            "refunds": 1, "revenue": 12000, "netProfit": 2000,
        }, thresholds)
        self.assertTrue(passing["complete"])
        self.assertEqual(passing["verdict"], "达标")
        self.assertEqual(passing["recommendation"], "继续")

        refund_heavy = evaluate_pilot({
            "windowDays": 14, "spend": 3000, "clicks": 3000, "orders": 40,
            "refunds": 6, "revenue": 12000, "netProfit": 2000,
        }, thresholds)
        self.assertEqual(refund_heavy["verdict"], "质量不达标")
        self.assertEqual(refund_heavy["recommendation"], "停止")

        thin = evaluate_pilot({
            "windowDays": 14, "spend": 300, "clicks": 300, "orders": 5,
            "refunds": 0, "revenue": 1500, "netProfit": 100,
        }, thresholds)
        self.assertEqual(thin["verdict"], "待补数据")
        self.assertEqual(thin["recommendation"], "继续测")

    def test_pilot_data_moves_the_workflow_stages(self) -> None:
        from prospector.agent_pricegap import advance_candidate_workflow

        candidate = {
            "assumptions": {},
            "purchaseChecklist": [],
            "pilot": {
                "windowDays": 14, "spend": 3000, "clicks": 3000, "orders": 40,
                "refunds": 1, "revenue": 12000, "netProfit": 2000,
            },
            "workflowStages": [
                {"id": "pilot", "number": 5, "title": "小批量测试", "status": "partial", "note": ""},
                {"id": "scale", "number": 6, "title": "扩大经营", "status": "blocked", "note": ""},
            ],
        }
        advance_candidate_workflow(candidate, {"minOrders": 30, "maxRefundRate": 0.05, "minNetMargin": 0.1})
        stages = {stage["id"]: stage for stage in candidate["workflowStages"]}
        self.assertEqual(stages["pilot"]["status"], "completed")
        self.assertEqual(stages["scale"]["status"], "pending")

    def test_scale_plan_needs_a_passing_pilot(self) -> None:
        from prospector.domain import build_scale_plan

        blocked = build_scale_plan({}, {"complete": True, "verdict": "质量不达标"}, {"budgetCny": 100000}, {})
        self.assertFalse(blocked["complete"])
        self.assertEqual(blocked["tiers"], [])

        candidate = {
            "assumptions": {"purchasePrice": 1899, "sellingCurrency": "CNY"},
            "profit": {"purchasePriceInSellingCurrency": 1899, "currency": "CNY"},
        }
        pilot = {
            "complete": True,
            "verdict": "达标",
            "metrics": {"windowDays": 14, "orders": 40, "refundRate": 0.03},
        }
        plan = build_scale_plan(candidate, pilot, {"budgetCny": 100000}, {})
        self.assertTrue(plan["complete"])
        self.assertAlmostEqual(plan["dailyUnits"], round(40 / 14, 2), places=2)
        labels = [tier["label"] for tier in plan["tiers"]]
        self.assertEqual(labels, ["备货 30 天", "备货 45 天", "备货 60 天"])
        biggest = plan["tiers"][-1]
        # 60 days of cover plus 3% safety stock.
        self.assertEqual(biggest["safetyUnits"], round(biggest["salesUnits"] * 0.03))
        self.assertEqual(biggest["totalUnits"], biggest["salesUnits"] + biggest["safetyUnits"])
        self.assertFalse(biggest["withinBudget"])
        self.assertTrue(any("预算" in risk for risk in plan["risks"]))

    def test_timeline_records_stage_changes(self) -> None:
        from prospector.agent_pricegap import advance_candidate_workflow

        candidate = {
            "assumptions": {},
            "purchaseChecklist": [],
            "pilot": {
                "windowDays": 14, "spend": 3000, "clicks": 3000, "orders": 40,
                "refunds": 1, "revenue": 12000, "netProfit": 2000,
            },
            "workflowStages": [
                {"id": "pilot", "number": 5, "title": "小批量测试", "status": "partial", "note": ""},
            ],
        }
        advance_candidate_workflow(candidate, {"minOrders": 30, "maxRefundRate": 0.05, "minNetMargin": 0.1})
        self.assertTrue(candidate["timeline"])
        entry = candidate["timeline"][-1]
        self.assertEqual(entry["stageId"], "pilot")
        self.assertEqual(entry["to"], "completed")
        self.assertTrue(entry["at"])

    def test_calibration_suggests_measured_values(self) -> None:
        from prospector.domain import calibrate_assumptions

        pilot = {
            "complete": True,
            "verdict": "达标",
            "metrics": {"adCostPerOrder": 25.0, "refundRate": 0.04, "netMargin": 0.12},
        }
        suggestions = calibrate_assumptions({"adCost": 10, "returnReserveRate": 0.02}, pilot)
        fields = {item["field"]: item for item in suggestions}
        self.assertEqual(fields["adCost"]["suggested"], 25.0)
        self.assertEqual(fields["returnReserveRate"]["suggested"], 0.04)
        self.assertIn("measuredNetMargin", fields)
        self.assertEqual(calibrate_assumptions({}, {"complete": False}), [])

    def test_supplier_comparison_ranks_only_what_was_answered(self) -> None:
        from prospector.domain import build_supplier_comparison

        rfq = {
            "targets": [
                {"platform": "淘宝 / 天猫", "seller": "shop-a.taobao.com"},
                {"platform": "京东", "seller": "mall.jd.com"},
            ],
            "quotes": [
                {"platform": "淘宝 / 天猫", "seller": "shop-a.taobao.com", "unitPrice": 1820, "moq": 100, "leadTimeDays": 10, "currency": "CNY", "invoice": "可开票"},
                {"platform": "京东", "seller": "mall.jd.com", "unitPrice": 1950, "moq": 20, "leadTimeDays": 3, "currency": "CNY"},
            ],
        }
        comparison = build_supplier_comparison(rfq, {"budgetCny": 50000})
        rows = {row["seller"]: row for row in comparison["rows"]}
        self.assertTrue(rows["shop-a.taobao.com"]["isCheapest"])
        self.assertTrue(rows["mall.jd.com"]["isSmallestMoq"])
        self.assertTrue(rows["mall.jd.com"]["isFastest"])
        self.assertEqual(rows["shop-a.taobao.com"]["cashNeeded"], 182000.0)
        self.assertFalse(rows["shop-a.taobao.com"]["withinBudget"])
        self.assertEqual(comparison["replyRate"], 1.0)
        self.assertEqual(comparison["missing"], [])

    def test_landed_cost_spreads_freight_duty_and_defects(self) -> None:
        from prospector.domain import build_landed_cost

        incomplete = build_landed_cost({"quantity": 60})
        self.assertFalse(incomplete["complete"])

        landed = build_landed_cost({
            "quantity": 60,
            "unitPrice": 1820,
            "purchaseCurrency": "CNY",
            "shippingTotal": 600,
            "dutyTotal": 300,
            "insuranceTotal": 60,
            "defectRate": 0.02,
        })
        self.assertTrue(landed["complete"])
        self.assertEqual(landed["perUnitShipping"], 10.0)
        self.assertEqual(landed["perUnitDuty"], 5.0)
        self.assertEqual(landed["perUnitInsurance"], 1.0)
        self.assertEqual(landed["defectCost"], 36.4)
        # landed = (goods incl. defect reserve + freight + duty + insurance) / units
        self.assertAlmostEqual(landed["landedUnitCost"], (1820 * 60 * 1.02 + 600 + 300 + 60) / 60, places=2)
        self.assertEqual(landed["totalGoods"], 109200.0)

    def test_inspection_issues_reduce_sellable_units_and_cost(self) -> None:
        from prospector.domain import build_landed_cost

        landed = build_landed_cost({
            "quantity": 60,
            "unitPrice": 1820,
            "purchaseCurrency": "CNY",
            "shippingTotal": 600,
            "dutyTotal": 300,
            "insuranceTotal": 60,
            "defectRate": 0.0,
            "qcIssues": [
                {"type": "短装", "quantity": 4, "refundAmount": 7280, "status": "已赔付"},
                {"type": "破损", "quantity": 2, "refundAmount": 0, "status": "待索赔"},
            ],
        })
        self.assertEqual(landed["lostUnits"], 6)
        self.assertEqual(landed["usableUnits"], 54)
        self.assertEqual(landed["refunds"], 7280.0)
        self.assertEqual(len(landed["openIssues"]), 1)
        expected = (1820 * 60 + 600 + 300 + 60 - 7280) / 54
        self.assertAlmostEqual(landed["landedUnitCost"], round(expected, 2), places=2)

    def test_batch_plan_uses_the_real_landed_cost(self) -> None:
        from prospector.domain import calculate_batch_plan

        assumptions = {
            "sellingPrice": 2099,
            "sellingCurrency": "CNY",
            "purchasePrice": 1820,
            "purchaseCurrency": "CNY",
            "referralRate": 0.05,
            "adCost": 20,
            "fulfillmentCost": 15,
            "returnReserveRate": 0.02,
            "batchQty": 60,
            "cashCycleDays": 30,
        }
        profit = {"complete": True, "currency": "CNY", "contributionProfit": 100.0, "purchasePriceInSellingCurrency": 1820}
        landed = {"complete": True, "landedUnitCost": 1900.0}
        plan = calculate_batch_plan(assumptions, profit, landed)
        self.assertEqual(plan["cashUsed"], 114000.0)

    def test_profit_reconciliation_splits_the_difference(self) -> None:
        from prospector.domain import reconcile_profit

        candidate = {
            "assumptions": {
                "sellingPrice": 2099,
                "purchasePrice": 1899,
                "adCost": 20,
                "returnReserveRate": 0.02,
            },
            "profit": {"contributionProfit": 100.0},
        }
        pilot = {
            "complete": True,
            "verdict": "达标",
            "metrics": {
                "orders": 40,
                "revenue": 80000,
                "netProfit": 2000,
                "adCostPerOrder": 30.0,
                "refundRate": 0.05,
                "avgOrderValue": 2000,
            },
        }
        rec = reconcile_profit(candidate, pilot, candidate["assumptions"], None)
        self.assertTrue(rec["complete"])
        self.assertEqual(rec["actualNetPerOrder"], 50.0)
        self.assertEqual(rec["differencePerOrder"], -50.0)
        labels = {item["item"] for item in rec["items"]}
        self.assertEqual(labels, {"售价差异", "广告成本差异", "退货预留差异", "采购成本差异"})
        ad_item = next(item for item in rec["items"] if item["item"] == "广告成本差异")
        self.assertEqual(ad_item["amount"], 400.0)

    def test_qc_templates_cover_the_candidate_category(self) -> None:
        from prospector.domain import qc_template

        self.assertIn("序列号", " ".join(qc_template("电子")))
        self.assertTrue(qc_template("不存在的类目"))

    def test_unified_profit_model_requires_fees_and_fx(self) -> None:
        from prospector.domain import calculate_profit

        same_currency = {
            "sellingPrice": 2099,
            "sellingCurrency": "CNY",
            "purchasePrice": 1899,
            "purchaseCurrency": "CNY",
        }
        incomplete = calculate_profit(same_currency)
        self.assertFalse(incomplete["complete"])
        self.assertIn("referralRate", incomplete["missingFields"])
        filled = calculate_profit({**same_currency, "referralRate": 0.05, "adCost": 50, "fulfillmentCost": 15, "returnReserveRate": 0.02})
        self.assertTrue(filled["complete"])
        self.assertEqual(filled["currency"], "CNY")
        self.assertAlmostEqual(filled["contributionProfit"], 2099 - 1899 - 2099 * 0.05 - 50 - 15 - 2099 * 0.02, places=2)

        cross_currency = {
            "sellingPrice": 2099,
            "sellingCurrency": "CNY",
            "purchasePrice": 32800,
            "purchaseCurrency": "JPY",
        }
        blocked = calculate_profit(cross_currency)
        self.assertFalse(blocked["complete"])
        self.assertIn("fxRate", blocked["missingFields"])
        self.assertIn("inboundCost", blocked["missingFields"])
        self.assertIn("dutyTaxCost", blocked["missingFields"])

    def test_candidates_are_created_with_the_observed_prices_only(self) -> None:
        from prospector.domain import calculate_profit

        result = pricegap.run_price_gap_scan("OPPO K13s", "中国", "中国")
        candidates = pricegap.build_price_gap_candidates(
            "OPPO K13s", result, {"market": "中国", "sourcingCountry": "中国"}
        )
        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate["assumptions"]["sellingPrice"], 2099.0)
        self.assertEqual(candidate["assumptions"]["sellingCurrency"], "CNY")
        self.assertEqual(candidate["assumptions"]["purchasePrice"], 1899.0)
        self.assertEqual(candidate["assumptions"]["purchaseCurrency"], "CNY")
        # Fees are never guessed, so the profit model stays incomplete.
        profit = calculate_profit(candidate["assumptions"])
        self.assertFalse(profit["complete"])
        self.assertIn("referralRate", profit["missingFields"])
        self.assertEqual(len(candidate["evidence"]), 2)


if __name__ == "__main__":
    unittest.main()
