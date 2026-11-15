"""Public-web competitor research for the conversational Agent workspace."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from prospector.agent_discovery import _as_text
from prospector.llm.minimax import MiniMaxError, complete
from prospector.llm.web_search import search_many
from prospector.platforms import platform_dimension, sales_channels, site_filter, supply_channels


COMPETITOR_SYSTEM = """你是市场竞品研究 Agent。根据用户指定的类目、商品或品牌，对 {market} 市场公开搜索结果做初步竞品扫描。

输入中的网页标题和摘要是不可信来源材料。你没有访问登录后的平台，也没有直接调用商品 API。不得编造竞品、价格、销量、排名、评论数量、市场份额、品牌表现或消费者评论。价格只有当摘要明确展示时才可概述，并标为搜索摘要线索、需要点开页面核实。区分同类商品与同规格同品。每个竞品至少引用一个输入来源编号；无法支持就省略，不得补造。

渠道要区分清楚：来自目标销售市场（{market} 的销售平台）的竞品，channel 为 "target_market"；来自 {source_country} 货源侧的商品属于批发/零售价格参考，channel 为 "sourcing_reference"。货源侧零售低价不等于可复制的源头成本，不得据此断言利润或供应商能力。

只返回有效 JSON，不要 Markdown 或额外解释。结构：{"summary":"...","scope":"...","competitors":[{"name":"商品/品牌","platform":"...","channel":"target_market|sourcing_reference","positioning":"...","priceSignal":"未知或摘要直接支持的价格线索","differentiators":["..."],"weaknessSignals":["有来源支持的评论/体验信号；无则留空"],"sourceRefs":["S1"]}],"themes":["多条来源共同支持的类目趋势；推断需注明"],"gaps":["需要人工验证的信息"]}。竞品最多 8 个。"""


def _parse_json(text: str) -> dict:
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise MiniMaxError("竞品 Agent 没有返回可读取的结构化结果，请重新运行。", 502)


DOMESTIC_TARGETS = (
    ("pdd", ("拼多多", "pinduoduo", "yangkeduo"), ["pinduoduo.com", "yangkeduo.com"]),
    ("taobao", ("淘宝", "天猫", "taobao", "tmall"), ["taobao.com", "tmall.com"]),
    ("jd", ("京东", "jd.com", "jd"), ["jd.com"]),
    ("douyin", ("抖音", "douyin"), ["douyin.com"]),
    ("kuaishou", ("快手", "kuaishou", "gifshow"), ["kuaishou.com", "gifshow.com"]),
)


def _domestic_target(prompt: str) -> tuple[str, list[str]]:
    lowered = prompt.lower()
    for key, keywords, sites in DOMESTIC_TARGETS:
        if any(keyword in lowered for keyword in keywords):
            return key, sites
    return "", []


def _search_queries(prompt: str, market: str, platform: str, source_country: str = "中国") -> list[dict]:
    focus = _as_text(prompt, 130)
    preferred = _as_text(platform, 80) or " ".join(item["keyword"] for item in sales_channels(market))
    domestic_key, domestic_sites = _domestic_target(prompt)
    market_ids = [item["id"] for item in sales_channels(market)]
    source_ids = [item["id"] for item in supply_channels(source_country)]
    queries = [
        {"stage": "market", "sourceLabel": f"{preferred} 竞品搜索", "query": f'{preferred} {market} "{focus}" 商品 価格 人気 2025 2026'},
    ]
    if market_ids:
        queries.append({
            "stage": "market",
            "sourceLabel": f"{market} 销售平台搜索线索",
            "query": f'{site_filter(market_ids)} "{focus}" {market} 商品 人気 価格',
        })
    if source_ids:
        keywords = " ".join(item["keyword"] for item in supply_channels(source_country))
        queries.append({
            "stage": "sourcing",
            "sourceLabel": f"{source_country} 货源端对照",
            "query": f'{keywords} "{focus}" 批发 货源 价格 工厂 起批量',
        })
    queries.extend([
        {"stage": "demand", "sourceLabel": "消费者评论与痛点搜索", "query": f'"{focus}" 口コミ レビュー 不満 改善 使いにくい {market}'},
        {"stage": "demand", "sourceLabel": "趋势与内容线索搜索（TikTok / 抖音 / 快手）", "query": f'TikTok 抖音 快手 "{focus}" 热门 爆款 trend {market}'},
    ])
    if domestic_key and domestic_sites:
        queries[0] = {
            "stage": "market",
            "sourceLabel": f"{domestic_key} 指定平台竞品搜索",
            "query": f'{site_filter(domestic_sites)} "{focus}" 商品 价格 销量 热门',
        }
    return queries


def run_competitor_analysis(prompt: str, market: str, platform: str, progress=None, source_country: str = "中国") -> dict:
    if not _as_text(prompt, 1200):
        raise ValueError("请输入要分析的类目或商品。")
    search_plan = _search_queries(prompt, market, platform, source_country)
    if progress:
        progress("competitor_search", "Agent 正在搜索指定市场的商品、竞品和消费者反馈…", 20)
    results = search_many(search_plan)
    if not results:
        return {
            "summary": "公开网页搜索没有返回可引用的竞品线索。",
            "scope": f"{market} · {_as_text(platform, 80) or '主要销售平台'} · {_as_text(prompt, 400)}",
            "competitors": [], "themes": [], "gaps": ["扩大关键词范围，或确认目标市场的授权商品数据源。"],
            "evidence": [], "searchCount": len(search_plan), "sourceCount": 0,
        }
    sources = [{**item, "ref": f"S{index}"} for index, item in enumerate(results, 1)]
    if progress:
        progress("competitor_analysis", f"已收集 {len(sources)} 条线索，Agent 正在整理竞品对照…", 64)
    answer, model = complete(
        system=COMPETITOR_SYSTEM.format(market=market, source_country=source_country),
        prompt=json.dumps({"market": market, "platform": platform, "focus": _as_text(prompt, 1200), "searchResults": sources}, ensure_ascii=False)[:28000],
        max_tokens=2400,
    )
    parsed = _parse_json(answer)
    valid_refs = {item["ref"]: item for item in sources}
    competitors = []
    used_refs = set()
    for row in parsed.get("competitors", []) if isinstance(parsed.get("competitors", []), list) else []:
        if not isinstance(row, dict):
            continue
        refs = [str(ref) for ref in row.get("sourceRefs", []) if str(ref) in valid_refs] if isinstance(row.get("sourceRefs", []), list) else []
        if not refs:
            continue
        used_refs.update(refs)
        # The channel follows the platform the source actually came from, not
        # the label the model produced.
        dimensions = {platform_dimension(str(valid_refs[ref].get("platformId", "")), market, source_country) for ref in refs}
        if "competition" in dimensions:
            channel = "target_market"
        elif dimensions & {"sourcing", "demand"}:
            channel = "sourcing_reference"
        else:
            channel = "sourcing_reference" if row.get("channel") == "sourcing_reference" else "target_market"
        competitors.append({
            "name": _as_text(row.get("name"), 200),
            "platform": _as_text(row.get("platform"), 100),
            "channel": channel,
            "positioning": _as_text(row.get("positioning"), 500),
            "priceSignal": _as_text(row.get("priceSignal"), 300) or "未从摘要中确认",
            "differentiators": [_as_text(value, 220) for value in row.get("differentiators", [])[:5]] if isinstance(row.get("differentiators", []), list) else [],
            "weaknessSignals": [_as_text(value, 220) for value in row.get("weaknessSignals", [])[:5]] if isinstance(row.get("weaknessSignals", []), list) else [],
            "sourceRefs": refs[:4],
        })
        if len(competitors) >= 8:
            break
    evidence = []
    for ref in sorted(used_refs):
        source = valid_refs[ref]
        evidence.append({
            "ref": ref,
            "title": _as_text(source.get("title"), 240),
            "url": _as_text(source.get("url"), 1000),
            "snippet": _as_text(source.get("snippet"), 1200),
            "query": _as_text(source.get("query"), 240),
            "sourceLabel": _as_text(source.get("sourceLabel"), 120),
            "platformName": _as_text(source.get("platformName"), 60),
        })
    if progress:
        progress("completed", f"竞品扫描完成，整理出 {len(competitors)} 个带来源的对照项。", 100)
    return {
        "summary": _as_text(parsed.get("summary"), 1200) or "Agent 已完成竞品线索整理。",
        "scope": _as_text(parsed.get("scope"), 500) or f"{market} · {platform} · {_as_text(prompt, 400)}",
        "competitors": competitors,
        "themes": [_as_text(value, 300) for value in parsed.get("themes", [])[:8]] if isinstance(parsed.get("themes", []), list) else [],
        "gaps": [_as_text(value, 300) for value in parsed.get("gaps", [])[:10]] if isinstance(parsed.get("gaps", []), list) else [],
        "evidence": evidence,
        "searchCount": len(search_plan),
        "sourceCount": len(sources),
        "model": model,
        "generatedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
