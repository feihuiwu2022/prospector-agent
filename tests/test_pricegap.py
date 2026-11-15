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
