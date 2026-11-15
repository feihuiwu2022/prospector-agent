"""Smoke test: boot the real server and check the contracts the UI relies on.

Standard library only, and deliberately free of any model or search dependency,
so it runs in CI without credentials.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _opener() -> urllib.request.OpenerDirector:
    """Build an opener that ignores any system proxy configuration."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


OPENER = _opener()


def _get(url: str) -> tuple[int, str, str]:
    with OPENER.open(url, timeout=10) as response:
        return response.status, response.headers.get("Content-Type", ""), response.read().decode("utf-8")


def _post(url: str, payload: dict, method: str = "POST") -> tuple[int, dict]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        with OPENER.open(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        with error:
            return error.code, json.loads(error.read().decode("utf-8"))


class ServerSmokeTest(unittest.TestCase):
    server: ThreadingHTTPServer
    thread: threading.Thread
    base: str

    @classmethod
    def setUpClass(cls) -> None:
        # The store path is read at import time, so point it at a temp file first.
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ["PROSPECTOR_DATA_PATH"] = str(Path(cls.tmp.name) / "prospector.json")
        import server  # noqa: PLC0415 - imported after the env var is set

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def test_bootstrap_contract(self) -> None:
        status, _, body = _get(f"{self.base}/api/bootstrap")
        self.assertEqual(status, 200)
        data = json.loads(body)
        for key in ("candidates", "profile", "discoveryStrategies", "workflowStages", "connectors", "platforms", "conversations"):
            self.assertIn(key, data)
        self.assertIsInstance(data["candidates"], list)

    def test_four_strategies_expose_tags_and_sources(self) -> None:
        data = json.loads(_get(f"{self.base}/api/bootstrap")[2])
        strategies = data["discoveryStrategies"]
        self.assertEqual(len(strategies), 4)
        self.assertEqual([item["code"] for item in strategies], ["A", "B", "C", "D"])
        for strategy in strategies:
            self.assertTrue(strategy["tags"], f"策略 {strategy['code']} 缺少标签")
            self.assertTrue(strategy["sourceHint"], f"策略 {strategy['code']} 缺少线索来源")

    def test_six_workflow_stages(self) -> None:
        data = json.loads(_get(f"{self.base}/api/bootstrap")[2])
        stages = data["workflowStages"]
        self.assertEqual(len(stages), 6)
        self.assertEqual([item["number"] for item in stages], [1, 2, 3, 4, 5, 6])

    def test_domestic_platforms_are_registered(self) -> None:
        data = json.loads(_get(f"{self.base}/api/bootstrap")[2])
        names = {item["name"] for item in data["platforms"]}
        for expected in ("抖音", "快手", "淘宝 / 天猫", "京东", "拼多多"):
            self.assertIn(expected, names)
        roles = {role for item in data["connectors"] for role in item["roles"]}
        self.assertEqual(roles, {"demand", "sales", "sourcing"})
        for connector in data["connectors"]:
            self.assertIn(connector["status"], {"web_search", "manual_source", "restricted_api", "authorization_required"})
            self.assertTrue(connector["access"], f"{connector['platform']} 缺少访问方式说明")
            self.assertTrue(connector["authorizationChannel"], f"{connector['platform']} 缺少授权通道说明")

    def test_markets_drive_sales_and_supply_channels(self) -> None:
        data = json.loads(_get(f"{self.base}/api/bootstrap")[2])
        self.assertEqual(data["markets"], ["日本", "中国", "海外"])
        self.assertEqual(
            data["marketGroups"],
            [{"group": "国内", "markets": ["中国"]}, {"group": "国际", "markets": ["日本", "海外"]}],
        )
        platforms = data["platforms"]
        japan = [item["name"] for item in platforms if "sales" in item["roles"] and item.get("sales_market") == "日本"]
        self.assertIn("Amazon.co.jp", japan)
        china = [item["name"] for item in platforms if "sales" in item["roles"] and item.get("sales_market") == "中国"]
        self.assertIn("淘宝 / 天猫", china)
        supply = [item["name"] for item in platforms if "sourcing" in item["roles"] and item.get("supply_market") == "中国"]
        self.assertIn("1688", supply)

    def test_profile_market_switch_resets_platform(self) -> None:
        status, _ = _post(f"{self.base}/api/profile", {"market": "中国"}, method="PATCH")
        self.assertEqual(status, 200)
        profile = json.loads(_get(f"{self.base}/api/bootstrap")[2])["profile"]
        self.assertEqual(profile["market"], "中国")
        self.assertEqual(profile["platform"], "淘宝 / 天猫")
        status, payload = _post(f"{self.base}/api/profile", {"market": "东南亚"}, method="PATCH")
        self.assertEqual(status, 400)
        self.assertIn("error", payload)

    def test_profile_round_trip(self) -> None:
        status, _ = _post(f"{self.base}/api/profile", {"sourcingPlatforms": ["1688", "jd"]}, method="PATCH")
        self.assertEqual(status, 200)
        profile = json.loads(_get(f"{self.base}/api/bootstrap")[2])["profile"]
        self.assertEqual(profile["sourcingPlatforms"], ["1688", "jd"])

    def test_price_gap_mode_is_offered(self) -> None:
        data = json.loads(_get(f"{self.base}/api/bootstrap")[2])
        modes = {item["id"] for item in data["modes"]}
        self.assertIn("price_gap", modes)

    def test_price_gap_pair_runs_the_same_spec_check(self) -> None:
        import server  # noqa: PLC0415 - the server module is imported in setUpClass

        candidate_id = "gap-candidate-1"

        def insert(data):
            data.setdefault("candidates", []).append({
                "id": candidate_id,
                "name": "OPPO K13s 12G+256G",
                "category": "其他",
                "hypothesis": "同配置跨平台差价",
                "market": "中国",
                "platform": "京东",
                "sourcingCountry": "日本",
                "fulfillment": "待确认",
                "mode": "active_research",
                "status": "researching",
                "assumptions": {
                    "sellingPrice": 2099,
                    "sellingCurrency": "CNY",
                    "purchasePrice": 32800,
                    "purchaseCurrency": "JPY",
                },
                "evidence": [],
                "scores": {},
                "tasks": [],
                "priceGap": {
                    "sourcePlatform": "Amazon.co.jp",
                    "sourceTitle": "OPPO K13s 12GB+256GB",
                    "sourcePrice": 32800,
                    "sourceCurrency": "JPY",
                    "sourceUrl": "https://www.amazon.co.jp/dp/B0TEST",
                    "sourceVariant": "12G+256G",
                    "sourceColor": "",
                    "sourceMarket": "日本",
                    "targetPlatform": "京东",
                    "targetTitle": "OPPO K13s 12+256",
                    "targetPrice": 2099,
                    "targetCurrency": "CNY",
                    "targetUrl": "https://item.jd.com/2.html",
                    "targetVariant": "12G+256G",
                    "targetColor": "",
                    "targetMarket": "中国",
                    "brand": "OPPO",
                    "model": "OPPO K13s",
                    "observedAt": "2026-11-15",
                },
                "createdAt": "2026-11-15T00:00:00+00:00",
                "updatedAt": "2026-11-15T00:00:00+00:00",
            })

        server.STORE.update(insert)
        status, payload = _post(f"{self.base}/api/discovery", {"trigger": "price_gap_pair", "candidateId": candidate_id})
        self.assertEqual(status, 201)
        self.assertIn(payload["matching"]["status"], {"exact_match", "probable_match"})
        # The cost inputs are missing, so no profit may be claimed.
        self.assertIsNone(payload["estimate"])
        self.assertTrue(payload["estimateError"])

    def test_checklist_update_advances_the_sourcing_stage(self) -> None:
        import server  # noqa: PLC0415 - the server module is imported in setUpClass

        candidate_id = "checklist-candidate-1"

        def insert(data):
            data.setdefault("candidates", []).append({
                "id": candidate_id,
                "name": "比价候选",
                "category": "其他",
                "hypothesis": "采购待办推进",
                "market": "中国",
                "platform": "京东",
                "sourcingCountry": "中国",
                "fulfillment": "待确认",
                "mode": "active_research",
                "status": "researching",
                "assumptions": {"sellingPrice": 2099, "sellingCurrency": "CNY", "purchasePrice": 1899, "purchaseCurrency": "CNY"},
                "evidence": [],
                "scores": {},
                "tasks": [],
                "purchaseChecklist": [
                    {"id": "price_evidence", "item": "采购参考价", "status": "已有线索"},
                    {"id": "moq", "item": "确认起订量与阶梯价", "status": "待确认"},
                ],
                "workflowStages": [
                    {"id": "sourcing", "number": 3, "title": "寻找货源", "status": "partial", "note": ""},
                    {"id": "economics", "number": 4, "title": "利润核算", "status": "partial", "note": ""},
                ],
                "createdAt": "2026-11-15T00:00:00+00:00",
                "updatedAt": "2026-11-15T00:00:00+00:00",
            })

        server.STORE.update(insert)
        status, payload = _post(
            f"{self.base}/api/candidates/{candidate_id}",
            {"purchaseChecklist": [{"id": "moq", "done": True}]},
            method="PATCH",
        )
        self.assertEqual(status, 200)
        stages = {stage["id"]: stage for stage in payload["workflowStages"]}
        self.assertEqual(stages["sourcing"]["status"], "completed")
        self.assertEqual(stages["economics"]["status"], "partial")
        self.assertTrue(payload["purchaseChecklist"][1]["done"])

    def test_supplier_archive_records_a_shop(self) -> None:
        status, payload = _post(
            f"{self.base}/api/suppliers",
            {
                "platform": "淘宝 / 天猫",
                "seller": "shop123.taobao.com",
                "url": "https://shop123.taobao.com/item.htm?id=1",
                "price": 1899,
                "currency": "CNY",
                "product": "OPPO K13s",
                "observedAt": "2026-11-15",
            },
        )
        self.assertEqual(status, 201)
        supplier_id = payload["id"]
        self.assertEqual(payload["status"], "待联系")
        self.assertEqual(payload["lastPrice"], 1899)

        status, updated = _post(
            f"{self.base}/api/suppliers/{supplier_id}",
            {"status": "已报价", "note": "100 件起，报价含税"},
            method="PATCH",
        )
        self.assertEqual(status, 200)
        self.assertEqual(updated["status"], "已报价")
        self.assertIn("100 件起", updated["note"])

        bootstrap = json.loads(_get(f"{self.base}/api/bootstrap")[2])
        self.assertTrue(any(item["id"] == supplier_id for item in bootstrap["suppliers"]))

    def test_supplier_requires_platform_and_seller(self) -> None:
        status, payload = _post(f"{self.base}/api/suppliers", {"platform": "淘宝"}, method="POST")
        self.assertEqual(status, 400)
        self.assertIn("error", payload)

    def test_metrics_summarise_the_local_record(self) -> None:
        status, _, body = _get(f"{self.base}/api/metrics")
        self.assertEqual(status, 200)
        metrics = json.loads(body)
        self.assertIn("candidates", metrics)
        self.assertIn("reviews", metrics)
        self.assertIn("evidence", metrics)
        self.assertGreaterEqual(metrics["candidates"]["total"], 0)
        self.assertIn("rejectReasons", metrics["reviews"])

    def test_rfq_creation_and_quote_collection(self) -> None:
        status, rfq = _post(
            f"{self.base}/api/rfqs",
            {
                "product": "OPPO K13s",
                "spec": "12G+256G",
                "market": "中国",
                "currency": "CNY",
                "targets": [
                    {"platform": "淘宝 / 天猫", "seller": "shop-a.taobao.com", "price": 1899, "currency": "CNY"},
                    {"platform": "京东", "seller": "mall.jd.com", "price": 2099, "currency": "CNY"},
                ],
                "questions": ["起订量与阶梯价"],
            },
        )
        self.assertEqual(status, 201)
        self.assertEqual(len(rfq["targets"]), 2)
        self.assertEqual(rfq["status"], "待发送")

        status, updated = _post(
            f"{self.base}/api/rfqs/{rfq['id']}/quotes",
            {"platform": "淘宝 / 天猫", "seller": "shop-a.taobao.com", "unitPrice": 1820, "moq": 50, "leadTimeDays": 7, "note": "含税"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(len(updated["quotes"]), 1)
        self.assertEqual(updated["status"], "回收中")
        self.assertEqual(updated["targets"][0]["status"], "已报价")

        bootstrap = json.loads(_get(f"{self.base}/api/bootstrap")[2])
        supplier = next(item for item in bootstrap["suppliers"] if item["seller"] == "shop-a.taobao.com")
        self.assertEqual(supplier["status"], "已报价")
        self.assertEqual(supplier["lastPrice"], 1820)
        self.assertTrue(supplier["quoteHistory"])

    def test_rfq_needs_product_and_targets(self) -> None:
        status, payload = _post(f"{self.base}/api/rfqs", {"product": "OPPO K13s"}, method="POST")
        self.assertEqual(status, 400)
        self.assertIn("error", payload)

    def test_written_quote_can_become_the_purchase_cost(self) -> None:
        import server  # noqa: PLC0415 - the server module is imported in setUpClass

        candidate_id = "quote-candidate-1"

        def insert(data):
            data.setdefault("candidates", []).append({
                "id": candidate_id,
                "name": "OPPO K13s 12G+256G",
                "category": "其他",
                "hypothesis": "报价回填",
                "market": "中国",
                "platform": "京东",
                "sourcingCountry": "中国",
                "fulfillment": "待确认",
                "mode": "active_research",
                "status": "researching",
                "assumptions": {"sellingPrice": 2099, "sellingCurrency": "CNY", "purchasePrice": 1899, "purchaseCurrency": "CNY"},
                "evidence": [],
                "scores": {},
                "tasks": [],
                "createdAt": "2026-11-15T00:00:00+00:00",
                "updatedAt": "2026-11-15T00:00:00+00:00",
            })

        server.STORE.update(insert)
        status, rfq = _post(
            f"{self.base}/api/rfqs",
            {
                "candidateId": candidate_id,
                "product": "OPPO K13s",
                "spec": "12G+256G",
                "market": "中国",
                "currency": "CNY",
                "targets": [{"platform": "淘宝 / 天猫", "seller": "shop-a.taobao.com"}],
            },
        )
        self.assertEqual(status, 201)

        status, _ = _post(
            f"{self.base}/api/rfqs/{rfq['id']}/quotes",
            {
                "platform": "淘宝 / 天猫",
                "seller": "shop-a.taobao.com",
                "unitPrice": 1820,
                "moq": 50,
                "leadTimeDays": 7,
                "currency": "CNY",
                "applyToCandidate": True,
            },
        )
        self.assertEqual(status, 200)
        candidate = json.loads(_get(f"{self.base}/api/candidates/{candidate_id}")[2])
        self.assertEqual(candidate["assumptions"]["purchasePrice"], 1820)
        self.assertEqual(candidate["assumptions"]["purchaseCurrency"], "CNY")
        sourcing = [item for item in candidate["evidence"] if item["dimension"] == "sourcing"]
        self.assertTrue(sourcing)
        self.assertIn("书面报价", sourcing[-1]["note"])
        self.assertIn("1820", sourcing[-1]["note"])

    def test_purchase_order_draft_records_the_human_steps(self) -> None:
        status, po = _post(
            f"{self.base}/api/purchase-orders",
            {
                "platform": "淘宝 / 天猫",
                "seller": "shop-a.taobao.com",
                "product": "OPPO K13s",
                "spec": "12G+256G",
                "quantity": 60,
                "unitPrice": 1820,
                "currency": "CNY",
                "leadTimeDays": 7,
                "paymentTerms": "预付 30%",
            },
        )
        self.assertEqual(status, 201)
        self.assertEqual(po["status"], "草案")
        self.assertEqual(po["amount"], 109200.0)
        self.assertTrue(po["expectedArrival"])
        self.assertEqual(po["history"][0]["status"], "草案")

        status, updated = _post(
            f"{self.base}/api/purchase-orders/{po['id']}",
            {"status": "已下单", "note": "已在平台下单"},
            method="PATCH",
        )
        self.assertEqual(status, 200)
        self.assertEqual(updated["status"], "已下单")
        self.assertEqual(updated["history"][-1]["note"], "已在平台下单")

        status, payload = _post(
            f"{self.base}/api/purchase-orders/{po['id']}",
            {"status": "已付款"},
            method="PATCH",
        )
        self.assertEqual(status, 400)

    def test_purchase_order_receipt_opens_the_actual_cost_step(self) -> None:
        import server  # noqa: PLC0415 - the server module is imported in setUpClass

        candidate_id = "po-candidate-1"

        def insert(data):
            data.setdefault("candidates", []).append({
                "id": candidate_id,
                "name": "OPPO K13s 12G+256G",
                "category": "其他",
                "hypothesis": "到货回填",
                "market": "中国",
                "platform": "京东",
                "sourcingCountry": "中国",
                "fulfillment": "待确认",
                "mode": "active_research",
                "status": "researching",
                "assumptions": {},
                "evidence": [],
                "scores": {},
                "tasks": [],
                "purchaseChecklist": [],
                "workflowStages": [
                    {"id": "sourcing", "number": 3, "title": "寻找货源", "status": "partial", "note": ""},
                ],
                "createdAt": "2026-11-15T00:00:00+00:00",
                "updatedAt": "2026-11-15T00:00:00+00:00",
            })

        server.STORE.update(insert)
        status, po = _post(
            f"{self.base}/api/purchase-orders",
            {
                "candidateId": candidate_id,
                "platform": "淘宝 / 天猫",
                "seller": "shop-a.taobao.com",
                "product": "OPPO K13s",
                "spec": "12G+256G",
                "quantity": 60,
                "unitPrice": 1820,
                "currency": "CNY",
                "leadTimeDays": 7,
            },
        )
        self.assertEqual(status, 201)
        candidate = json.loads(_get(f"{self.base}/api/candidates/{candidate_id}")[2])
        delivery = [item for item in candidate["purchaseChecklist"] if item["id"] == f"po_{po['id']}_delivery"]
        self.assertTrue(delivery)
        self.assertFalse(delivery[0]["done"])

        _post(f"{self.base}/api/purchase-orders/{po['id']}", {"status": "已收货"}, method="PATCH")
        candidate = json.loads(_get(f"{self.base}/api/candidates/{candidate_id}")[2])
        ids = {item["id"] for item in candidate["purchaseChecklist"]}
        self.assertIn(f"po_{po['id']}_actual", ids)
        delivery = next(item for item in candidate["purchaseChecklist"] if item["id"] == f"po_{po['id']}_delivery")
        self.assertTrue(delivery["done"])

    def test_actual_cost_update_writes_assumptions_and_evidence(self) -> None:
        import server  # noqa: PLC0415 - the server module is imported in setUpClass

        candidate_id = "cost-candidate-1"

        def insert(data):
            data.setdefault("candidates", []).append({
                "id": candidate_id,
                "name": "OPPO K13s 12G+256G",
                "category": "电子",
                "hypothesis": "到货成本回填",
                "market": "中国",
                "platform": "京东",
                "sourcingCountry": "中国",
                "fulfillment": "待确认",
                "mode": "active_research",
                "status": "researching",
                "assumptions": {"sellingPrice": 2099, "sellingCurrency": "CNY"},
                "evidence": [],
                "scores": {},
                "tasks": [],
                "purchaseChecklist": [{"id": "po_x_actual", "item": "录入实际到货数量与总成本", "status": "待执行", "done": False}],
                "workflowStages": [{"id": "sourcing", "number": 3, "title": "寻找货源", "status": "partial", "note": ""}],
                "createdAt": "2026-11-15T00:00:00+00:00",
                "updatedAt": "2026-11-15T00:00:00+00:00",
            })

        server.STORE.update(insert)
        status, payload = _post(
            f"{self.base}/api/candidates/{candidate_id}",
            {
                "actualCost": {
                    "quantity": 60,
                    "unitPrice": 1820,
                    "purchaseCurrency": "CNY",
                    "shippingTotal": 600,
                    "dutyTotal": 300,
                    "insuranceTotal": 60,
                    "defectRate": 0.02,
                    "receivedAt": "2026-11-15",
                    "qcChecks": ["外包装完好无破损"],
                }
            },
            method="PATCH",
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["assumptions"]["purchasePrice"], 1820)
        self.assertEqual(payload["assumptions"]["purchaseCurrency"], "CNY")
        self.assertEqual(payload["assumptions"]["inboundCost"], 11.0)
        self.assertEqual(payload["assumptions"]["dutyTaxCost"], 5.0)
        sourcing = [item for item in payload["evidence"] if item["dimension"] == "sourcing"]
        self.assertTrue(sourcing)
        self.assertIn("到岸成本", sourcing[-1]["note"])
        done = next(item for item in payload["purchaseChecklist"] if item["id"] == "po_x_actual")
        self.assertTrue(done["done"])

    def test_cost_check_can_write_into_the_profit_model(self) -> None:
        import server  # noqa: PLC0415 - the server module is imported in setUpClass

        candidate_id = "cost-check-candidate-1"

        def insert(data):
            data.setdefault("candidates", []).append({
                "id": candidate_id,
                "name": "OPPO K13s",
                "category": "电子",
                "hypothesis": "成本待查",
                "market": "中国",
                "platform": "京东",
                "sourcingCountry": "中国",
                "fulfillment": "待确认",
                "mode": "active_research",
                "status": "researching",
                "assumptions": {"sellingPrice": 2099, "sellingCurrency": "CNY"},
                "evidence": [],
                "scores": {},
                "tasks": [],
                "purchaseChecklist": [
                    {"id": "cost_adCost", "item": "核对成本项：广告成本", "status": "待确认", "done": False},
                    {"id": "soft_0", "item": "核对成本项：包材与赠品", "status": "待确认", "done": False},
                ],
                "workflowStages": [{"id": "sourcing", "number": 3, "title": "寻找货源", "status": "partial", "note": ""}],
                "createdAt": "2026-11-15T00:00:00+00:00",
                "updatedAt": "2026-11-15T00:00:00+00:00",
            })

        server.STORE.update(insert)
        status, payload = _post(
            f"{self.base}/api/candidates/{candidate_id}",
            {"costCheck": {"id": "cost_adCost", "mode": "apply", "value": 30}},
            method="PATCH",
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["assumptions"]["adCost"], 30.0)
        done = next(item for item in payload["purchaseChecklist"] if item["id"] == "cost_adCost")
        self.assertTrue(done["done"])

        status, payload = _post(
            f"{self.base}/api/candidates/{candidate_id}",
            {"costCheck": {"id": "soft_0", "mode": "apply", "value": 3}},
            method="PATCH",
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["assumptions"]["otherCost"], 3.0)

        status, payload = _post(
            f"{self.base}/api/candidates/{candidate_id}",
            {"costCheck": {"id": "soft_0", "mode": "apply", "value": 5}},
            method="PATCH",
        )
        self.assertEqual(status, 200)
        # Re-confirming replaces the earlier value instead of adding to it.
        self.assertEqual(payload["assumptions"]["otherCost"], 5.0)

    def test_connectors_expose_an_authorization_checklist(self) -> None:
        data = json.loads(_get(f"{self.base}/api/bootstrap")[2])
        connectors = {item["platform"]: item for item in data["connectors"]}
        guide = connectors["1688"]["authorizationGuide"]
        self.assertTrue(guide["portal"].startswith("http"))
        self.assertTrue(guide["requirements"])
        self.assertTrue(guide["scopes"])
        self.assertTrue(guide["fields"])
        self.assertIn("MOQ", " ".join(guide["fields"]))
        for connector in data["connectors"]:
            self.assertTrue(connector["authorizationGuide"], f"{connector['platform']} 缺少授权准备清单")

    def test_static_assets_are_served(self) -> None:
        for path, needle in (("/", "app-content"), ("/styles.css", ".app"), ("/app.js", "renderHome")):
            status, content_type, body = _get(f"{self.base}{path}")
            self.assertEqual(status, 200, path)
            self.assertIn(needle, body, path)
            self.assertTrue(content_type, path)

    def test_unknown_api_path_returns_json_error(self) -> None:
        status, payload = _post(f"{self.base}/api/nope", {})
        self.assertEqual(status, 404)
        self.assertIn("error", payload)

    def test_candidate_creation_validates_input(self) -> None:
        status, payload = _post(f"{self.base}/api/candidates", {"mode": "specified_product", "name": ""})
        self.assertEqual(status, 400)
        self.assertIn("error", payload)


if __name__ == "__main__":
    unittest.main()
