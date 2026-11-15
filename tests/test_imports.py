"""Tests for the authorized-file import path.

The parser is standard library only, so the file tests run without any
extra dependency; the API tests boot the real server.
"""

from __future__ import annotations

import base64
import io
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from prospector.imports import (  # noqa: E402 - import after sys.path setup
    map_headers,
    normalize_rows,
    parse_table,
    pick_price,
    summarize,
)


def _opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


OPENER = _opener()


def _get(url: str) -> tuple[int, str]:
    with OPENER.open(url, timeout=10) as response:
        return response.status, response.read().decode("utf-8")


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
            body = error.read().decode("utf-8")
        try:
            return error.code, json.loads(body)
        except json.JSONDecodeError:
            return error.code, {"error": body}


CSV_SAMPLE = (
    "商品名称,供应商,单价(元),起订量(件),交期(天),商品链接,报价日期,货号\n"
    "折叠收纳箱,义乌百货厂,12.50,100,7,https://detail.1688.com/offer/1.html,2026-11-10,SN-001\n"
    "折叠收纳箱,义乌百货厂,10.00,500,7,https://detail.1688.com/offer/1.html,2026-11-10,SN-001\n"
    "衣柜分隔板,东莞木业,¥23,50,10,https://detail.1688.com/offer/2.html,2026-11-11,SN-002\n"
)


def _xlsx_bytes(rows: list[list[str]]) -> bytes:
    """Build a tiny .xlsx file so the reader is tested on a real zip."""
    strings: list[str] = []

    def shared_index(value: str) -> int:
        if value not in strings:
            strings.append(value)
        return strings.index(value)

    sheet_rows = []
    for number, row in enumerate(rows, start=1):
        cells = []
        for column, value in enumerate(row):
            reference = f"{chr(ord('A') + column)}{number}"
            cells.append(f'<c r="{reference}" t="s"><v>{shared_index(str(value))}</v></c>')
        sheet_rows.append(f'<row r="{number}">' + "".join(cells) + "</row>")
    shared_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        + "".join(f"<si><t>{value}</t></si>" for value in strings)
        + "</sst>"
    )
    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{''.join(sheet_rows)}</sheetData></worksheet>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/sharedStrings.xml", shared_xml)
        archive.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<sheets><sheet name="Sheet1"/></sheets></workbook>',
        )
        archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)
    return buffer.getvalue()


class ImportParserTest(unittest.TestCase):
    def test_chinese_headers_map_to_canonical_fields(self) -> None:
        headers, rows = parse_table("报价.csv", CSV_SAMPLE.encode("utf-8"))
        mapping = map_headers(headers)
        self.assertEqual(mapping["title"], 0)
        self.assertEqual(mapping["seller"], 1)
        self.assertEqual(mapping["unitPrice"], 2)
        self.assertEqual(mapping["moq"], 3)
        self.assertEqual(mapping["leadTime"], 4)
        self.assertEqual(mapping["url"], 5)
        self.assertEqual(mapping["observedAt"], 6)
        self.assertEqual(len(rows), 3)

    def test_rows_become_quote_records(self) -> None:
        headers, rows = parse_table("报价.csv", CSV_SAMPLE.encode("utf-8"))
        records, warnings = normalize_rows(headers, rows, "1688")
        self.assertEqual(warnings, [])
        self.assertEqual(len(records), 3)
        first = records[0]
        self.assertEqual(first["title"], "折叠收纳箱")
        self.assertEqual(first["seller"], "义乌百货厂")
        self.assertEqual(first["unitPrice"], 12.5)
        self.assertEqual(first["currency"], "CNY")
        self.assertEqual(first["moq"], 100)
        self.assertEqual(first["leadTimeDays"], 7)
        self.assertEqual(first["observedAt"], "2026-11-10")
        self.assertEqual(first["platform"], "1688")
        self.assertEqual(first["attributes"]["货号"], "SN-001")

    def test_unreadable_price_is_skipped_and_reported(self) -> None:
        headers, rows = parse_table("报价.csv", "商品名称,单价\n收纳箱,待议\n".encode("utf-8"))
        records, warnings = normalize_rows(headers, rows, "1688")
        self.assertEqual(records, [])
        self.assertTrue(any("读不出价格" in item for item in warnings))

    def test_price_rules_pick_the_expected_row(self) -> None:
        headers, rows = parse_table("报价.csv", CSV_SAMPLE.encode("utf-8"))
        records, _ = normalize_rows(headers, rows, "1688")
        self.assertEqual(pick_price(records, "lowest")["unitPrice"], 10.0)
        self.assertEqual(pick_price(records, "highest")["unitPrice"], 23.0)
        self.assertEqual(pick_price(records, "first")["unitPrice"], 12.5)
        self.assertEqual(pick_price(records, "median")["unitPrice"], 12.5)

    def test_xlsx_is_read_without_extra_dependencies(self) -> None:
        payload = _xlsx_bytes([
            ["商品名称", "供应商", "单价", "起订量"],
            ["折叠收纳箱", "义乌百货厂", 12.5, 100],
        ])
        headers, rows = parse_table("报价.xlsx", payload)
        records, warnings = normalize_rows(headers, rows, "1688")
        self.assertEqual(warnings, [])
        self.assertEqual(records[0]["title"], "折叠收纳箱")
        self.assertEqual(records[0]["unitPrice"], 12.5)
        self.assertEqual(records[0]["moq"], 100)

    def test_summary_counts_are_reported(self) -> None:
        headers, rows = parse_table("报价.csv", CSV_SAMPLE.encode("utf-8"))
        records, _ = normalize_rows(headers, rows, "1688")
        summary = summarize(records)
        self.assertEqual(summary["rows"], 3)
        self.assertEqual(summary["sellers"], 2)
        self.assertEqual(summary["lowest"], 10.0)
        self.assertEqual(summary["highest"], 23.0)


class ImportApiTest(unittest.TestCase):
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

    def _candidate(self) -> str:
        status, payload = _post(f"{self.base}/api/candidates", {
            "mode": "specified_product",
            "name": "折叠收纳箱",
            "category": "家居收纳",
        })
        self.assertEqual(status, 201)
        return payload["id"]

    def test_import_writes_evidence_suppliers_observations_and_model(self) -> None:
        candidate_id = self._candidate()
        status, payload = _post(f"{self.base}/api/imports", {
            "fileName": "1688报价.csv",
            "contentBase64": base64.b64encode(CSV_SAMPLE.encode("utf-8")).decode("ascii"),
            "platform": "1688",
            "candidateId": candidate_id,
            "targets": ["evidence", "assumptions", "suppliers", "observations"],
            "priceRule": "lowest",
        })
        self.assertEqual(status, 201, payload)
        record = payload["import"]
        self.assertEqual(record["rowCount"], 3)
        self.assertEqual(record["applied"]["evidence"], 3)
        self.assertEqual(record["applied"]["observations"], 3)
        self.assertEqual(record["applied"]["suppliers"], 3)
        self.assertEqual(record["applied"]["assumptions"]["value"], 10.0)

        bootstrap = json.loads(_get(f"{self.base}/api/bootstrap")[1])
        candidate = next(item for item in bootstrap["candidates"] if item["id"] == candidate_id)
        self.assertEqual(candidate["assumptions"]["purchasePrice"], 10.0)
        self.assertEqual(candidate["assumptions"]["purchaseCurrency"], "CNY")
        sourcing = [item for item in candidate["evidence"] if item["dimension"] == "sourcing"]
        self.assertEqual(len(sourcing), 3)
        self.assertEqual(sourcing[0]["sourceType"], "授权文件导入")
        self.assertEqual(len(bootstrap["suppliers"]), 2)
        self.assertTrue(any(item.get("quoteHistory") for item in bootstrap["suppliers"]))

    def test_the_same_file_is_not_imported_twice(self) -> None:
        payload = {
            "fileName": "重复.csv",
            "contentBase64": base64.b64encode(
                (CSV_SAMPLE + "床下收纳箱,东莞木业,31,30,12,https://detail.1688.com/offer/3.html,2026-11-12,SN-003\n").encode("utf-8")
            ).decode("ascii"),
            "platform": "1688",
            "targets": ["observations"],
        }
        status, _ = _post(f"{self.base}/api/imports", payload)
        self.assertEqual(status, 201)
        status, body = _post(f"{self.base}/api/imports", payload)
        self.assertEqual(status, 409)
        self.assertIn("已经导入过", body["error"])

    def test_import_needs_a_candidate_for_evidence(self) -> None:
        status, body = _post(f"{self.base}/api/imports", {
            "fileName": "无候选.csv",
            "contentBase64": base64.b64encode(CSV_SAMPLE.encode("utf-8")).decode("ascii"),
            "platform": "1688",
            "targets": ["evidence"],
        })
        self.assertEqual(status, 400)
        self.assertIn("候选", body["error"])

    def test_unknown_platform_is_rejected(self) -> None:
        status, body = _post(f"{self.base}/api/imports", {
            "fileName": "x.csv",
            "contentBase64": base64.b64encode(CSV_SAMPLE.encode("utf-8")).decode("ascii"),
            "platform": "unknown_platform",
        })
        self.assertEqual(status, 400)
        self.assertIn("平台", body["error"])

    def test_deleting_an_import_removes_what_it_wrote(self) -> None:
        candidate_id = self._candidate()
        status, payload = _post(f"{self.base}/api/imports", {
            "fileName": "可撤销.csv",
            "contentBase64": base64.b64encode(CSV_SAMPLE.encode("utf-8")).decode("ascii"),
            "platform": "1688",
            "candidateId": candidate_id,
            "targets": ["evidence", "assumptions", "suppliers", "observations"],
        })
        self.assertEqual(status, 201, payload)
        import_id = payload["import"]["id"]

        status, _ = _post(f"{self.base}/api/imports/{import_id}", {}, method="DELETE")
        self.assertEqual(status, 200)

        bootstrap = json.loads(_get(f"{self.base}/api/bootstrap")[1])
        candidate = next(item for item in bootstrap["candidates"] if item["id"] == candidate_id)
        self.assertEqual([item for item in candidate["evidence"] if item.get("importId") == import_id], [])
        self.assertNotIn("purchasePrice", candidate.get("assumptions", {}))
        self.assertEqual([item for item in bootstrap["imports"] if item["id"] == import_id], [])
        self.assertEqual(
            [item for item in bootstrap["priceObservations"] if item.get("importId") == import_id],
            [],
        )


if __name__ == "__main__":
    unittest.main()
