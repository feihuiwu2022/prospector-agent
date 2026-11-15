"""Read authorized quote exports into the same evidence and profit model.

An operator who already holds platform credentials can export quotes from the
official console and drag the file here.  The file is the hard data: it is
read with the standard library only, so this path needs no developer
credentials of its own.

The parser is forgiving about column names (Chinese and English both work)
and strict about numbers: a price that cannot be read is reported as a
skipped row, never guessed.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
import xml.etree.ElementTree as ET
from datetime import date, timedelta


MAX_FILE_BYTES = 5 * 1024 * 1024

# Canonical field -> accepted header names (already normalized).
FIELD_ALIASES = {
    "title": (
        "商品", "商品名称", "商品标题", "标题", "名称", "品名", "货品名称", "货号",
        "offer", "offer标题", "offerid", "商品id", "product", "producttitle", "title", "itemname",
    ),
    "platform": ("平台", "来源平台", "站点", "platform", "site", "channel"),
    "seller": (
        "供应商", "供应商名称", "店铺", "店铺名称", "卖家", "供货商", "工厂", "公司",
        "supplier", "seller", "storename", "company", "factory",
    ),
    "unitPrice": (
        "单价", "价格", "采购价", "供货价", "报价", "成交价", "挂牌价", "含税价", "单价元",
        "price", "unitprice", "unitcost", "cost", "offerprice",
    ),
    "tierPrice": ("阶梯价", "档位价", "区间价", "批量价", "tierprice", "ladderprice", "pricebreak"),
    "currency": ("币种", "货币", "currency", "money", "币别"),
    "moq": ("起订量", "起批量", "最小起订量", "最小订购量", "起订件数", "moq", "minorder", "minqty"),
    "leadTime": ("交期", "交货期", "货期", "发货周期", "备货期", "leadtime", "leaddays", "deliverytime"),
    "url": ("链接", "商品链接", "商品地址", "网址", "url", "link", "offerurl", "producturl"),
    "spec": ("规格", "型号", "颜色", "尺寸", "包装数量", "属性", "sku", "spec", "specification", "model", "size"),
    "stock": ("库存", "可用库存", "现货", "stock", "inventory", "quantityavailable"),
    "observedAt": ("日期", "观察日期", "采集日期", "报价日期", "更新时间", "date", "observedat", "updatedat", "pricedate"),
    "note": ("备注", "说明", "note", "remark", "comment", "memo"),
}

# Platform -> currency of the quoted price, used when the file has no column
# for it.  Only platforms with a known quote currency are listed.
PLATFORM_CURRENCY = {
    "1688": "CNY",
    "taobao": "CNY",
    "jd": "CNY",
    "pdd": "CNY",
    "yiwugo": "CNY",
    "amazon_jp": "JPY",
    "rakuten_yahoo_jp": "JPY",
    "mercari_jp": "JPY",
    "tiktok_shop": "JPY",
    "xianyu": "CNY",
    "douyin": "CNY",
    "kuaishou": "CNY",
}

PLATFORM_NAMES = {
    "1688": "1688",
    "taobao": "淘宝 / 天猫",
    "jd": "京东",
    "pdd": "拼多多",
    "yiwugo": "义乌购 / 中国商品网",
    "amazon_jp": "Amazon.co.jp",
    "rakuten_yahoo_jp": "乐天 / Yahoo! Shopping",
    "mercari_jp": "Mercari Japan",
    "tiktok_shop": "TikTok Shop",
    "xianyu": "闲鱼（个人二手）",
    "douyin": "抖音",
    "kuaishou": "快手",
}

PRICE_RULES = {
    "lowest": "最低报价",
    "highest": "最高报价",
    "average": "平均报价",
    "median": "中位报价",
    "first": "第一行报价",
}


def _normalize_header(value: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[（(\[【][^）)\]】]*[）)\]】]", "", text)
    text = re.sub(r"\s+|[_\-·/\\]", "", text)
    return text


def _clean(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip().strip('"').strip()


def _to_float(value) -> float | None:
    text = _clean(value)
    if not text:
        return None
    text = text.replace(",", "").replace("，", "").replace("¥", "").replace("￥", "").replace("$", "")
    text = text.replace("元", "").strip()
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _to_int(value) -> int | None:
    number = _to_float(value)
    if number is None:
        return None
    try:
        return int(round(number))
    except (TypeError, ValueError):
        return None


def _to_date(value) -> str:
    text = _clean(value)
    if not text:
        return ""
    text = text.replace("/", "-").replace(".", "-").replace("年", "-").replace("月", "-").replace("日", "")
    match = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3))).isoformat()
        except ValueError:
            return ""
    return ""


def _normalize_url(value) -> str:
    text = _clean(value)
    if not text:
        return ""
    if text.startswith("http://") or text.startswith("https://"):
        return text[:2000]
    if text.startswith("//"):
        return f"https:{text}"[:2000]
    if re.match(r"^[\w.-]+\.[a-z]{2,}/", text, re.IGNORECASE):
        return f"https://{text}"[:2000]
    return ""


def _guess_delimiter(sample: str) -> str:
    counts = {candidate: sample.count(candidate) for candidate in (",", "\t", ";", "|")}
    best = max(counts, key=lambda key: counts[key])
    return best if counts[best] else ","


def parse_delimited(text: str) -> tuple[list[str], list[list[str]]]:
    """Read a CSV/TSV export into a header row and data rows."""
    delimiter = _guess_delimiter(text[:4096])
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    rows = [[_clean(cell) for cell in row] for row in reader]
    rows = [row for row in rows if any(cell for cell in row)]
    if not rows:
        return [], []
    headers = rows[0]
    width = len(headers)
    body = [row[:width] + [""] * (width - len(row)) for row in rows[1:]]
    return headers, body


def _xlsx_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        payload = archive.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    root = ET.fromstring(payload)
    namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    strings = []
    for item in root.findall(f"{namespace}si"):
        strings.append("".join(node.text or "" for node in item.iter(f"{namespace}t")))
    return strings


def _xlsx_sheet_names(archive: zipfile.ZipFile) -> list[str]:
    try:
        payload = archive.read("xl/workbook.xml")
    except KeyError:
        return []
    root = ET.fromstring(payload)
    namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    return [sheet.attrib.get("name", "Sheet1") for sheet in root.iter(f"{namespace}sheet")]


def _xlsx_cell_value(cell, shared: list[str]) -> str:
    namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    cell_type = cell.attrib.get("t", "")
    if cell_type == "inlineStr":
        item = cell.find(f"{namespace}is")
        if item is None:
            return ""
        return "".join(node.text or "" for node in item.iter(f"{namespace}t"))
    value = cell.find(f"{namespace}v")
    if value is None or value.text is None:
        return ""
    text = value.text.strip()
    if cell_type == "s":
        try:
            return shared[int(text)]
        except (ValueError, IndexError):
            return ""
    if cell_type == "b":
        return "TRUE" if text == "1" else "FALSE"
    try:
        number = float(text)
    except ValueError:
        return text
    if 20000 < number < 80000:
        try:
            return (date(1899, 12, 30) + timedelta(days=int(number))).isoformat()
        except (OverflowError, ValueError):
            return text
    if number.is_integer():
        return str(int(number))
    return str(number)


def parse_xlsx(content: bytes) -> tuple[list[str], list[list[str]]]:
    """Read the first worksheet of an .xlsx file without extra dependencies."""
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        shared = _xlsx_shared_strings(archive)
        names = sorted(
            name for name in archive.namelist()
            if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
        )
        if not names:
            return [], []
        payload = archive.read(names[0])
    root = ET.fromstring(payload)
    namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    rows: list[list[str]] = []
    for row in root.iter(f"{namespace}row"):
        values: dict[int, str] = {}
        for cell in row.findall(f"{namespace}c"):
            reference = cell.attrib.get("r", "")
            column = 0
            for char in reference:
                if char.isalpha():
                    column = column * 26 + (ord(char.upper()) - 64)
                else:
                    break
            values[max(column - 1, 0)] = _xlsx_cell_value(cell, shared)
        if not values:
            continue
        width = max(values) + 1
        rows.append([_clean(values.get(index, "")) for index in range(width)])
    rows = [row for row in rows if any(cell for cell in row)]
    if not rows:
        return [], []
    headers = rows[0]
    width = len(headers)
    body = [row[:width] + [""] * (width - len(row)) for row in rows[1:]]
    return headers, body


def parse_table(file_name: str, content: bytes) -> tuple[list[str], list[list[str]]]:
    """Parse a quote export into a header row and data rows."""
    if not content:
        raise ValueError("导入文件是空的。")
    if len(content) > MAX_FILE_BYTES:
        raise ValueError("文件超过 5MB，请分批导入或只保留需要的列。")
    suffix = file_name.lower().rsplit(".", 1)[-1] if "." in file_name else ""
    if suffix == "xlsx":
        try:
            return parse_xlsx(content)
        except zipfile.BadZipFile as exc:
            raise ValueError("这个 .xlsx 文件无法解析（可能不是真正的 Excel 文件）。") from exc
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = content.decode("gb18030")
        except UnicodeDecodeError as exc:
            raise ValueError("文件编码无法识别，请另存为 UTF-8 的 CSV。") from exc
    return parse_delimited(text)


def map_headers(headers: list[str]) -> dict[str, int]:
    """Map canonical fields to column indexes, first by exact name then by hint."""
    normalized = [_normalize_header(item) for item in headers]
    mapping: dict[str, int] = {}
    for field, aliases in FIELD_ALIASES.items():
        for alias in aliases:
            if alias in normalized:
                mapping[field] = normalized.index(alias)
                break
    for field, aliases in FIELD_ALIASES.items():
        if field in mapping:
            continue
        for index, header in enumerate(normalized):
            if not header:
                continue
            if any(alias in header for alias in aliases):
                mapping[field] = index
                break
    return mapping


def normalize_rows(
    headers: list[str],
    rows: list[list[str]],
    platform: str = "",
    default_currency: str = "",
) -> tuple[list[dict], list[str]]:
    """Turn raw rows into quote records, with a note for every skipped row."""
    mapping = map_headers(headers)
    warnings: list[str] = []
    records: list[dict] = []
    used_indexes = set(mapping.values())
    fallback_currency = default_currency or PLATFORM_CURRENCY.get(platform, "")

    for number, row in enumerate(rows, start=2):
        def cell(field: str) -> str:
            index = mapping.get(field)
            if index is None or index >= len(row):
                return ""
            return _clean(row[index])

        title = cell("title")
        unit_price = _to_float(cell("unitPrice"))
        tier_price = _to_float(cell("tierPrice"))
        if unit_price is None and tier_price is not None:
            unit_price = tier_price
        if not title and not cell("seller"):
            continue
        if not title:
            warnings.append(f"第 {number} 行没有商品名称，已跳过。")
            continue
        if unit_price is None:
            warnings.append(f"第 {number} 行读不出价格，已跳过（{title}）。")
            continue
        if unit_price <= 0:
            warnings.append(f"第 {number} 行价格不大于 0，已跳过（{title}）。")
            continue

        attributes: dict[str, str] = {}
        for index, header in enumerate(headers):
            if index in used_indexes:
                continue
            value = _clean(row[index]) if index < len(row) else ""
            if value:
                attributes[str(header).strip()[:60]] = value[:160]
        if tier_price is not None and tier_price != unit_price:
            attributes.setdefault("阶梯价", str(tier_price))

        records.append({
            "title": title[:240],
            "platform": cell("platform") or PLATFORM_NAMES.get(platform, platform) or "未标注平台",
            "seller": cell("seller"),
            "unitPrice": round(unit_price, 4),
            "currency": (cell("currency") or fallback_currency or "CNY").upper()[:8],
            "moq": _to_int(cell("moq")),
            "leadTimeDays": _to_int(cell("leadTime")),
            "spec": cell("spec"),
            "url": _normalize_url(cell("url")),
            "stock": cell("stock"),
            "observedAt": _to_date(cell("observedAt")),
            "note": cell("note"),
            "attributes": attributes,
            "rowNumber": number,
        })

    if not records and not warnings:
        warnings.append("文件里没有读到数据行。")
    return records, warnings


def pick_price(records: list[dict], rule: str = "lowest") -> dict | None:
    """Choose the record whose price enters the profit model."""
    if not records:
        return None
    priced = [item for item in records if item.get("unitPrice") not in (None, "")]
    if not priced:
        return None
    if rule == "highest":
        return max(priced, key=lambda item: float(item["unitPrice"]))
    if rule == "average":
        average = sum(float(item["unitPrice"]) for item in priced) / len(priced)
        return min(priced, key=lambda item: abs(float(item["unitPrice"]) - average))
    if rule == "median":
        ordered = sorted(priced, key=lambda item: float(item["unitPrice"]))
        return ordered[len(ordered) // 2]
    if rule == "first":
        return priced[0]
    return min(priced, key=lambda item: float(item["unitPrice"]))


def summarize(records: list[dict]) -> dict:
    """Small summary shown to the operator right after the import."""
    prices = [float(item["unitPrice"]) for item in records if item.get("unitPrice") not in (None, "")]
    sellers = {item.get("seller", "") for item in records if item.get("seller")}
    return {
        "rows": len(records),
        "sellers": len(sellers),
        "withMoq": sum(1 for item in records if item.get("moq")),
        "withLeadTime": sum(1 for item in records if item.get("leadTimeDays")),
        "lowest": min(prices) if prices else None,
        "highest": max(prices) if prices else None,
        "currencies": sorted({item.get("currency", "") for item in records if item.get("currency")}),
    }
