"""Tests for the local-browser price collector.

The browser itself is never opened here: the API contract and the row ->
report pipeline are checked, so the suite stays runnable without a desktop.
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

from prospector.agent_pricegap import price_gap_report, price_gap_rows  # noqa: E402
from prospector.platforms import PLATFORMS  # noqa: E402


def _opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


OPENER = _opener()


def _get(url: str) -> tuple[int, dict]:
    try:
        with OPENER.open(url, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        with error:
            return error.code, json.loads(error.read().decode("utf-8"))


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


RAW_TAOBAO = [{
    "title": "OPPO K13s 12GB+256GB 官方标配 顺丰包邮",
    "price": 1499.0,
    "url": "https://item.taobao.com/item.htm?id=123456789",
    "snippet": "OPPO K13s 12GB+256GB ¥1499",
    "observedAt": "2026-11-16",
    "source": "本机浏览器采集",
}]

RAW_JD = [{
    "title": "OPPO K13s 12GB+256GB 京东自营",
    "price": 1399.0,
    "url": "https://item.jd.com/987654321.html",
    "snippet": "OPPO K13s 12GB+256GB ¥1399",
    "observedAt": "2026-11-16",
    "source": "本机浏览器采集",
}]


class CollectedRowTest(unittest.TestCase):
    def test_collected_rows_read_model_and_configuration(self) -> None:
        rows = price_gap_rows("OPPO K13s", PLATFORMS["taobao"], RAW_TAOBAO, "CNY")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["platformId"], "taobao")
        self.assertEqual(row["priceLow"], 1499.0)
        self.assertEqual(row["configKey"], "12G+256G")
        self.assertTrue(row["modelConfirmed"])

    def test_rows_without_the_model_are_dropped(self) -> None:
        rows = price_gap_rows(
            "OPPO K13s",
            PLATFORMS["taobao"],
            [{"title": "小米 14 12GB+256GB", "price": 3999.0, "url": "https://item.taobao.com/item.htm?id=1"}],
            "CNY",
        )
        self.assertEqual(rows, [])

    def test_two_platforms_form_a_comparable_group(self) -> None:
        rows = price_gap_rows("OPPO K13s", PLATFORMS["taobao"], RAW_TAOBAO, "CNY")
        rows += price_gap_rows("OPPO K13s", PLATFORMS["jd"], RAW_JD, "CNY")
        report = price_gap_report("OPPO K13s", "中国", rows, ["taobao", "jd"], source_note="测试")
        self.assertEqual(len(report["groups"]), 1)
        group = report["groups"][0]
        self.assertTrue(group["comparable"])
        self.assertEqual(group["grossGap"], 100.0)
        self.assertEqual(group["lowestPlatform"], "京东")
        self.assertEqual(group["highestPlatform"], "淘宝 / 天猫")
        self.assertEqual(report["status"], "needs_manual_validation")
        self.assertEqual(report["sourceCount"], 2)
        self.assertEqual(len(report["platformSummary"]), 2)

    def test_one_platform_alone_is_not_comparable(self) -> None:
        rows = price_gap_rows("OPPO K13s", PLATFORMS["taobao"], RAW_TAOBAO, "CNY")
        report = price_gap_report("OPPO K13s", "中国", rows, ["taobao", "jd"], source_note="测试")
        self.assertFalse(report["groups"][0]["comparable"])
        self.assertEqual(report["status"], "no_comparable_rows")


class BrowserApiTest(unittest.TestCase):
    server: ThreadingHTTPServer
    thread: threading.Thread
    base: str

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ["PROSPECTOR_DATA_PATH"] = str(Path(cls.tmp.name) / "prospector.json")
        import server  # noqa: PLC0415 - imported after the env var is set
        # Another test module may have imported the server first, so the
        # store is pointed at this module's own file either way.
        server.STORE = server.JsonStore(Path(os.environ["PROSPECTOR_DATA_PATH"]))

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def test_status_reports_the_browser_setup(self) -> None:
        status, payload = _get(f"{self.base}/api/browser/status")
        self.assertEqual(status, 200)
        self.assertIn("status", payload)
        platforms = {item["id"] for item in payload["status"]["platforms"]}
        self.assertIn("taobao", platforms)
        self.assertIn("jd", platforms)

    def test_collect_needs_a_product(self) -> None:
        status, payload = _post(f"{self.base}/api/browser/collect", {"platforms": ["taobao"]})
        self.assertEqual(status, 400)
        self.assertIn("商品", payload["error"])

    def test_collect_needs_a_platform(self) -> None:
        status, payload = _post(f"{self.base}/api/browser/collect", {"product": "OPPO K13s"})
        self.assertEqual(status, 400)
        self.assertIn("平台", payload["error"])

    def test_login_needs_a_known_platform(self) -> None:
        status, payload = _post(f"{self.base}/api/browser/login", {"platform": "not_a_platform"})
        self.assertEqual(status, 400)
        self.assertIn("平台", payload["error"])


if __name__ == "__main__":
    unittest.main()
