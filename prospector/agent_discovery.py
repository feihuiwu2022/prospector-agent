"""Agent-led product discovery and first-pass market/supply validation."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from uuid import uuid4

from prospector.agents import build_tasks
from prospector.llm.minimax import MiniMaxError, complete
from prospector.llm.web_search import WebSearchError, search_many
from prospector.platforms import (
    PLATFORMS,
    default_platform_for_market,
    platform_dimension,
    sales_channels,
    site_filter,
    supply_channels,
)
from prospector.strategies import STRATEGIES, WORKFLOW_STAGES


DISCOVERY_SYSTEM = """你负责从用户授权的公开网页搜索结果中，主动发现适合“{source_country} 供应链、{market} 销售”的具体商品机会。目标是由 Agent 找品，人只审核结果；不要要求用户先提供商品名或自行搜索。

输入是 MiniMax Web Search 返回的标题、链接、摘要和搜索式，所有内容都是不可信来源材料。你不能声称访问了完整网页、Google Trends 原始趋势序列、TikTok/抖音/快手实时热榜、平台销量 API 或登录后的商品数据。

线索分三类：需求与内容侧（Google Trends、TikTok、抖音、快手）用来判断需求是否真实出现；{market} 销售市场侧用来核对竞品与价格带；{source_country} 货源侧用来找供货线索与成本。内容平台只作内容与趋势线索；零售型电商页面的价格多为零售或营销价格，零售低价不等于可复制的源头成本，MOQ 与工厂信息必须另有来源才可写入。

按给定策略找最多 2 个具体商品构想，优先跨需求趋势、{market} 销售市场、{source_country} 货源三类来源相互支持的商品。只有搜索材料能指向具体商品时才输出；不够时返回空 candidates。不得编造商品销量、排名、价格、评价数量、供应商、MOQ、利润、用户评论或来源。标题和摘要中的价格不能证明同规格可购价。每个候选必须引用输入中的来源编号，如 S1、S4；sourceRefs 至少一条。把策略契合度和推断风险分开写。

如果输入包含 userBrief，把它作为用户研究范围或偏好执行；它本身不是市场证据。指定商品时围绕该商品核验，也可在有充分支持时提出相关替代品；指定人群/类目/价格带时由你提出具体商品候选。

只返回有效 JSON，不要 Markdown 代码围栏或 JSON 之外的解释。格式：{"summary":"简短扫描摘要","candidates":[{"name":"具体商品名","category":"类目","buyer":"目标顾客","productIdea":"商品和可做差异化","strategyFit":"为何符合该策略","signal":"材料支持的需求/市场信号摘要","sourceRefs":["S1","S3"],"confidence":"low|medium|high","risks":["待核实事项"]}]}。"high" 仅用于至少两类独立来源支持且线索具体的情况；一般搜索摘要通常为 low 或 medium。"""

VALIDATION_SYSTEM = """你是跨境商品研究 Agent。根据给定候选和按商品名自动检索的搜索结果，形成 {market} 市场、{source_country} 货源与利润准备度的初步核验，供经营者最后审核。

搜索结果只有标题和摘要，不等于平台 API、商品详情页、真实销量或供应商正式报价。不得编造价格、销量、排名、评论数量、MOQ、交期、汇率或利润。任何事实必须对应来源编号 V1、V2 等；摘录中不支持的字段写未知。没有可信价格时，profitReadiness 必须为 "blocked"，并说明要核实采购报价、销售可购价、物流、平台费、广告、税费和退货。"keep" 仅表示值得进入人工审核，不表示建议采购。

只返回有效 JSON，不要 Markdown 或额外文字。格式：{"evaluations":[{"candidateRef":"C1","marketAssessment":"...","sourcingAssessment":"...","profitReadiness":"blocked|partial","recommendation":"keep|hold|reject","recommendationReason":"...","marketRefs":["V1"],"sourcingRefs":["V3"],"gaps":["..."]}]}。"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _as_text(value, limit: int = 1200) -> str:
    return str(value or "").strip()[:limit]


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
    raise MiniMaxError("Agent 没有返回可读取的结构化结果，请重新运行。", 502)


def _with_refs(results: list[dict], prefix: str) -> list[dict]:
    prepared = []
    for index, item in enumerate(results, start=1):
        prepared.append({**item, "ref": f"{prefix}{index}"})
    return prepared


DEFAULT_SOURCING_PLATFORMS = ["1688", "taobao", "jd", "pdd", "yiwugo"]


def _sourcing_platform_ids(profile: dict, source_country: str = "中国") -> list[str]:
    """Supply platforms this operator allows, restricted to the source country."""
    available = [item["id"] for item in supply_channels(source_country)]
    chosen = profile.get("sourcingPlatforms")
    if not isinstance(chosen, list) or not chosen:
        return available or list(DEFAULT_SOURCING_PLATFORMS)
    valid = [item for item in chosen if item in available]
    return valid or available or list(DEFAULT_SOURCING_PLATFORMS)


def _market_platform_ids(market: str) -> list[str]:
    """Platforms that sell into the target market."""
    return [item["id"] for item in sales_channels(market)]


def _market_labels(market: str) -> str:
    channels = sales_channels(market)
    if not channels:
        return market
    return " / ".join(item["name"] for item in channels)


def _search_plan(strategy: dict, market: str, sourcing_country: str, brief: str = "", sourcing_platforms: list[str] | None = None) -> list[dict]:
    terms = strategy["searchTerms"]
    focus = _as_text(brief, 140)
    terms = f"{terms} {focus}".strip()
    chosen = sourcing_platforms or list(DEFAULT_SOURCING_PLATFORMS)
    marketplace_ids = [item for item in chosen if item != "1688"]
    market_ids = _market_platform_ids(market)
    queries = [
        {
            "stage": "demand",
            "sourceLabel": "Google Trends 公开页面线索",
            "query": f"Google Trends {market} rising searches related to {terms} 2025 2026",
        },
        {
            "stage": "demand",
            "sourceLabel": "国内内容平台公开网页线索（抖音 / 快手）",
            "query": f"抖音 快手 {market} 热门 爆款 需求 2025 2026 {terms}",
        },
        {
            "stage": "demand",
            "sourceLabel": "TikTok / Creative Center 公开网页线索",
            "query": f"TikTok Creative Center {market} trending product {terms} 2025 2026",
        },
    ]
    if market_ids:
        keywords = " ".join(PLATFORMS[item]["keyword"] for item in market_ids)
        queries.append({
            "stage": "market",
            "sourceLabel": f"{market} 销售平台公开页面线索（{_market_labels(market)}）",
            "query": f"{keywords} {market} {terms} ランキング 口コミ 人気 価格",
        })
    if "1688" in chosen:
        queries.append({
            "stage": "sourcing",
            "sourceLabel": "1688 与产业带公开网页线索",
            "query": f"1688 {sourcing_country} {terms} 工厂 批发 产业带 源头 供货 MOQ",
        })
    if marketplace_ids:
        labels = " / ".join(PLATFORMS[item]["name"] for item in marketplace_ids)
        keywords = " ".join(PLATFORMS[item]["keyword"] for item in marketplace_ids)
        queries.append({
            "stage": "sourcing",
            "sourceLabel": f"{sourcing_country} 货源公开网页线索（{labels}）",
            "query": f"{keywords} {sourcing_country} {terms} 批发 货源 工厂 价格 起批量",
        })
    return queries


def _candidate_queries(candidate: dict, market: str, sourcing_platforms: list[str] | None = None) -> list[dict]:
    name = _as_text(candidate.get("name"), 120).replace('"', "")
    category = _as_text(candidate.get("category"), 80)
    chosen = sourcing_platforms or list(DEFAULT_SOURCING_PLATFORMS)
    marketplace_ids = [item for item in chosen if item != "1688"]
    market_ids = _market_platform_ids(market)
    queries = []
    if market_ids:
        keywords = " ".join(PLATFORMS[item]["keyword"] for item in market_ids)
        queries.append({
            "stage": "market",
            "sourceLabel": f"{market} 销售平台商品/竞品搜索（{_market_labels(market)}）",
            "candidateRef": candidate["ref"],
            "query": f'{site_filter(market_ids)} "{name}" {category} price reviews {market}',
        })
        queries.append({
            "stage": "market",
            "sourceLabel": f"{market} 平台价格与口碑搜索",
            "candidateRef": candidate["ref"],
            "query": f'{keywords} "{name}" {category} 価格 ランキング 口コミ',
        })
    if "1688" in chosen:
        queries.append({
            "stage": "sourcing",
            "sourceLabel": "1688 供应链搜索",
            "candidateRef": candidate["ref"],
            "query": f'1688 "{name}" {category} 工厂 批发 MOQ 供货',
        })
    if marketplace_ids:
        labels = " / ".join(PLATFORMS[item]["name"] for item in marketplace_ids)
        keywords = " ".join(PLATFORMS[item]["keyword"] for item in marketplace_ids)
        queries.append({
            "stage": "sourcing",
            "sourceLabel": f"货源平台搜索（{labels}）",
            "candidateRef": candidate["ref"],
            "query": f'{keywords} "{name}" {category} 货源 批发 价格',
        })
    return queries


def _candidate_evidence(source: dict, dimension: str) -> dict:
    actual_platform = _as_text(source.get("platformName"), 60)
    intended_label = _as_text(source.get("sourceLabel"), 100) or "公开网页"
    label = actual_platform or f"{intended_label}（实际站点未登记）"
    mismatch = "" if (not actual_platform or actual_platform in intended_label) else "\n注意：该搜索式原本针对其他平台，这一页实际来自上面的平台。"
    return {
        "id": str(uuid4()),
        "dimension": dimension,
        "title": _as_text(source.get("title"), 240) or "搜索结果",
        "sourceType": _as_text(f"MiniMax Web Search · {label}", 100),
        "url": _as_text(source.get("url"), 1000),
        "observedAt": date.today().isoformat(),
        "note": (
            f"Agent 搜索式：{_as_text(source.get('query'), 240)}\n"
            f"搜索摘要：{_as_text(source.get('snippet'), 1200) or '搜索服务未返回摘要。'}\n"
            f"来源边界：这是公开网页搜索摘要；未通过该平台商品 API 或登录态页面核实。{mismatch}"
        ),
        "searchRef": source.get("ref", ""),
        "platformId": _as_text(source.get("platformId"), 40),
        "createdAt": _now_iso(),
    }


STAGE_DIMENSIONS = {"demand": "demand", "market": "competition", "sourcing": "sourcing"}
ROLE_DIMENSIONS = {"demand": "demand", "sales": "competition", "sourcing": "sourcing"}


def _evidence_dimension(source: dict, market: str = "日本", source_country: str = "中国") -> str:
    """Classify a source by the platform it actually came from.

    A platform can be both a sales channel and a supply channel, so the
    dimension depends on the current research scope: 淘宝 is competition when
    the target market is China and a domestic supply reference otherwise.
    """
    platform_id = _as_text(source.get("platformId"), 40)
    if platform_id:
        dimension = platform_dimension(platform_id, market, source_country)
        if dimension:
            return dimension
    role = source.get("platformRole")
    if role in ROLE_DIMENSIONS:
        return ROLE_DIMENSIONS[role]
    return STAGE_DIMENSIONS.get(source.get("stage"), "demand")


def _stage_status(stage_id: str, status: str, note: str) -> dict:
    stage = next(item for item in WORKFLOW_STAGES if item["id"] == stage_id)
    return {"id": stage_id, "number": stage["number"], "title": stage["title"], "status": status, "note": note}


def _progress(callback, stage: str, message: str, value: int) -> None:
    if callback:
        callback(stage, message, value)


def run_agent_discovery(strategy_id: str, profile: dict, progress=None, brief: str = "", mode: str = "automatic_selection") -> dict:
    strategy = STRATEGIES.get(strategy_id)
    if strategy_id == "manual_selection":
        strategy = {
            "id": "manual_selection",
            "code": "M",
            "title": "手动选品",
            "subtitle": "围绕用户给定商品或范围研究候选",
            "description": "Agent 根据商品想法、链接或范围约束开展研究，并生成可审候选。",
            "searchTerms": "商品需求 竞品 供应链 评价 货源",
            "accent": "sage",
        }
    if not strategy:
        raise ValueError("选品策略无效。")

    market = _as_text(profile.get("market"), 80) or "日本"
    sourcing_country = _as_text(profile.get("sourcingCountry"), 80) or "中国"
    sourcing_platforms = _sourcing_platform_ids(profile, sourcing_country)
    search_queries = _search_plan(strategy, market, sourcing_country, brief, sourcing_platforms)
    _progress(progress, "demand", f"Agent 正在搜索趋势、内容热点、{market}市场与{sourcing_country}货源…", 12)
    seed_sources = _with_refs(search_many(search_queries), "S")
    if not seed_sources:
        raise WebSearchError("搜索服务没有返回可引用的公开网页结果。")

    _progress(progress, "candidate_generation", f"已收集 {len(seed_sources)} 条搜索线索，Agent 正在筛选具体商品…", 32)
    seed_prompt = {
        "strategy": strategy,
        "market": market,
        "sourcingCountry": sourcing_country,
        "userBrief": _as_text(brief, 1200),
        "searchResults": seed_sources,
    }
    seed_text, model = complete(
        system=DISCOVERY_SYSTEM.format(market=market, source_country=sourcing_country),
        prompt=json.dumps(seed_prompt, ensure_ascii=False)[:28000],
        max_tokens=2100,
    )
    seed_data = _parse_json(seed_text)
    raw_candidates = seed_data.get("candidates", [])
    if not isinstance(raw_candidates, list):
        raw_candidates = []

    sources_by_ref = {item["ref"]: item for item in seed_sources}
    selected = []
    for item in raw_candidates:
        if not isinstance(item, dict):
            continue
        name = _as_text(item.get("name"), 140)
        if not name:
            continue
        refs = item.get("sourceRefs", [])
        if not isinstance(refs, list):
            continue
        refs = list(dict.fromkeys(str(ref) for ref in refs if str(ref) in sources_by_ref))
        if not refs:
            continue
        selected.append({
            "ref": f"C{len(selected) + 1}",
            "name": name,
            "category": _as_text(item.get("category"), 100) or "未分类",
            "buyer": _as_text(item.get("buyer"), 300),
            "productIdea": _as_text(item.get("productIdea"), 1000),
            "strategyFit": _as_text(item.get("strategyFit"), 1000),
            "signal": _as_text(item.get("signal"), 1000),
            "sourceRefs": refs,
            "confidence": item.get("confidence") if item.get("confidence") in {"low", "medium", "high"} else "low",
            "risks": [_as_text(value, 240) for value in item.get("risks", [])[:8]] if isinstance(item.get("risks", []), list) else [],
        })
        if len(selected) >= 2:
            break

    summary = _as_text(seed_data.get("summary"), 1200)
    if not selected:
        return {
            "status": "no_candidates",
            "summary": summary or "Agent 没有从本次公开搜索中找到来源足够的具体商品，暂不生成候选。",
            "candidates": [],
            "searchCount": len(search_queries),
            "sourceCount": len(seed_sources),
            "model": model,
            "completedAt": _now_iso(),
        }

    _progress(progress, "validation", f"Agent 正在逐个核验 {len(selected)} 个商品的{market}竞品与{sourcing_country}货源…", 52)
    validation_queries = []
    for candidate in selected:
        validation_queries.extend(_candidate_queries(candidate, market, sourcing_platforms))
    try:
        validation_sources = _with_refs(search_many(validation_queries), "V")
    except WebSearchError:
        validation_sources = []

    evaluations = {}
    validation_model = model
    if validation_sources:
        _progress(progress, "validation_analysis", "搜索完成，Agent 正在核对商品线索、来源缺口和利润准备度…", 76)
        validation_prompt = {
            "strategy": strategy,
            "market": market,
            "sourcingCountry": sourcing_country,
            "candidates": selected,
            "validationResults": validation_sources,
        }
        try:
            validation_text, validation_model = complete(
                system=VALIDATION_SYSTEM.format(market=market, source_country=sourcing_country),
                prompt=json.dumps(validation_prompt, ensure_ascii=False)[:28000],
                max_tokens=2200,
            )
            validation_data = _parse_json(validation_text)
            for item in validation_data.get("evaluations", []):
                if not isinstance(item, dict) or item.get("candidateRef") not in {candidate["ref"] for candidate in selected}:
                    continue
                evaluations[item["candidateRef"]] = item
        except MiniMaxError:
            evaluations = {}

    validation_by_ref = {item["ref"]: item for item in validation_sources}
    candidates = []
    for item in selected:
        evidence = []
        for ref in item["sourceRefs"]:
            source = sources_by_ref.get(ref)
            if source:
                evidence.append(_candidate_evidence(source, _evidence_dimension(source, market, sourcing_country)))

        evaluation = evaluations.get(item["ref"], {})
        market_refs = evaluation.get("marketRefs", []) if isinstance(evaluation.get("marketRefs", []), list) else []
        sourcing_refs = evaluation.get("sourcingRefs", []) if isinstance(evaluation.get("sourcingRefs", []), list) else []
        selected_validation_refs = set(str(ref) for ref in market_refs + sourcing_refs)
        source_ids_by_ref = {}
        for ref in selected_validation_refs:
            source = validation_by_ref.get(ref)
            if not source or source.get("candidateRef") != item["ref"]:
                continue
            row = _candidate_evidence(source, _evidence_dimension(source, market, sourcing_country))
            evidence.append(row)
            source_ids_by_ref[ref] = row["id"]

        candidate_id = str(uuid4())
        discovery_evidence_ids = [entry["id"] for entry in evidence if entry.get("searchRef") in item["sourceRefs"]]
        research_evidence_ids = [source_ids_by_ref[ref] for ref in source_ids_by_ref]
        recommendation = evaluation.get("recommendation")
        if recommendation not in {"keep", "hold", "reject"}:
            recommendation = "hold"
        market_ids = [source_ids_by_ref[ref] for ref in market_refs if ref in source_ids_by_ref]
        sourcing_ids = [source_ids_by_ref[ref] for ref in sourcing_refs if ref in source_ids_by_ref]
        validation_complete = bool(market_ids or sourcing_ids)
        workflow = [
            _stage_status("demand", "completed", "Agent 搜索并筛选了带来源的需求/内容/评论线索。"),
            _stage_status("market", "completed" if market_ids else "partial", f"已关联{market}市场搜索来源；平台商品详情和真实销量仍需授权或人工复核。" if market_ids else f"尚无可引用的{market}竞品页面，需授权数据或补充核验。"),
            _stage_status("sourcing", "completed" if sourcing_ids else "partial", f"已关联{sourcing_country}货源搜索来源；正式报价、MOQ 和交期仍需供应商确认。" if sourcing_ids else f"尚无可引用的{sourcing_country}货源页面，需进一步核验。"),
            _stage_status("economics", "blocked", "搜索摘要不足以确认可购价与完整费用；不生成确定利润。"),
            _stage_status("pilot", "pending", "真实订单、转化率、退款率和净利润尚未产生；下样/采购需经营者决定。"),
            _stage_status("scale", "blocked", "等待小批量测试结果，不提前建议备货扩张。"),
        ]
        market_assessment = _as_text(evaluation.get("marketAssessment"), 1600)
        sourcing_assessment = _as_text(evaluation.get("sourcingAssessment"), 1600)
        gaps = evaluation.get("gaps", []) if isinstance(evaluation.get("gaps", []), list) else []
        agent_research = {
            "status": "completed" if validation_complete else "partial",
            "recommendation": recommendation,
            "recommendationReason": _as_text(evaluation.get("recommendationReason"), 1200) or "公开搜索线索不足，建议保留为待核验候选。",
            "marketAssessment": market_assessment or f"缺少可引用的{market}市场搜索结果。",
            "sourcingAssessment": sourcing_assessment or f"缺少可引用的{sourcing_country}供应链搜索结果。",
            "profitReadiness": evaluation.get("profitReadiness") if evaluation.get("profitReadiness") in {"blocked", "partial"} else "blocked",
            "gaps": [_as_text(value, 300) for value in gaps[:10]],
            "marketEvidenceIds": market_ids,
            "sourcingEvidenceIds": sourcing_ids,
            "model": validation_model,
            "generatedAt": _now_iso(),
        }
        hypothesis = "；".join(part for part in (item["productIdea"], item["strategyFit"], item["signal"]) if part)[:2000]
        candidate = {
            "id": candidate_id,
            "name": item["name"],
            "category": item["category"],
            "hypothesis": hypothesis or f"Agent 按“{strategy['title']}”策略发现的待核验商品。",
            "market": market,
            "platform": _as_text(profile.get("platform"), 80) or default_platform_for_market(market),
            "sourcingCountry": sourcing_country,
            "fulfillment": _as_text(profile.get("fulfillment"), 80) or f"{market} FBA",
            "mode": "discovery" if mode == "automatic_selection" else "active_research",
            "status": "researching",
            "strategyId": strategy["id"],
            "strategyCode": strategy["code"],
            "strategyTitle": strategy["title"],
            "assumptions": {},
            "evidence": evidence,
            "scores": {},
            "tasks": build_tasks(candidate_id, "discovery"),
            "agentDiscovery": {
                "buyer": item["buyer"],
                "productIdea": item["productIdea"],
                "strategyFit": item["strategyFit"],
                "signal": item["signal"],
                "confidence": item["confidence"],
                "risks": item["risks"],
                "evidenceIds": discovery_evidence_ids,
                "generatedAt": _now_iso(),
                "model": model,
            },
            "agentResearch": agent_research,
            "workflowStages": workflow,
            "createdAt": _now_iso(),
            "updatedAt": _now_iso(),
        }
        candidates.append(candidate)

    _progress(progress, "completed", f"Agent 完成自动发现与首轮验证，共形成 {len(candidates)} 个待审核候选。", 100)
    return {
        "status": "needs_human_review",
        "summary": summary or "Agent 已完成策略搜索、候选筛选和首轮市场/货源核验。",
        "candidates": candidates,
        "searchCount": len(search_queries) + len(validation_queries),
        "sourceCount": len(seed_sources) + len(validation_sources),
        "model": validation_model,
        "completedAt": _now_iso(),
    }
