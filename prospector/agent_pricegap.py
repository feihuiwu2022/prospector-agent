"""Same-spec price gap scanning across the platforms of one market.

The question this module answers is narrow and mechanical: for one product
model, what does each platform of the target market show, and how large is the
visible price gap *between the same configuration*?

Rules that keep the output honest:

- only pages that actually carry the searched model count as the product;
- only rows that expose the same configuration (memory + storage, colour when
  both sides state it) may be compared with each other;
- second-hand platforms are reported as price background, never as a gap;
- every price is a public web search snippet, so each row keeps its URL and
  observation date and stays marked as unverified;
- no fee, freight, duty or exchange rate is guessed.  The output is a gross
  gap with an explicit list of what still has to be filled in by a human.
"""

from __future__ import annotations

import re
import statistics
from datetime import date, datetime, timezone
from uuid import uuid4

from prospector.agents import build_tasks
from prospector.llm.web_search import WebSearchError, search_many
from prospector.strategies import WORKFLOW_STAGES
from prospector.platforms import (
    PLATFORMS,
    market_currency,
    sales_channels,
    site_filter,
    supply_channels,
)


PRICE_PATTERNS = (
    re.compile(r"(?:¥|￥|人民币|RMB|CNY)\s*([0-9][0-9,]*(?:\.\d+)?)"),
    re.compile(r"([0-9][0-9,]*(?:\.\d+)?)\s*元"),
    re.compile(r"([0-9][0-9,]{2,})\s*円"),
)

RAM_STORAGE = re.compile(r"(\d{1,3})\s*[Gg]\s*[Bb]?\s*(?:[/+＋]|\+)\s*(\d{1,4})\s*[Gg]\s*[Bb]?")
RAM_ONLY = re.compile(r"(\d{1,3})\s*[Gg]\s*[Bb]")
STORAGE_ONLY = re.compile(r"(\d{1,4})\s*[Gg]\s*[Bb]")
MODEL_CODE = re.compile(r"[A-Za-z]{1,6}\s?\d{1,3}\s?[A-Za-z]{0,3}")
COLORS = ("黑", "白", "金", "蓝", "银", "灰", "粉", "绿", "紫", "红", "青", "幻")

PRICE_WORDS_CNY = "价格 到手价 优惠 补贴 直播间"
PRICE_WORDS_JPY = "価格 最安値 セール 税込 セール価格"

# Words that suggest the visible price is a promotional price rather than the
# everyday one.  A gap that only exists between promo pages is not a business.
PROMO_KEYWORDS = (
    "补贴", "券后", "到手", "秒杀", "限时", "清仓", "尾货", "直播", "赠品",
    "立减", "直降", "折后", "百亿", "促销", "特价", "活动价", "秒杀价",
    "セール", "特売", "セール価格", "値引き", "限定",
)

MAX_PLATFORMS = 8


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _as_text(value, limit: int = 1200) -> str:
    return str(value or "").strip()[:limit]


def _normalize(text: str) -> str:
    return re.sub(r"[\s\-_/·・,，.。()（）]+", "", str(text or "")).casefold()


def _model_core(product: str) -> str:
    """The part of the product name that identifies the model.

    ``OPPO K13s 手机`` becomes ``k13s``; a product without a code keeps its
    normalized name and simply matches less strictly.
    """
    codes = MODEL_CODE.findall(product or "")
    if codes:
        return _normalize(codes[0])
    return _normalize(product)


def _model_confirmed(product: str, text: str) -> bool:
    core = _model_core(product)
    if not core:
        return False
    return core in _normalize(text)


def _prices(text: str, currency: str) -> list[float]:
    found: list[float] = []
    for pattern in PRICE_PATTERNS:
        for match in pattern.finditer(text or ""):
            raw = match.group(1).replace(",", "")
            try:
                value = float(raw)
            except ValueError:
                continue
            if value <= 0:
                continue
            if currency == "JPY" and value < 100:
                continue
            if currency == "CNY" and value < 10:
                continue
            found.append(value)
    return sorted(found)


def _config(text: str) -> dict:
    """Read memory and storage out of a snippet, conservatively."""
    match = RAM_STORAGE.search(text or "")
    if match:
        return {"ramGb": int(match.group(1)), "storageGb": int(match.group(2)), "configSource": "标题/摘要中的容量组合"}
    ram = RAM_ONLY.search(text or "")
    storage = STORAGE_ONLY.search(text or "")
    ram_gb = int(ram.group(1)) if ram else None
    storage_gb = int(storage.group(1)) if storage else None
    if ram_gb and storage_gb and storage_gb <= 1024 and ram_gb <= 64:
        return {"ramGb": ram_gb, "storageGb": storage_gb, "configSource": "摘要中分别出现的内存与容量"}
    return {"ramGb": ram_gb, "storageGb": storage_gb, "configSource": ""}


def _color(text: str) -> str:
    for color in COLORS:
        if color in (text or ""):
            return color
    return ""


def _config_key(ram_gb, storage_gb) -> str:
    if ram_gb and storage_gb:
        return f"{ram_gb}G+{storage_gb}G"
    if ram_gb:
        return f"{ram_gb}G"
    if storage_gb:
        return f"{storage_gb}G"
    return ""


def _seller_of(url: str) -> str:
    """Host of the page, used to tell stores apart on the same platform."""
    match = re.search(r"https?://([^/]+)", str(url or ""), flags=re.IGNORECASE)
    if not match:
        return ""
    host = match.group(1).lower()
    return host[4:] if host.startswith("www.") else host


def _promo_signals(text: str) -> list[str]:
    """Promotional wording found in a snippet, if any."""
    lowered = str(text or "").casefold()
    return [word for word in PROMO_KEYWORDS if word.casefold() in lowered]


def _platform_rows(product: str, platform: dict, results: list[dict], currency: str) -> list[dict]:
    rows = []
    for source in results:
        title = _as_text(source.get("title"), 240)
        snippet = _as_text(source.get("snippet"), 1200)
        text = f"{title} {snippet}"
        if not _model_confirmed(product, text):
            continue
        prices = _prices(text, currency)
        if not prices:
            continue
        config = _config(text)
        rows.append({
            "platformId": platform["id"],
            "platform": platform["name"],
            "title": title,
            "url": _as_text(source.get("url"), 1000),
            "observedAt": date.today().isoformat(),
            "snippet": snippet,
            "priceLow": prices[0],
            "priceHigh": prices[-1],
            "currency": currency,
            "ramGb": config["ramGb"],
            "storageGb": config["storageGb"],
            "configSource": config["configSource"],
            "configKey": _config_key(config["ramGb"], config["storageGb"]),
            "color": _color(text),
            "modelConfirmed": True,
            "secondHand": bool(platform.get("second_hand", False)),
            "seller": _seller_of(source.get("url", "")),
            "promoSignals": _promo_signals(text),
            "sourceRef": source.get("ref", ""),
            "searchQuery": _as_text(source.get("query"), 240),
        })
    return rows


def _search_plan(product: str, market: str, source_country: str, platform_ids: list[str]) -> list[dict]:
    currency = market_currency(market)
    words = PRICE_WORDS_CNY if currency == "CNY" else PRICE_WORDS_JPY
    queries = []
    for platform_id in platform_ids:
        platform = PLATFORMS.get(platform_id)
        if not platform:
            continue
        filters = site_filter([platform_id])
        prefix = f"{filters} " if filters else ""
        queries.append({
            "stage": "market",
            "sourceLabel": f"{platform['name']} 同品价格搜索",
            "query": f'{prefix}"{product}" {words}',
        })
    return queries


def _group_rows(rows: list[dict]) -> list[dict]:
    """Group rows by configuration; only complete configurations can be compared."""
    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(row["configKey"] or "未识别配置", []).append(row)

    result = []
    for key, items in groups.items():
        platforms = {item["platformId"] for item in items}
        first_hand = [item for item in items if not item["secondHand"]]
        reasons = []
        if not key or key == "未识别配置":
            reasons.append("摘要未给出内存/存储，无法确认同配置")
        if len(platforms) < 2:
            reasons.append("只有一个平台提供该配置的可见价格，无法形成平台差价")
        if not first_hand:
            reasons.append("只有二手/个人定价平台，不作为差价结论")
        comparable = not reasons and len(first_hand) >= 2
        caveats = []
        colors = {item["color"] for item in items if item["color"]}
        if len(colors) > 1:
            caveats.append("各平台可见颜色不同（" + "、".join(sorted(colors)) + "），差价可能含颜色因素，需确认是否为同一 SKU。")
        lowest = min(first_hand, key=lambda item: item["priceLow"]) if first_hand else None
        highest = max(first_hand, key=lambda item: item["priceLow"]) if first_hand else None
        for item in items:
            if item["secondHand"]:
                item["gapToLowest"] = 0.0
                item["gapPct"] = 0.0
                item["note"] = "二手/个人定价，只作价格背景，不参与差价结论。"
                continue
            if lowest and comparable and item["platformId"] != lowest["platformId"]:
                item["gapToLowest"] = round(item["priceLow"] - lowest["priceLow"], 2)
                item["gapPct"] = round((item["priceLow"] - lowest["priceLow"]) / lowest["priceLow"], 4) if lowest["priceLow"] else 0.0
            else:
                item["gapToLowest"] = 0.0
                item["gapPct"] = 0.0
        gross_gap = round(highest["priceLow"] - lowest["priceLow"], 2) if comparable and lowest and highest else 0.0
        gross_gap_pct = round(gross_gap / lowest["priceLow"], 4) if comparable and lowest and lowest["priceLow"] else 0.0

        # The same gap without promotional wording: a price gap that only
        # exists between subsidy pages is not a repeatable business.
        durable_by_platform: dict[str, dict] = {}
        for item in first_hand:
            if item.get("promoSignals"):
                continue
            current = durable_by_platform.get(item["platformId"])
            if current is None or item["priceLow"] < current["priceLow"]:
                durable_by_platform[item["platformId"]] = item
        durable_gap = None
        durable_gap_pct = None
        if len(durable_by_platform) >= 2:
            durable_values = [item["priceLow"] for item in durable_by_platform.values()]
            durable_gap = round(max(durable_values) - min(durable_values), 2)
            durable_gap_pct = round(durable_gap / min(durable_values), 4) if min(durable_values) else 0.0
        promo_platforms = sorted({item["platform"] for item in first_hand if item.get("promoSignals")})
        if promo_platforms and comparable:
            caveats.append("可见低价里包含疑似促销页（" + "、".join(promo_platforms) + "），差价可能不可持续。")

        # Store level pairs: which shop is cheapest and which shop is dearest.
        store_pairs = []
        by_platform: dict[str, dict] = {}
        for item in first_hand:
            current = by_platform.get(item["platformId"])
            if current is None or item["priceLow"] < current["priceLow"]:
                by_platform[item["platformId"]] = item
        platform_ids = list(by_platform)
        for index, left_id in enumerate(platform_ids):
            for right_id in platform_ids[index + 1 :]:
                left, right = by_platform[left_id], by_platform[right_id]
                source, target = (left, right) if left["priceLow"] <= right["priceLow"] else (right, left)
                store_pairs.append({
                    "sourcePlatform": source["platform"],
                    "sourceSeller": source.get("seller") or "未识别店铺",
                    "sourcePrice": source["priceLow"],
                    "sourcePromo": bool(source.get("promoSignals")),
                    "targetPlatform": target["platform"],
                    "targetSeller": target.get("seller") or "未识别店铺",
                    "targetPrice": target["priceLow"],
                    "targetPromo": bool(target.get("promoSignals")),
                    "gap": round(target["priceLow"] - source["priceLow"], 2),
                    "gapPct": round((target["priceLow"] - source["priceLow"]) / source["priceLow"], 4) if source["priceLow"] else 0.0,
                })
        store_pairs.sort(key=lambda row: row["gap"], reverse=True)
        sellers = []
        for item in items:
            seller = item.get("seller") or "未识别店铺"
            existing = next((row for row in sellers if row["seller"] == seller and row["platform"] == item["platform"]), None)
            if existing:
                existing["priceLow"] = min(existing["priceLow"], item["priceLow"])
                existing["listingCount"] += 1
            else:
                sellers.append({
                    "seller": seller,
                    "platform": item["platform"],
                    "priceLow": item["priceLow"],
                    "currency": item["currency"],
                    "listingCount": 1,
                    "secondHand": item["secondHand"],
                })
        sellers.sort(key=lambda row: row["priceLow"])
        result.append({
            "config": key,
            "comparable": comparable,
            "reasons": reasons,
            "caveats": caveats,
            "sellers": sellers,
            "storePairs": store_pairs,
            "durableGap": durable_gap,
            "durableGapPct": durable_gap_pct,
            "rows": sorted(items, key=lambda item: item["priceLow"]),
            "lowestPlatform": lowest["platform"] if lowest else "",
            "highestPlatform": highest["platform"] if highest else "",
            "arbDirection": f"{lowest['platform']} → {highest['platform']}" if comparable and lowest and highest else "",
            "grossGap": gross_gap,
            "grossGapPct": gross_gap_pct,
            "currency": items[0]["currency"] if items else market_currency(market),
        })
    result.sort(key=lambda item: (not item["comparable"], item["config"]))
    return result


def _opportunities(groups: list[dict], currency: str) -> list[dict]:
    """Turn comparable groups into actionable buy-low / sell-high pairs."""
    result = []
    for group in groups:
        if not group.get("comparable"):
            continue
        rows = [row for row in group["rows"] if not row["secondHand"]]
        if len(rows) < 2:
            continue
        source = min(rows, key=lambda row: row["priceLow"])
        target = max(rows, key=lambda row: row["priceLow"])
        if source["platformId"] == target["platformId"]:
            continue
        result.append({
            "config": group["config"],
            "currency": currency,
            "sourcePlatform": source["platform"],
            "sourcePlatformId": source["platformId"],
            "sourcePrice": source["priceLow"],
            "sourceUrl": source["url"],
            "sourceTitle": source["title"],
            "sourceColor": source.get("color", ""),
            "sourceSeller": source.get("seller", ""),
            "sourcePromo": bool(source.get("promoSignals")),
            "targetPlatform": target["platform"],
            "targetPlatformId": target["platformId"],
            "targetPrice": target["priceLow"],
            "targetUrl": target["url"],
            "targetTitle": target["title"],
            "targetColor": target.get("color", ""),
            "targetSeller": target.get("seller", ""),
            "grossGap": round(target["priceLow"] - source["priceLow"], 2),
            "grossGapPct": round((target["priceLow"] - source["priceLow"]) / source["priceLow"], 4) if source["priceLow"] else 0.0,
            "observedAt": date.today().isoformat(),
        })
    result.sort(key=lambda item: item["grossGap"], reverse=True)
    return result


def _cross_market_scan(product: str, source_country: str, progress=None) -> dict:
    """Look up the same model on the platforms of the source country.

    The two sides are priced in different currencies, so no gap is computed
    here: the exchange rate, inbound cost and duty are not on any page and
    stay for the operator to fill in.
    """
    channels = [item for item in supply_channels(source_country) if "sales" in item["roles"]]
    if not channels:
        return {"sourceCountry": source_country, "rows": [], "pairs": []}
    currency = market_currency(source_country)
    plan = _search_plan(product, source_country, source_country, [item["id"] for item in channels])
    if progress:
        progress("price_gap_search", f"正在搜索 {source_country} 来源平台的 {product} 报价…", 30)
    try:
        results = search_many(plan)
    except WebSearchError:
        results = []
    rows: list[dict] = []
    for platform in channels:
        platform_results = [item for item in results if item.get("platformId") == platform["id"]]
        if not platform_results:
            continue
        rows.extend(_platform_rows(product, platform, platform_results, currency))
    return {
        "sourceCountry": source_country,
        "currency": currency,
        "rows": sorted(rows, key=lambda row: row["priceLow"]),
        "pairs": [],
    }


def run_price_gap_scan(product: str, market: str = "中国", source_country: str = "中国", platform_ids: list[str] | None = None, progress=None) -> dict:
    """Compare one product model across the platforms of the target market."""
    product = _as_text(product, 160)
    if not product:
        raise ValueError("请填写要比较的商品名称或型号。")
    market = _as_text(market, 80) or "中国"
    source_country = _as_text(source_country, 80) or "中国"

    channels = sales_channels(market)
    if platform_ids:
        chosen = [item for item in channels if item["id"] in set(platform_ids)]
    else:
        chosen = channels
    if not chosen:
        raise ValueError(f"没有登记面向{market}的销售平台，无法比价。")
    chosen = chosen[:MAX_PLATFORMS]

    plan = _search_plan(product, market, source_country, [item["id"] for item in chosen])
    if progress:
        progress("price_gap_search", f"正在逐个平台搜索 {product} 的公开价格线索…", 20)
    try:
        results = search_many(plan)
    except WebSearchError:
        results = []
    if not results:
        return {
            "status": "no_results",
            "summary": f"没有在{market}的平台公开页面上检索到 {product} 的可见价格。",
            "scope": f"{product} · {market}",
            "groups": [],
            "platformSummary": [],
            "gaps": ["换一个更通用的商品名（例如型号不带空格）再试，或确认商品是否在该市场有公开页面。"],
            "evidence": [],
            "searchCount": len(plan),
            "sourceCount": 0,
            "generatedAt": _now_iso(),
        }

    if progress:
        progress("price_gap_compare", f"已收集 {len(results)} 条线索，正在按同配置分组并计算差价…", 60)
    rows: list[dict] = []
    for platform in chosen:
        platform_results = [item for item in results if item.get("platformId") == platform["id"]]
        if not platform_results:
            continue
        rows.extend(_platform_rows(product, platform, platform_results, market_currency(market)))

    groups = _group_rows(rows)
    comparable = [group for group in groups if group["comparable"]]

    platform_summary = []
    for platform in chosen:
        platform_rows = [row for row in rows if row["platformId"] == platform["id"]]
        if not platform_rows:
            platform_summary.append({
                "platform": platform["name"],
                "platformId": platform["id"],
                "status": "no_visible_price",
                "note": "公开搜索摘要没有出现可核对的型号与价格。",
            })
            continue
        best = min(platform_rows, key=lambda item: item["priceLow"])
        platform_summary.append({
            "platform": platform["name"],
            "platformId": platform["id"],
            "status": "found",
            "secondHand": best["secondHand"],
            "visiblePriceLow": best["priceLow"],
            "visiblePriceHigh": best["priceHigh"],
            "currency": best["currency"],
            "config": best["configKey"] or "配置未识别",
            "rowCount": len(platform_rows),
            "note": "价格为公开搜索摘要中的可见报价，未打开商品页核实。",
        })

    cross_border = None
    if source_country and source_country != market:
        cross_border = _cross_market_scan(product, source_country, progress=progress)
        cross_border["targetMarket"] = market
        cross_border["targetCurrency"] = market_currency(market)
        source_platform_names = list(dict.fromkeys(row["platform"] for row in cross_border.get("rows", [])))
        cross_border["note"] = (
            f"跨市场价差涉及币种、汇率、关税与头程，页面没有这些数字，因此不自动给出差价结论；"
            f"来源国的可比平台：{'、'.join(source_platform_names) or '未登记'}。"
        )
        # Pair the cheapest source-country price with every comparable target
        # group.  The numbers stay side by side; no conversion happens here.
        source_rows = [row for row in cross_border.get("rows", []) if not row["secondHand"]]
        pairs = []
        if source_rows:
            cheapest = min(source_rows, key=lambda row: row["priceLow"])
            for group in comparable:
                target_rows = [row for row in group["rows"] if not row["secondHand"]]
                if not target_rows:
                    continue
                dearest = max(target_rows, key=lambda row: row["priceLow"])
                pairs.append({
                    "config": group["config"],
                    "sourcePlatform": cheapest["platform"],
                    "sourcePrice": cheapest["priceLow"],
                    "sourceCurrency": cheapest["currency"],
                    "sourceUrl": cheapest["url"],
                    "sourceTitle": cheapest["title"],
                    "sourceColor": cheapest.get("color", ""),
                    "targetPlatform": dearest["platform"],
                    "targetPrice": dearest["priceLow"],
                    "targetCurrency": dearest["currency"],
                    "targetUrl": dearest["url"],
                    "targetTitle": dearest["title"],
                    "targetColor": dearest.get("color", ""),
                    "note": "两侧币种不同：需要人工填汇率、头程与关税后才能算贡献利润。",
                })
        cross_border["pairs"] = pairs

    gaps = [
        "差价是搜索摘要中的挂牌价，未扣平台佣金、广告、履约与退货预留，不能当作利润。",
        "同配置对比只覆盖摘要里写明内存与存储的行；配置未写明的页面已单独列出。",
        "最终利润需在候选详情页填入采购价、汇率、头程、税费、佣金与广告后重算。",
    ]
    if not comparable:
        gaps.insert(0, "没有找到两家以上平台的同配置可见价格，本轮不给差价结论。")

    summary = (
        f"在{market}的 {len(chosen)} 个平台搜索了 {product}："
        f"{len(comparable)} 组同配置可以对比，"
        f"最大毛差价 {max((group['grossGap'] for group in comparable), default=0):.0f} "
        f"{market_currency(market)}（未扣费用）。"
    )

    return {
        "status": "needs_manual_validation" if comparable else "no_comparable_rows",
        "summary": summary,
        "scope": f"{product} · {market} · {len(chosen)} 个平台",
        "currency": market_currency(market),
        "groups": groups,
        "opportunities": _opportunities(groups, market_currency(market)),
        "platformSummary": platform_summary,
        "crossBorder": cross_border,
        "gaps": gaps,
        "evidence": [
            {
                "ref": row["sourceRef"],
                "title": row["title"],
                "url": row["url"],
                "snippet": row["snippet"],
                "platform": row["platform"],
                "query": row["searchQuery"],
            }
            for group in groups
            for row in group["rows"]
        ],
        "searchCount": len(plan),
        "sourceCount": len(rows),
        "generatedAt": _now_iso(),
    }


def gap_history(history: list[dict], config: str, source_platform: str, target_platform: str) -> dict:
    """Historical gap between two platforms for one configuration.

    The question is whether today's gap is normal for this pair: a gap that
    is far below its own history is usually a temporary subsidy rather than
    an arbitrage.
    """
    by_date: dict[str, dict[str, float]] = {}
    for item in history:
        if item.get("config") != config or not item.get("price"):
            continue
        date_key = str(item.get("observedAt", ""))
        bucket = by_date.setdefault(date_key, {})
        platform = item.get("platform", "")
        if platform not in bucket or item["price"] < bucket[platform]:
            bucket[platform] = item["price"]
    series = []
    for date_key in sorted(by_date):
        bucket = by_date[date_key]
        if source_platform in bucket and target_platform in bucket:
            gap = round(bucket[target_platform] - bucket[source_platform], 2)
            series.append({
                "date": date_key,
                "sourcePrice": bucket[source_platform],
                "targetPrice": bucket[target_platform],
                "gap": gap,
                "gapPct": round(gap / bucket[source_platform], 4) if bucket[source_platform] else 0.0,
            })
    gaps = [row["gap"] for row in series]
    return {
        "series": series,
        "observations": len(series),
        "medianGap": round(statistics.median(gaps), 2) if gaps else None,
        "minGap": min(gaps) if gaps else None,
        "maxGap": max(gaps) if gaps else None,
    }


PRICE_GAP_COST_FIELDS = ("referralRate", "adCost", "fulfillmentCost", "returnReserveRate")


def _fees_for(platform_id: str, assumptions: dict) -> tuple[dict, list[str]]:
    """Fees of one platform: per-platform override first, defaults second."""
    defaults = {key: assumptions.get(key) for key in PRICE_GAP_COST_FIELDS}
    overrides = (assumptions.get("byPlatform") or {}).get(platform_id) or {}
    merged = {key: overrides.get(key, defaults.get(key)) for key in PRICE_GAP_COST_FIELDS}
    missing = [key for key in PRICE_GAP_COST_FIELDS if merged.get(key) in (None, "")]
    return merged, missing


def _purchase_scenarios(rows_in_group: list[dict]) -> list[dict]:
    """Three honest purchase-price bases for the same configuration.

    The cheapest visible price, the cheapest price without promotional
    wording, and the median of the platform minima.  A business case built on
    the first number alone is usually a subsidy case.
    """
    platform_minima = {}
    for row in rows_in_group:
        current = platform_minima.get(row["platformId"])
        if current is None or row["priceLow"] < current["priceLow"]:
            platform_minima[row["platformId"]] = row
    if not platform_minima:
        return []
    values = sorted(row["priceLow"] for row in platform_minima.values())
    median = statistics.median(values)
    non_promo = [row["priceLow"] for row in platform_minima.values() if not row.get("promoSignals")]
    scenarios = [{
        "key": "lowest_visible",
        "label": "最低可见价",
        "purchasePrice": values[0],
        "note": "各平台同配置最低可见价，可能来自补贴或清仓。",
    }]
    if non_promo:
        scenarios.append({
            "key": "lowest_regular",
            "label": "去除促销后的最低价",
            "purchasePrice": min(non_promo),
            "note": "只用没有促销措辞的页面，更接近可持续拿货价。",
        })
    scenarios.append({
        "key": "median",
        "label": "同配置中位价",
        "purchasePrice": round(median, 2),
        "note": "各平台最低价的中位数，作为保守基准。",
    })
    return scenarios


def compute_platform_profit(groups: list[dict], assumptions: dict, currency: str) -> dict:
    """Per selling platform: what a unit would earn on several purchase bases.

    Every fee has to come from the operator; when a fee is missing the row
    says so instead of assuming zero.  Fees may differ per platform, so a
    platform can override the default assumption.
    """
    rows = []
    incomplete = []
    for group in groups:
        if not group.get("comparable"):
            continue
        config = group["config"]
        rows_in_group = [row for row in group["rows"] if not row["secondHand"]]
        if len(rows_in_group) < 2:
            continue
        purchase_row = min(rows_in_group, key=lambda row: row["priceLow"])
        scenarios = _purchase_scenarios(rows_in_group)
        for platform_id in {row["platformId"] for row in rows_in_group}:
            platform_rows = [row for row in rows_in_group if row["platformId"] == platform_id]
            sell_row = min(platform_rows, key=lambda row: row["priceLow"])
            if sell_row["platformId"] == purchase_row["platformId"]:
                continue
            fees, missing = _fees_for(platform_id, assumptions)
            row = {
                "config": config,
                "platform": sell_row["platform"],
                "platformId": platform_id,
                "sellPrice": sell_row["priceLow"],
                "purchasePrice": purchase_row["priceLow"],
                "purchasePlatform": purchase_row["platform"],
                "purchaseSeller": purchase_row.get("seller", ""),
                "grossGap": round(sell_row["priceLow"] - purchase_row["priceLow"], 2),
                "currency": currency,
                "promo": bool(sell_row.get("promoSignals")),
                "missingFields": missing,
                "contributionProfit": None,
                "contributionMargin": None,
                "scenarios": [],
            }
            for scenario in scenarios:
                entry = {
                    "key": scenario["key"],
                    "label": scenario["label"],
                    "purchasePrice": scenario["purchasePrice"],
                    "note": scenario["note"],
                    "grossGap": round(sell_row["priceLow"] - scenario["purchasePrice"], 2),
                    "missingFields": list(missing),
                    "contributionProfit": None,
                    "contributionMargin": None,
                }
                if not missing:
                    commission = sell_row["priceLow"] * float(fees["referralRate"])
                    returns = sell_row["priceLow"] * float(fees["returnReserveRate"])
                    ad_cost = float(fees["adCost"])
                    fulfillment = float(fees["fulfillmentCost"])
                    contribution = sell_row["priceLow"] - scenario["purchasePrice"] - commission - ad_cost - fulfillment - returns
                    entry["contributionProfit"] = round(contribution, 2)
                    entry["contributionMargin"] = round(contribution / sell_row["priceLow"], 4) if sell_row["priceLow"] else 0.0
                row["scenarios"].append(entry)
            primary = next((item for item in row["scenarios"] if item["key"] == "lowest_visible"), None)
            if primary and primary["contributionProfit"] is not None:
                row["contributionProfit"] = primary["contributionProfit"]
                row["contributionMargin"] = primary["contributionMargin"]
            if missing:
                incomplete.extend(missing)
            rows.append(row)
    rows.sort(key=lambda item: item.get("contributionProfit") if item.get("contributionProfit") is not None else item["grossGap"], reverse=True)
    return {
        "rows": rows,
        "missingFields": list(dict.fromkeys(incomplete)),
        "complete": not incomplete,
        "note": (
            "每行按三种采购价基准计算：最低可见价、去促销后的最低价、同配置中位价；"
            "费用来自经营画像里的比价假设（可按平台单独设置），未填时不给利润。"
        ),
    }


PRICE_ALERT_THRESHOLD = 0.05


def summarize_price_history(history: list[dict]) -> dict:
    """Turn raw observations into per-platform series and change alerts.

    An observation is what a public page showed on a date.  A drop is a
    signal to look again, never a reason to buy.
    """
    grouped: dict[tuple, list[dict]] = {}
    for item in history:
        key = (item.get("platform", ""), item.get("config", ""))
        grouped.setdefault(key, []).append(item)
    series = []
    alerts = []
    for (platform, config), items in grouped.items():
        ordered = sorted(items, key=lambda row: (str(row.get("observedAt", "")), str(row.get("recordedAt", ""))))
        latest = ordered[-1]
        previous = ordered[-2] if len(ordered) > 1 else None
        change_pct = None
        if previous and previous.get("price") and latest.get("price"):
            change_pct = round((latest["price"] - previous["price"]) / previous["price"], 4)
        row = {
            "platform": platform,
            "config": config,
            "latestPrice": latest.get("price"),
            "latestCurrency": latest.get("currency", ""),
            "observedAt": latest.get("observedAt", ""),
            "previousPrice": previous.get("price") if previous else None,
            "changePct": change_pct,
            "observations": len(ordered),
            "prices": [
                {"price": item.get("price"), "observedAt": item.get("observedAt", ""), "url": item.get("url", "")}
                for item in ordered[-12:]
            ],
        }
        series.append(row)
        if change_pct is not None and abs(change_pct) >= PRICE_ALERT_THRESHOLD:
            alerts.append({
                **row,
                "direction": "降价" if change_pct < 0 else "涨价",
                "note": (
                    f"{platform} 同配置报价较上次{('下降' if change_pct < 0 else '上升')} "
                    f"{abs(change_pct) * 100:.1f}%，需要打开页面确认是否为同款、是否伴随补贴或限时活动。"
                ),
            })
    series.sort(key=lambda row: (row["platform"], row["config"]))
    return {"series": series, "alerts": alerts, "threshold": PRICE_ALERT_THRESHOLD}


def merge_purchase_checklist(existing: list, updates: list) -> list:
    """Apply the operator's ticks to the stored checklist.

    Only the ``done`` flag comes from the client; the wording stays in the
    server's copy so the client cannot rewrite the checklist itself.
    """
    done_ids = {
        str(item.get("id"))
        for item in updates
        if isinstance(item, dict) and item.get("id") and item.get("done")
    }
    merged = []
    for item in existing:
        row = dict(item)
        row["done"] = str(row.get("id", "")) in done_ids
        merged.append(row)
    return merged


def advance_candidate_workflow(candidate: dict, thresholds: dict | None = None) -> dict:
    """Move the six stages forward when the operator finished the to-dos.

    The sourcing stage only completes when every purchase to-do is confirmed
    by a human; the economics stage only completes when the profit model has
    every input filled in; the pilot stage only completes when real order
    data was recorded, and the scale stage stays blocked until the pilot
    passed the thresholds.
    """
    from prospector.domain import calculate_profit, evaluate_pilot  # local import avoids a cycle

    stages = candidate.get("workflowStages") or []
    checklist = candidate.get("purchaseChecklist") or []
    open_items = [item for item in checklist if item.get("id") != "price_evidence" and not item.get("done")]
    profit = calculate_profit(candidate.get("assumptions", {}))
    pilot = evaluate_pilot(candidate.get("pilot") or {}, thresholds)
    timeline = candidate.get("timeline") or []
    for stage in stages:
        before = stage.get("status")
        if stage.get("id") == "sourcing":
            if checklist and not open_items:
                stage["status"] = "completed"
                stage["note"] = "采购待办已由人工逐项确认（起订量、书面报价、资质、样品、时效、验货、到岸成本）。"
            else:
                stage["status"] = "partial"
                stage["note"] = f"还有 {len(open_items)} 项采购待办未确认；MOQ、交期与发票不能由公开页面推断。"
        elif stage.get("id") == "economics":
            if profit.get("complete"):
                stage["status"] = "completed"
                stage["note"] = "利润假设已填齐，按你填的费率与成本计算了单件贡献利润。"
            else:
                stage["status"] = "partial"
                stage["note"] = "费用或汇率未填齐，利润显示为待核验，不给出确定数字。"
        elif stage.get("id") == "pilot":
            if pilot.get("complete"):
                stage["status"] = "completed"
                stage["note"] = f"小测数据已录入（{pilot['verdict']}）：{('；'.join(pilot['findings'])) or '各项指标在门槛之内'}。"
            else:
                stage["status"] = "partial"
                stage["note"] = "还没有真实订单数据；缺 " + "、".join(pilot.get("missingFields", [])) + "，小测结论保持待补。"
        elif stage.get("id") == "scale":
            if pilot.get("complete") and pilot.get("verdict") == "达标":
                stage["status"] = "pending"
                stage["note"] = "小测达标，可以开始规划扩量；备货与预算仍需你确认。"
            else:
                stage["status"] = "blocked"
                stage["note"] = "等待小测达标后再谈扩量；当前不生成备货建议。"
        if stage.get("status") != before:
            timeline.append({
                "stageId": stage.get("id"),
                "title": stage.get("title"),
                "from": before,
                "to": stage.get("status"),
                "note": stage.get("note", ""),
                "at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            })
    candidate["workflowStages"] = stages
    candidate["timeline"] = timeline[-60:]
    return candidate


def build_purchase_checklist(product: str, spec: dict) -> list[dict]:
    """The sourcing to-dos behind a price gap.

    Nothing here is invented: every line is either a price we actually saw
    or an item a human still has to confirm with the seller.
    """
    checklist = [
        {
            "id": "price_evidence",
            "item": f"采购参考价 {spec['sourcePrice']} {spec['sourceCurrency']}（{spec['sourcePlatform']}"
                    + (f" · {spec.get('sourceSeller')}" if spec.get("sourceSeller") else "")
                    + "，公开搜索摘要可见价）",
            "status": "已有线索",
            "note": "未打开商品页核实，需确认是否含配件、赠品与店铺优惠。",
        },
        {
            "id": "moq",
            "item": "确认起订量与阶梯价",
            "status": "待确认",
            "note": "公开页面没有 MOQ 与阶梯报价，必须向卖家索取。",
        },
        {
            "id": "quote",
            "item": "索取书面报价与付款方式",
            "status": "待执行",
            "note": "口头价格不作为利润依据；需要可追溯的报价记录。",
        },
        {
            "id": "invoice",
            "item": "确认是否可开票与主体资质",
            "status": "待确认",
            "note": "跨境采购还要核对报关与进口主体要求。",
        },
        {
            "id": "sample",
            "item": "下样并核对型号/配置/颜色",
            "status": "待执行",
            "note": f"目标配置：{spec['config']}；同款不同颜色与套装会影响价差。",
        },
        {
            "id": "leadtime",
            "item": "确认发货地、时效与运费",
            "status": "待确认",
            "note": "到货时效直接决定小测窗口能否成立。",
        },
        {
            "id": "qc",
            "item": "验货清单（外观、配件、铭牌、序列号）",
            "status": "待执行",
            "note": "验货要求由你确定，系统不代设标准。",
        },
        {
            "id": "landed",
            "item": "核算到岸成本（头程、关税、破损率）",
            "status": "待确认",
            "note": "这些数字不在公开页面上，需要你补进利润模型。",
        },
    ]
    if spec.get("sourcePromo"):
        checklist.insert(1, {
            "id": "promo_check",
            "item": "复核低价是否为限时补贴或清仓价",
            "status": "待确认",
            "note": "促销价通常不可持续，直接按它备货风险很高。",
        })
    return checklist


def build_price_gap_candidates(product: str, result: dict, profile: dict) -> list[dict]:
    """Turn comparable price gaps into reviewable candidates.

    Two kinds of gap are handled: two platforms of the same market (same
    currency) and a source-country platform against a target-market platform
    (different currency).  In both cases the candidate starts with the
    observed prices only; every fee, rate and duty is left for the operator,
    so the profit model stays explicit and never guesses.
    """
    market = str(profile.get("market", "中国"))[:80]
    currency = result.get("currency", "CNY")
    specs = []
    for item in result.get("opportunities", [])[:2]:
        specs.append({
            "kind": "same_market",
            "config": item["config"],
            "sourcePlatform": item["sourcePlatform"],
            "sourcePlatformId": item["sourcePlatformId"],
            "sourcePrice": item["sourcePrice"],
            "sourceCurrency": currency,
            "sourceUrl": item.get("sourceUrl", ""),
            "sourceTitle": item.get("sourceTitle", ""),
            "sourceColor": item.get("sourceColor", ""),
            "targetPlatform": item["targetPlatform"],
            "targetPlatformId": item["targetPlatformId"],
            "targetPrice": item["targetPrice"],
            "targetCurrency": currency,
            "targetUrl": item.get("targetUrl", ""),
            "targetTitle": item.get("targetTitle", ""),
            "targetColor": item.get("targetColor", ""),
            "gapValue": item["grossGap"],
            "sourcePromo": bool(item.get("sourcePromo")),
            "gapText": f"同配置毛差价 {item['grossGap']} {currency}（{item['grossGapPct'] * 100:.1f}%）",
        })
    cross = (result.get("crossBorder") or {}).get("pairs") or []
    for pair in cross[:2]:
        specs.append({
            "kind": "cross_market",
            "config": pair["config"],
            "sourcePlatform": pair["sourcePlatform"],
            "sourcePlatformId": pair.get("sourcePlatformId", ""),
            "sourcePrice": pair["sourcePrice"],
            "sourceCurrency": pair["sourceCurrency"],
            "sourceUrl": pair.get("sourceUrl", ""),
            "sourceTitle": pair.get("sourceTitle", ""),
            "sourceColor": pair.get("sourceColor", ""),
            "targetPlatform": pair["targetPlatform"],
            "targetPlatformId": pair.get("targetPlatformId", ""),
            "targetPrice": pair["targetPrice"],
            "targetCurrency": pair["targetCurrency"],
            "targetUrl": pair.get("targetUrl", ""),
            "targetTitle": pair.get("targetTitle", ""),
            "targetColor": pair.get("targetColor", ""),
            "gapValue": None,
            "sourcePromo": False,
            "gapText": "两侧币种不同，需要人工填汇率、头程与关税后才能算贡献利润",
        })

    history = result.get("history", []) or []
    for spec in specs:
        spec["gapHistory"] = gap_history(history, spec["config"], spec["sourcePlatform"], spec["targetPlatform"])
        median = spec["gapHistory"].get("medianGap")
        current = spec.get("gapValue")
        spec["gapContext"] = ""
        if median and current is not None and median > 0:
            ratio = current / median
            if ratio < 0.7:
                spec["gapContext"] = f"本次差价 {current} 明显低于这对平台的历史中位差价 {median}，更可能是限时补贴或异常低价。"
            elif ratio > 1.3:
                spec["gapContext"] = f"本次差价 {current} 高于这对平台的历史中位差价 {median}，值得复核是否同款或页面口径不同。"

    assumptions = (profile.get("priceGapAssumptions") or {})
    try:
        platform_profit = compute_platform_profit(result.get("groups", []), assumptions, currency)
    except (TypeError, ValueError):
        platform_profit = {"rows": []}
    scenarios_by_key = {
        (row["config"], row["platformId"]): row for row in platform_profit.get("rows", [])
    }

    stages = {stage["id"]: stage for stage in WORKFLOW_STAGES}
    candidates = []
    for spec in specs:
        candidate_id = str(uuid4())
        observed = date.today().isoformat()
        cross_border = spec["kind"] == "cross_market"
        evidence = [
            {
                "id": str(uuid4()),
                "dimension": "sourcing",
                "title": f"{spec['sourcePlatform']} · {product} {spec['config']}",
                "sourceType": f"{spec['sourcePlatform']}（公开搜索摘要）",
                "url": spec["sourceUrl"],
                "observedAt": observed,
                "note": (
                    f"低价一侧：公开搜索摘要可见报价 {spec['sourcePrice']} {spec['sourceCurrency']}。"
                    "未打开商品页核实，配件、赠品与店铺优惠可能不同。"
                ),
                "createdAt": _now_iso(),
            },
            {
                "id": str(uuid4()),
                "dimension": "competition",
                "title": f"{spec['targetPlatform']} · {product} {spec['config']}",
                "sourceType": f"{spec['targetPlatform']}（公开搜索摘要）",
                "url": spec["targetUrl"],
                "observedAt": observed,
                "note": (
                    f"高价一侧：公开搜索摘要可见报价 {spec['targetPrice']} {spec['targetCurrency']}；{spec['gapText']}。"
                    "差价未扣佣金、广告、履约与退货，不是利润。"
                ),
                "createdAt": _now_iso(),
            },
        ]
        workflow = [
            {"id": "demand", "number": stages["demand"]["number"], "title": stages["demand"]["title"], "status": "pending", "note": "比价模式不判断需求规模；如要投入需另找需求证据。"},
            {"id": "market", "number": stages["market"]["number"], "title": stages["market"]["title"], "status": "partial", "note": f"已在{spec['targetPlatform']}找到同配置公开报价；价格与在售状态仍需打开页面核实。"},
            {"id": "sourcing", "number": stages["sourcing"]["number"], "title": stages["sourcing"]["title"], "status": "partial", "note": f"已在{spec['sourcePlatform']}找到同配置公开报价；是否可稳定采购、MOQ 与发票需另行确认。"},
            {"id": "economics", "number": stages["economics"]["number"], "title": stages["economics"]["title"], "status": "partial", "note": "两端报价已填入利润模型；" + ("汇率、头程与关税待填。" if cross_border else "平台佣金、广告、履约与退货预留待填。")},
            {"id": "pilot", "number": stages["pilot"]["number"], "title": stages["pilot"]["title"], "status": "pending", "note": "尚未产生真实订单；下样与采购需经营者决定。"},
            {"id": "scale", "number": stages["scale"]["number"], "title": stages["scale"]["title"], "status": "blocked", "note": "等待真实小测结果。"},
        ]
        candidates.append({
            "id": candidate_id,
            "name": f"{product} {spec['config']} · {'跨市场价差' if cross_border else '跨平台差价'}",
            "category": "其他",
            "hypothesis": (
                f"同配置 {product} {spec['config']} 在{spec['sourcePlatform']}与{spec['targetPlatform']}的公开报价："
                f"{spec['sourcePrice']} {spec['sourceCurrency']} 与 {spec['targetPrice']} {spec['targetCurrency']}；{spec['gapText']}。"
                "是否为真实利润取决于平台佣金、广告、履约、退货与是否可稳定拿到低价货源。"
            ),
            "market": market,
            "platform": spec["targetPlatform"],
            "sourcingCountry": str(profile.get("sourcingCountry", "中国"))[:80],
            "fulfillment": str(profile.get("fulfillment", "待确认"))[:80],
            "mode": "active_research",
            "status": "researching",
            "assumptions": {
                "sellingPrice": spec["targetPrice"],
                "sellingCurrency": spec["targetCurrency"],
                "purchasePrice": spec["sourcePrice"],
                "purchaseCurrency": spec["sourceCurrency"],
            },
            "evidence": evidence,
            "purchaseChecklist": build_purchase_checklist(product, spec),
            "priceScenarios": (scenarios_by_key.get((spec["config"], spec.get("targetPlatformId", ""))) or {}).get("scenarios", []),
            "priceGap": {
                "product": product,
                "sourcePlatform": spec["sourcePlatform"],
                "sourceTitle": spec["sourceTitle"] or f"{product} {spec['config']}",
                "sourcePrice": spec["sourcePrice"],
                "sourceCurrency": spec["sourceCurrency"],
                "sourceUrl": spec["sourceUrl"],
                "sourceVariant": spec["config"],
                "sourceColor": spec["sourceColor"],
                "sourceMarket": str(profile.get("sourcingCountry", "中国"))[:80],
                "targetPlatform": spec["targetPlatform"],
                "targetTitle": spec["targetTitle"] or f"{product} {spec['config']}",
                "targetPrice": spec["targetPrice"],
                "targetCurrency": spec["targetCurrency"],
                "targetUrl": spec["targetUrl"],
                "targetVariant": spec["config"],
                "targetColor": spec["targetColor"],
                "targetMarket": market,
                "brand": product.split(" ")[0] if product else "",
                "model": product,
                "observedAt": observed,
                "gapHistory": spec.get("gapHistory", {}),
                "gapContext": spec.get("gapContext", ""),
            },
            "scores": {},
            "tasks": build_tasks(candidate_id, "active_research"),
            "agentDiscovery": {
                "buyer": "",
                "productIdea": "",
                "strategyFit": "",
                "signal": (
                    f"{spec['sourcePlatform']} {spec['sourcePrice']} {spec['sourceCurrency']} → "
                    f"{spec['targetPlatform']} {spec['targetPrice']} {spec['targetCurrency']}"
                ),
                "confidence": "low",
                "risks": ["公开摘要价格未经页面核实", "同配置颜色或套装可能不同", "未包含佣金、广告、履约与退货成本"]
                + [
                    f"{alert['platform']} 同配置报价较上次{'下降' if alert.get('changePct', 0) < 0 else '上升'} {abs(alert.get('changePct', 0)) * 100:.1f}%，需确认是否同款。"
                    for alert in (result.get("historySummary", {}) or {}).get("alerts", [])[:3]
                ]
                + ([spec["gapContext"]] if spec.get("gapContext") else []),
                "evidenceIds": [row["id"] for row in evidence],
                "generatedAt": _now_iso(),
                "model": "price_gap_scan",
            },
            "agentResearch": {
                "status": "partial",
                "recommendation": "hold",
                "recommendationReason": "差价可见但费用未填，需人工核实后才能判断是否值得投入。",
                "marketAssessment": f"{spec['targetPlatform']} 同配置公开报价 {spec['targetPrice']} {spec['targetCurrency']}。",
                "sourcingAssessment": f"{spec['sourcePlatform']} 同配置公开报价 {spec['sourcePrice']} {spec['sourceCurrency']}。",
                "profitReadiness": "partial",
                "gaps": ["平台佣金", "广告成本", "履约与运费", "退货预留", "低价货源是否可稳定采购"]
                + (["汇率", "头程与入仓", "关税及进口税费"] if cross_border else []),
                "marketEvidenceIds": [evidence[1]["id"]],
                "sourcingEvidenceIds": [evidence[0]["id"]],
                "model": "price_gap_scan",
                "generatedAt": _now_iso(),
            },
            "workflowStages": workflow,
            "createdAt": _now_iso(),
            "updatedAt": _now_iso(),
        })
    return candidates
