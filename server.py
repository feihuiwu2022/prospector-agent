#!/usr/bin/env python3
"""Run the local Prospector Agent web prototype."""

from __future__ import annotations

import json
import math
import mimetypes
import os
import re
from datetime import date, datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import urlparse, urlunparse
from uuid import uuid4

from prospector.agents import build_tasks, now_iso, refresh_task_statuses
from prospector.agent_discovery import _rejected_constraints, run_agent_discovery
from prospector.agent_competitors import run_competitor_analysis
from prospector.agent_pricegap import (
    advance_candidate_workflow,
    build_price_gap_candidates,
    compute_platform_profit,
    merge_purchase_checklist,
    run_price_gap_scan,
    summarize_price_history,
)
from prospector.agent_runtime import run_task
from prospector.connectors.registry import connector_status, platform_metadata
from prospector.platforms import MARKETS, default_platform_for_market, market_group_catalog, sales_channels, supply_markets
from prospector.domain import (
    EVIDENCE_DIMENSION_LABELS,
    evaluate_pilot,
    SCORE_EVIDENCE_DIMENSIONS,
    SCORE_LABELS,
    calculate_batch_plan,
    calculate_profit,
    calculate_scores,
    confidence_for,
    resource_fit,
)
from prospector.economics import estimate_cross_market_profit, estimate_currency_pair_profit
from prospector.matching import match_listings
from prospector.llm.minimax import MiniMaxError, provider_status as llm_provider_status
from prospector.llm.web_search import WebSearchError, search_status as web_search_status
from prospector.store import DEFAULT_PROFILE, JsonStore
from prospector.strategies import STRATEGIES, WORKFLOW_STAGES, strategy_catalog


ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / "public"
STORE = JsonStore(Path(os.environ.get("PROSPECTOR_DATA_PATH", ROOT / "data" / "prospector.json")))
CATEGORIES = ["家居收纳", "宠物用品", "园艺用品", "DIY 五金", "汽车内饰", "兴趣爱好", "其他"]
MODES = {"discovery", "active_research", "specified_product"}
EVIDENCE_DIMENSIONS = set(EVIDENCE_DIMENSION_LABELS)
SCORE_KEYS = set(SCORE_LABELS)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_json(handler: BaseHTTPRequestHandler) -> dict:
    try:
        length = int(handler.headers.get("Content-Length", "0"))
    except ValueError as exc:
        raise ValueError("请求长度无效。") from exc
    if length <= 0:
        return {}
    if length > 1_000_000:
        raise ValueError("请求内容过大。")
    try:
        value = json.loads(handler.rfile.read(length).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("请求必须是有效的 JSON。") from exc
    if not isinstance(value, dict):
        raise ValueError("请求内容必须是 JSON 对象。")
    return value


def find_candidate(data: dict, candidate_id: str) -> dict | None:
    return next((item for item in data["candidates"] if item.get("id") == candidate_id), None)


def candidate_view(candidate: dict, profile: dict) -> dict:
    result = dict(candidate)
    refresh_task_statuses(result)
    score_result = calculate_scores(result)
    result["scores"] = score_result["scores"]
    result["scoreStatus"] = {"complete": score_result["complete"], "missingScores": score_result["missingScores"]}
    result["confidence"] = confidence_for(result)
    result["profit"] = calculate_profit(result.get("assumptions", {}))
    result["batchPlan"] = calculate_batch_plan(result.get("assumptions", {}), result["profit"])
    result["pilot"] = result.get("pilot") or {}
    result["pilotReview"] = evaluate_pilot(result["pilot"], profile.get("pilotThresholds"))
    result["resourceFit"] = resource_fit(result, profile)
    return result


def parse_source_url(raw: str) -> str:
    parsed = urlparse(raw.strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("来源链接必须是 http 或 https URL。")
    return urlunparse(parsed)


def normalize_listing(raw: dict, side: str) -> dict:
    label = "采购平台" if side == "source" else "销售平台"
    title = str(raw.get("title", "")).strip()[:240]
    platform = str(raw.get("platform", "")).strip()[:80]
    market = str(raw.get("market", "")).strip()[:80]
    currency = str(raw.get("currency", "")).strip().upper()
    if not title or not platform or not market:
        raise ValueError(f"{label}商品需要填写平台、市场和商品标题。")
    if currency not in {"CNY", "JPY"}:
        raise ValueError(f"{label}币种目前只接受 CNY 或 JPY。")
    try:
        price = float(raw.get("price"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label}价格必须是有效数字。") from exc
    if not math.isfinite(price) or price <= 0:
        raise ValueError(f"{label}价格必须大于 0。")
    observed_at = str(raw.get("observedAt", "")).strip()
    try:
        date.fromisoformat(observed_at[:10])
    except ValueError as exc:
        raise ValueError(f"{label}采集日期必须使用 YYYY-MM-DD 格式。") from exc
    identifiers = raw.get("identifiers", {})
    attributes = raw.get("attributes", {})
    if not isinstance(identifiers, dict) or not isinstance(attributes, dict):
        raise ValueError(f"{label}商品标识和属性必须是对象。")
    return {
        "id": str(uuid4()),
        "side": side,
        "platform": platform,
        "market": market,
        "title": title,
        "url": parse_source_url(str(raw.get("url", ""))),
        "price": round(price, 4),
        "currency": currency,
        "observedAt": observed_at[:10],
        "brand": str(raw.get("brand", "")).strip()[:120],
        "model": str(raw.get("model", "")).strip()[:120],
        "variant": str(raw.get("variant", "")).strip()[:120],
        "color": str(raw.get("color", "")).strip()[:80],
        "size": str(raw.get("size", "")).strip()[:80],
        "packCount": str(raw.get("packCount", "")).strip()[:40],
        "identifiers": {str(key)[:30]: str(value)[:100] for key, value in identifiers.items() if value},
        "attributes": {str(key)[:60]: str(value)[:160] for key, value in attributes.items() if value},
        "createdAt": utc_now(),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "ProspectorAgent/0.1"

    def log_message(self, fmt: str, *args) -> None:
        # Keep local run output compact and avoid logging request bodies.
        super().log_message(fmt, *args)

    def send_json(self, status: int, value: dict) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self.handle_api_get(parsed.path)
            return
        self.serve_static(parsed.path)

    def do_POST(self) -> None:
        try:
            self.handle_api_post(urlparse(self.path).path, parse_json(self))
        except ValueError as exc:
            self.send_json(400, {"error": str(exc)})
        except Exception as exc:  # Keep unexpected errors useful to the local prototype.
            self.send_json(500, {"error": "服务器处理失败。", "detail": str(exc)})

    def do_PATCH(self) -> None:
        try:
            self.handle_api_patch(urlparse(self.path).path, parse_json(self))
        except ValueError as exc:
            self.send_json(400, {"error": str(exc)})
        except Exception as exc:
            self.send_json(500, {"error": "服务器处理失败。", "detail": str(exc)})

    def do_DELETE(self) -> None:
        match = re.fullmatch(r"/api/candidates/([\w-]+)", urlparse(self.path).path)
        if not match:
            self.send_json(404, {"error": "没有找到这个接口。"})
            return

        def archive(data):
            candidate = find_candidate(data, match.group(1))
            if not candidate:
                return None
            candidate["status"] = "archived"
            candidate["updatedAt"] = utc_now()
            return candidate_view(candidate, data.get("profile", DEFAULT_PROFILE))

        result = STORE.update(archive)
        self.send_json(200 if result else 404, result or {"error": "没有找到这个候选。"})

    def serve_static(self, path: str) -> None:
        relative = "index.html" if path in ("", "/") else path.lstrip("/")
        target = (PUBLIC / relative).resolve()
        if PUBLIC.resolve() not in target.parents and target != PUBLIC.resolve():
            self.send_json(404, {"error": "没有找到这个页面。"})
            return
        if not target.is_file():
            self.send_json(404, {"error": "没有找到这个页面。"})
            return
        body = target.read_bytes()
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in ("application/javascript", "application/json"):
            content_type += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_api_get(self, path: str) -> None:
        data = STORE.read()
        profile = data.get("profile", DEFAULT_PROFILE)
        if path == "/api/bootstrap":
            candidates = [candidate_view(item, profile) for item in data.get("candidates", [])]
            candidates.sort(key=lambda item: item.get("updatedAt", ""), reverse=True)
            self.send_json(200, {
                "candidates": candidates,
                "profile": profile,
                "scoreLabels": SCORE_LABELS,
                "evidenceDimensionLabels": EVIDENCE_DIMENSION_LABELS,
                "categories": CATEGORIES,
                "discoveryRuns": sorted(data.get("discoveryRuns", []), key=lambda item: item.get("createdAt", ""), reverse=True),
                "conversations": sorted(data.get("conversations", []), key=lambda item: item.get("updatedAt", ""), reverse=True),
                "priceObservations": data.get("priceObservations", [])[-300:],
                "suppliers": data.get("suppliers", []),
                "connectors": connector_status(),
                "platforms": platform_metadata(),
                "markets": MARKETS,
                "marketGroups": market_group_catalog(),
                "llmProvider": llm_provider_status(),
                "webSearch": web_search_status(),
                "discoveryStrategies": strategy_catalog(),
                "workflowStages": [dict(item) for item in WORKFLOW_STAGES],
                "modes": [
                    {"id": "automatic_selection", "label": "自动选品"},
                    {"id": "manual_selection", "label": "手动选品"},
                    {"id": "competitor_analysis", "label": "手动竞品分析"},
                    {"id": "price_gap", "label": "同品比价"},
                ],
                "providerStatus": "MiniMax Agent 可主动搜索公开网页并筛选商品；平台搜索摘要不等于官方 API、实时销量或可购报价。",
            })
            return
        if path == "/api/discovery":
            self.send_json(200, {
                "runs": sorted(data.get("discoveryRuns", []), key=lambda item: item.get("createdAt", ""), reverse=True),
                "connectors": connector_status(),
            })
            return
        match = re.fullmatch(r"/api/candidates/([\w-]+)", path)
        if match:
            candidate = find_candidate(data, match.group(1))
            self.send_json(200, candidate_view(candidate, profile) if candidate else {"error": "没有找到这个候选。"})
            if candidate:
                return
            self.wfile.flush()
            return
        self.send_json(404, {"error": "没有找到这个接口。"})

    def handle_api_post(self, path: str, body: dict) -> None:
        if path == "/api/agent/discovery":
            self.start_agent_discovery(body)
            return
        if path == "/api/agent/chat":
            self.start_agent_chat(body)
            return
        review_match = re.fullmatch(r"/api/candidates/([\w-]+)/review", path)
        if review_match:
            self.review_candidate(review_match.group(1), body)
            return
        if path == "/api/discovery":
            self.create_discovery_run(body)
            return
        if path == "/api/suppliers":
            self.upsert_supplier(body)
            return
        promote_match = re.fullmatch(r"/api/discovery/([\w-]+)/candidate", path)
        if promote_match:
            self.promote_discovery_run(promote_match.group(1))
            return
        task_match = re.fullmatch(r"/api/candidates/([\w-]+)/tasks/([\w-]+)/run", path)
        if task_match:
            self.run_agent_task(task_match.group(1), task_match.group(2))
            return
        if path != "/api/candidates":
            match = re.fullmatch(r"/api/candidates/([\w-]+)/(evidence|scores)", path)
            if not match:
                self.send_json(404, {"error": "没有找到这个接口。"})
                return
            if match.group(2) == "evidence":
                self.add_evidence(match.group(1), body)
            else:
                self.add_score(match.group(1), body)
            return
        mode = body.get("mode", "specified_product")
        if mode not in MODES:
            raise ValueError("研究模式无效。")
        category = str(body.get("category", "其他")).strip()[:80] or "其他"
        profile = STORE.read().get("profile", DEFAULT_PROFILE)
        candidate_market = str(body.get("market") or profile.get("market", "日本"))[:80]
        name = str(body.get("name", "")).strip()[:160]
        hypothesis = str(body.get("hypothesis", "")).strip()[:2000]
        if mode == "specified_product" and not name:
            raise ValueError("指定商品研究需要填写商品名称。")
        if mode == "active_research" and not (name or hypothesis):
            raise ValueError("主动研究请填写机会方向或需求描述。")
        if mode == "discovery" and not name:
            name = f"待发现机会：{category}"
        if not hypothesis and mode == "discovery":
            hypothesis = f"在 {category} 中查找有来源支持的{candidate_market}站需求机会。"
        if not name and hypothesis:
            name = hypothesis[:100]

        candidate_id = str(uuid4())
        candidate = {
            "id": candidate_id,
            "name": name,
            "category": category,
            "hypothesis": hypothesis,
            "market": candidate_market,
            "platform": str(body.get("platform") or profile.get("platform") or default_platform_for_market(candidate_market))[:80],
            "sourcingCountry": str(body.get("sourcingCountry", profile.get("sourcingCountry", "中国")))[:80],
            "fulfillment": str(body.get("fulfillment", profile.get("fulfillment", f"{candidate_market} FBA")))[:80],
            "mode": mode,
            "status": "researching",
            "assumptions": {},
            "evidence": [],
            "scores": {},
            "tasks": build_tasks(candidate_id, mode),
            "createdAt": utc_now(),
            "updatedAt": utc_now(),
        }
        saved = STORE.update(lambda data: data["candidates"].append(candidate) or candidate)
        self.send_json(201, candidate_view(saved, STORE.read().get("profile", DEFAULT_PROFILE)))

    def run_agent_task(self, candidate_id: str, task_id: str) -> None:
        provider = llm_provider_status()
        if not provider["configured"]:
            self.send_json(503, {"error": provider["message"]})
            return
        data = STORE.read()
        candidate = find_candidate(data, candidate_id)
        if not candidate:
            self.send_json(404, {"error": "没有找到这个候选。"})
            return
        task = next((item for item in candidate.get("tasks", []) if item.get("id") == task_id), None)
        if not task:
            self.send_json(404, {"error": "没有找到这个 Agent 任务。"})
            return
        try:
            result = run_task(candidate, task)
        except MiniMaxError as exc:
            self.send_json(exc.status, {"error": str(exc)})
            return

        result["createdAt"] = utc_now()
        result["status"] = "draft_review_required"

        def save(data):
            current = find_candidate(data, candidate_id)
            if not current:
                return None
            current_task = next((item for item in current.get("tasks", []) if item.get("id") == task_id), None)
            if not current_task:
                return None
            current_task["result"] = result
            current_task["updatedAt"] = utc_now()
            current["updatedAt"] = utc_now()
            return candidate_view(current, data.get("profile", DEFAULT_PROFILE))

        saved = STORE.update(save)
        self.send_json(200 if saved else 404, saved or {"error": "候选或 Agent 任务已被移除。"})

    def review_candidate(self, candidate_id: str, body: dict) -> None:
        decision = str(body.get("decision", "")).strip()
        if decision not in {"continue", "hold", "reject"}:
            raise ValueError("审核动作必须是继续研究、暂缓或淘汰。")
        reason = str(body.get("reason", "")).strip()[:600]

        def review(data):
            candidate = find_candidate(data, candidate_id)
            if not candidate:
                return None
            candidate["humanReview"] = {
                "decision": decision,
                "reason": reason,
                "reviewedAt": utc_now(),
                "reviewer": "local_user",
            }
            candidate["status"] = {"continue": "researching", "hold": "paused", "reject": "archived"}[decision]
            candidate["updatedAt"] = utc_now()
            return candidate_view(candidate, data.get("profile", DEFAULT_PROFILE))

        saved = STORE.update(review)
        self.send_json(200 if saved else 404, saved or {"error": "没有找到这个候选。"})

    def start_agent_discovery(self, body: dict) -> None:
        strategy_id = str(body.get("strategyId", "")).strip()
        strategy = STRATEGIES.get(strategy_id)
        if not strategy:
            raise ValueError("请选择有效的选品策略。")
        search = web_search_status()
        if not search.get("configured"):
            self.send_json(503, {"error": search["message"]})
            return

        run = {
            "id": str(uuid4()),
            "trigger": "agent_strategy_discovery",
            "strategyId": strategy_id,
            "title": f"{strategy['code']} · {strategy['title']}",
            "status": "running",
            "stage": "demand",
            "progress": 3,
            "message": "Agent 选品任务已启动。",
            "candidateIds": [],
            "createdAt": utc_now(),
            "updatedAt": utc_now(),
        }
        profile = STORE.read().get("profile", DEFAULT_PROFILE)
        STORE.update(lambda data: data.setdefault("discoveryRuns", []).append(run) or run)
        worker = Thread(target=self.execute_agent_discovery, args=(run["id"], strategy_id, profile), daemon=True)
        worker.start()
        self.send_json(202, run)

    def start_agent_chat(self, body: dict) -> None:
        mode = str(body.get("mode", "")).strip()
        if mode not in {"automatic_selection", "manual_selection", "competitor_analysis", "price_gap"}:
            raise ValueError("请选择自动选品、手动选品、手动竞品分析或同品比价模式。")
        search = web_search_status()
        if not search.get("configured"):
            self.send_json(503, {"error": search["message"]})
            return

        prompt = str(body.get("message", "")).strip()[:1200]
        strategy_id = str(body.get("strategyId", "")).strip()
        if mode == "automatic_selection" and strategy_id not in STRATEGIES:
            raise ValueError("自动选品请先选择 A-D 策略。")
        if mode != "automatic_selection" and not prompt:
            raise ValueError("请描述商品、目标范围或竞品分析对象。")

        base_profile = STORE.read().get("profile", DEFAULT_PROFILE)
        profile = {
            **base_profile,
            "market": str(body.get("market") or base_profile.get("market", "日本"))[:80],
            "platform": str(body.get("platform") or base_profile.get("platform") or default_platform_for_market(str(body.get("market") or base_profile.get("market", "日本"))))[:80],
            "sourcingCountry": str(body.get("sourcingCountry") or base_profile.get("sourcingCountry", "中国"))[:80],
        }
        requested_platforms = body.get("platformIds")
        if isinstance(requested_platforms, list) and requested_platforms:
            profile["platformIds"] = [str(item)[:40] for item in requested_platforms]
        conversation_id = str(body.get("conversationId", "")).strip()
        created_at = utc_now()
        run_id = str(uuid4())
        title = str(body.get("title", "")).strip()[:100]
        if not title:
            title = STRATEGIES[strategy_id]["title"] if mode == "automatic_selection" else (prompt[:72] or "Agent 研究")
        user_message = {"id": str(uuid4()), "role": "user", "content": prompt or f"按“{STRATEGIES[strategy_id]['title']}”策略自动选品。", "createdAt": created_at, "runId": run_id}
        run = {
            "id": run_id,
            "trigger": "chat_agent_run",
            "mode": mode,
            "strategyId": strategy_id or None,
            "conversationId": conversation_id or None,
            "title": title,
            "status": "running",
            "stage": "planning",
            "progress": 3,
            "message": "Agent 已接收任务，正在规划研究步骤。",
            "prompt": prompt,
            "candidateIds": [],
            "createdAt": created_at,
            "updatedAt": created_at,
        }

        def create(data):
            nonlocal conversation_id
            conversations = data.setdefault("conversations", [])
            conversation = next((item for item in conversations if item.get("id") == conversation_id), None) if conversation_id else None
            if conversation is None:
                conversation_id = str(uuid4())
                run["conversationId"] = conversation_id
                conversation = {
                    "id": conversation_id,
                    "mode": mode,
                    "title": title,
                    "market": profile["market"],
                    "platform": profile["platform"],
                    "messages": [],
                    "runIds": [],
                    "createdAt": created_at,
                    "updatedAt": created_at,
                }
                conversations.append(conversation)
            conversation["mode"] = mode
            conversation["market"] = profile["market"]
            conversation["platform"] = profile["platform"]
            conversation["updatedAt"] = created_at
            conversation.setdefault("messages", []).append(user_message)
            conversation.setdefault("runIds", []).append(run_id)
            data.setdefault("discoveryRuns", []).append(run)
        STORE.update(create)

        worker = Thread(
            target=self.execute_agent_chat,
            args=(run_id, conversation_id, mode, strategy_id, prompt, profile),
            daemon=True,
        )
        worker.start()
        self.send_json(202, {"run": run, "conversationId": conversation_id})

    def execute_agent_chat(self, run_id: str, conversation_id: str, mode: str, strategy_id: str, prompt: str, profile: dict) -> None:
        def update_progress(stage: str, message: str, progress: int) -> None:
            def update(data):
                run = next((item for item in data.get("discoveryRuns", []) if item.get("id") == run_id), None)
                if run and run.get("status") == "running":
                    run.update({"stage": stage, "message": message, "progress": progress, "updatedAt": utc_now()})
            STORE.update(update)

        try:
            if mode == "price_gap":
                result = run_price_gap_scan(
                    prompt,
                    profile.get("market", "中国"),
                    profile.get("sourcingCountry", "中国"),
                    platform_ids=profile.get("platformIds"),
                    progress=update_progress,
                )
                result["history"] = self._record_price_observations(prompt, result)
                result["historySummary"] = summarize_price_history(result["history"])
                result["platformProfit"] = compute_platform_profit(
                    result.get("groups", []),
                    profile.get("priceGapAssumptions", {}) or {},
                    result.get("currency", "CNY"),
                )
                candidates = build_price_gap_candidates(prompt, result, profile)
                final_status = result.get("status", "needs_manual_validation")
                summary = result.get("summary", "同品比价完成。")
            elif mode == "competitor_analysis":
                result = run_competitor_analysis(
                    prompt,
                    profile.get("market", "日本"),
                    profile.get("platform", ""),
                    update_progress,
                    source_country=profile.get("sourcingCountry", "中国"),
                )
                candidates = []
                final_status = "completed"
                summary = result.get("summary", "竞品扫描完成。")
            else:
                discovery_strategy = strategy_id if mode == "automatic_selection" else "manual_selection"
                avoid: list[str] = []
                if profile.get("avoidRejected"):
                    avoid = _rejected_constraints(STORE.read())
                result = run_agent_discovery(
                    discovery_strategy,
                    profile,
                    update_progress,
                    brief=prompt,
                    mode=mode,
                    avoid_constraints=avoid,
                )
                candidates = result.get("candidates", [])
                final_status = result.get("status", "no_candidates")
                summary = result.get("summary", "Agent 已完成商品研究。")

            def finish(data):
                run = next((item for item in data.get("discoveryRuns", []) if item.get("id") == run_id), None)
                conversation = next((item for item in data.get("conversations", []) if item.get("id") == conversation_id), None)
                if not run:
                    return
                candidate_ids = []
                for candidate in candidates:
                    candidate["discoveryRunId"] = run_id
                    candidate["conversationId"] = conversation_id
                    data.setdefault("candidates", []).append(candidate)
                    candidate_ids.append(candidate["id"])
                run.update({
                    "status": final_status,
                    "stage": "human_review" if final_status == "needs_human_review" else "completed",
                    "progress": 100,
                    "message": summary,
                    "summary": summary,
                    "candidateIds": candidate_ids,
                    "candidateCount": len(candidate_ids),
                    "searchCount": result.get("searchCount", 0),
                    "sourceCount": result.get("sourceCount", 0),
                    "model": result.get("model"),
                    "report": result if mode in {"competitor_analysis", "price_gap"} else None,
                    "completedAt": result.get("completedAt", result.get("generatedAt", utc_now())),
                    "updatedAt": utc_now(),
                })
                if conversation:
                    conversation.setdefault("messages", []).append({
                        "id": str(uuid4()),
                        "role": "assistant",
                        "content": summary,
                        "runId": run_id,
                        "createdAt": utc_now(),
                    })
                    conversation["updatedAt"] = utc_now()
            STORE.update(finish)
        except (MiniMaxError, WebSearchError, ValueError) as exc:
            failure_message = str(exc)
            self._fail_agent_chat(run_id, conversation_id, failure_message)
        except Exception:
            self._fail_agent_chat(run_id, conversation_id, "Agent 运行遇到内部错误，请检查服务并重试。")

    @staticmethod
    def _record_price_observations(product: str, result: dict) -> list[dict]:
        """Store the visible prices of this scan and return the price history.

        Observations are facts about what a public page showed on a given
        date.  They are never used as a price guarantee and never as a
        profit input.
        """
        rows = []
        for group in result.get("groups", []):
            for row in group.get("rows", []):
                rows.append({
                    "product": product,
                    "config": row.get("configKey", ""),
                    "platformId": row.get("platformId", ""),
                    "platform": row.get("platform", ""),
                    "price": row.get("priceLow"),
                    "currency": row.get("currency", ""),
                    "url": row.get("url", ""),
                    "observedAt": row.get("observedAt", ""),
                    "recordedAt": utc_now(),
                    "secondHand": bool(row.get("secondHand", False)),
                })
        for row in (result.get("crossBorder") or {}).get("rows", []):
            rows.append({
                "product": product,
                "config": row.get("configKey", ""),
                "platformId": row.get("platformId", ""),
                "platform": row.get("platform", ""),
                "price": row.get("priceLow"),
                "currency": row.get("currency", ""),
                "url": row.get("url", ""),
                "observedAt": row.get("observedAt", ""),
                "recordedAt": utc_now(),
                "secondHand": bool(row.get("secondHand", False)),
            })

        def save(data):
            observations = data.setdefault("priceObservations", [])
            observations.extend(rows)
            history = [item for item in observations if item.get("product") == product]
            return history[-60:]

        return STORE.update(save)

    @staticmethod
    def _fail_agent_chat(run_id: str, conversation_id: str, message: str) -> None:
        def fail(data):
            run = next((item for item in data.get("discoveryRuns", []) if item.get("id") == run_id), None)
            conversation = next((item for item in data.get("conversations", []) if item.get("id") == conversation_id), None)
            if run:
                run.update({"status": "failed", "stage": "failed", "progress": 100, "message": message, "completedAt": utc_now(), "updatedAt": utc_now()})
            if conversation:
                conversation.setdefault("messages", []).append({"id": str(uuid4()), "role": "assistant", "content": message, "runId": run_id, "createdAt": utc_now()})
                conversation["updatedAt"] = utc_now()
        STORE.update(fail)

    def execute_agent_discovery(self, run_id: str, strategy_id: str, profile: dict) -> None:
        def update_progress(stage: str, message: str, progress: int) -> None:
            def update(data):
                run = next((item for item in data.get("discoveryRuns", []) if item.get("id") == run_id), None)
                if run and run.get("status") == "running":
                    run.update({"stage": stage, "message": message, "progress": progress, "updatedAt": utc_now()})
            STORE.update(update)

        try:
            avoid = _rejected_constraints(STORE.read()) if profile.get("avoidRejected") else []
            result = run_agent_discovery(strategy_id, profile, update_progress, avoid_constraints=avoid)

            def finish(data):
                run = next((item for item in data.get("discoveryRuns", []) if item.get("id") == run_id), None)
                if not run:
                    return
                candidate_ids = []
                for candidate in result["candidates"]:
                    candidate["discoveryRunId"] = run_id
                    data.setdefault("candidates", []).append(candidate)
                    candidate_ids.append(candidate["id"])
                run.update({
                    "status": result["status"],
                    "stage": "human_review" if result["status"] == "needs_human_review" else "completed",
                    "progress": 100,
                    "message": result["summary"],
                    "summary": result["summary"],
                    "candidateIds": candidate_ids,
                    "candidateCount": len(candidate_ids),
                    "searchCount": result["searchCount"],
                    "sourceCount": result["sourceCount"],
                    "model": result["model"],
                    "completedAt": result["completedAt"],
                    "updatedAt": utc_now(),
                })
            STORE.update(finish)
        except (MiniMaxError, WebSearchError, ValueError) as exc:
            def fail(data):
                run = next((item for item in data.get("discoveryRuns", []) if item.get("id") == run_id), None)
                if run:
                    run.update({"status": "failed", "stage": "failed", "progress": 100, "message": str(exc), "completedAt": utc_now(), "updatedAt": utc_now()})
            STORE.update(fail)
        except Exception:
            def fail_unexpected(data):
                run = next((item for item in data.get("discoveryRuns", []) if item.get("id") == run_id), None)
                if run:
                    run.update({"status": "failed", "stage": "failed", "progress": 100, "message": "Agent 运行遇到内部错误，请检查服务并重试。", "completedAt": utc_now(), "updatedAt": utc_now()})
            STORE.update(fail_unexpected)

    def upsert_supplier(self, body: dict) -> None:
        """Save a shop as a sourcing lead, keeping the latest observed price.

        A supplier record is a note about a shop, not a verified supplier:
        MOQ, lead time and invoices stay empty until a human fills them in.
        """
        platform = str(body.get("platform", "")).strip()[:80]
        seller = str(body.get("seller", "")).strip()[:120]
        if not platform or not seller:
            raise ValueError("标记货源需要平台与店铺（页面域名）。")
        note = str(body.get("note", "")).strip()[:600]
        status = str(body.get("status", "待联系")).strip()[:40] or "待联系"
        if status not in {"待联系", "已联系", "已报价", "已排除"}:
            raise ValueError("货源状态必须是待联系、已联系、已报价或已排除。")

        def save(data):
            suppliers = data.setdefault("suppliers", [])
            existing = next(
                (item for item in suppliers if item.get("platform") == platform and item.get("seller") == seller),
                None,
            )
            now = utc_now()
            if existing is None:
                existing = {
                    "id": str(uuid4()),
                    "platform": platform,
                    "seller": seller,
                    "url": str(body.get("url", "")).strip()[:1000],
                    "products": [],
                    "note": "",
                    "status": status,
                    "lastPrice": body.get("price"),
                    "lastCurrency": str(body.get("currency", ""))[:8],
                    "lastObservedAt": str(body.get("observedAt", ""))[:10],
                    "createdAt": now,
                    "updatedAt": now,
                }
                suppliers.append(existing)
            existing["status"] = status
            if note:
                existing["note"] = note
            existing["url"] = str(body.get("url", existing.get("url", ""))).strip()[:1000] or existing.get("url", "")
            if body.get("price") not in (None, ""):
                existing["lastPrice"] = body.get("price")
                existing["lastCurrency"] = str(body.get("currency", existing.get("lastCurrency", "")))[:8]
                existing["lastObservedAt"] = str(body.get("observedAt", existing.get("lastObservedAt", "")))[:10]
            product = str(body.get("product", "")).strip()[:120]
            if product and product not in existing["products"]:
                existing["products"].append(product)
            existing["updatedAt"] = now
            return existing

        saved = STORE.update(save)
        self.send_json(201, saved)

    def create_discovery_run(self, body: dict) -> None:
        trigger = str(body.get("trigger", "")).strip()
        if trigger == "tiktok_trend":
            raw = body.get("signal", {})
            if not isinstance(raw, dict):
                raise ValueError("TikTok 信号必须是对象。")
            title = str(raw.get("title", "")).strip()[:240]
            if not title:
                raise ValueError("请填写商品或趋势信号名称。")
            observed_at = str(raw.get("observedAt", "")).strip()
            try:
                date.fromisoformat(observed_at[:10])
            except ValueError as exc:
                raise ValueError("采集日期必须使用 YYYY-MM-DD 格式。") from exc
            metrics = raw.get("metrics", {})
            if not isinstance(metrics, dict):
                raise ValueError("趋势指标必须是对象。")
            clean_metrics = {}
            for key in ("views", "engagement", "likes", "comments", "shares", "adSpend", "ctr", "conversionRate"):
                value = metrics.get(key)
                if value in (None, ""):
                    continue
                try:
                    number = float(value)
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"趋势指标 {key} 必须是有效数字。") from exc
                if not math.isfinite(number) or number < 0:
                    raise ValueError(f"趋势指标 {key} 不能小于 0。")
                clean_metrics[key] = number
            signal = {
                "title": title,
                "kind": str(raw.get("kind", "top_product"))[:40],
                "market": str(raw.get("market", "日本"))[:80],
                "keyword": str(raw.get("keyword", "")).strip()[:160],
                "source": str(raw.get("source", "TikTok Creative Center"))[:100],
                "url": parse_source_url(str(raw.get("url", ""))),
                "observedAt": observed_at[:10],
                "metrics": clean_metrics,
                "note": str(raw.get("note", "")).strip()[:2000],
            }
            run = {
                "id": str(uuid4()),
                "trigger": trigger,
                "title": title,
                "status": "needs_review",
                "signal": signal,
                "createdAt": utc_now(),
            }
        elif trigger == "cross_market_price_gap":
            raw_source = body.get("sourceListing", {})
            raw_target = body.get("targetListing", {})
            assumptions = body.get("assumptions", {})
            if not isinstance(raw_source, dict) or not isinstance(raw_target, dict) or not isinstance(assumptions, dict):
                raise ValueError("采购商品、销售商品和利润假设必须是对象。")
            source = normalize_listing(raw_source, "source")
            target = normalize_listing(raw_target, "target")
            if source["currency"] != "CNY" or target["currency"] != "JPY":
                raise ValueError("中日价差入口要求采购价为 CNY、销售价为 JPY。")
            matching = match_listings(source, target)
            estimate = None
            if matching["status"] in {"exact_match", "probable_match"}:
                estimate = estimate_cross_market_profit(source, target, assumptions)
            run = {
                "id": str(uuid4()),
                "trigger": trigger,
                "title": target["title"],
                "status": "needs_product_match_review" if estimate is None else "needs_manual_validation",
                "sourceListing": source,
                "targetListing": target,
                "matching": matching,
                "assumptions": {
                    key: value for key, value in assumptions.items()
                    if key in {"sourceCurrencyToJpy", "fxObservedAt", "inboundJpy", "dutyTaxJpy", "fulfillmentJpy", "referralRate", "adCostJpy", "returnReserveRate", "otherCostJpy"}
                },
                "estimate": estimate,
                "createdAt": utc_now(),
            }
        elif trigger == "price_gap_pair":
            candidate_id = str(body.get("candidateId", "")).strip()
            data = STORE.read()
            candidate = find_candidate(data, candidate_id)
            pairing = (candidate or {}).get("priceGap") or {}
            if not candidate or not pairing:
                raise ValueError("这个候选没有可用的比价配对，无法生成价差研究。")
            source = normalize_listing({
                "title": pairing.get("sourceTitle", candidate.get("name", "采购商品")),
                "platform": pairing.get("sourcePlatform", "待确认平台"),
                "market": pairing.get("sourceMarket", candidate.get("sourcingCountry", "中国")),
                "price": pairing.get("sourcePrice"),
                "currency": pairing.get("sourceCurrency", "CNY"),
                "url": pairing.get("sourceUrl", ""),
                "observedAt": pairing.get("observedAt", date.today().isoformat()),
                "brand": pairing.get("brand", ""),
                "model": pairing.get("model", ""),
                "variant": pairing.get("sourceVariant", ""),
                "color": pairing.get("sourceColor", ""),
                "packCount": pairing.get("packCount", ""),
                "attributes": {"配置": pairing.get("sourceVariant", "")},
            }, "source")
            target = normalize_listing({
                "title": pairing.get("targetTitle", candidate.get("name", "销售商品")),
                "platform": pairing.get("targetPlatform", "待确认平台"),
                "market": pairing.get("targetMarket", candidate.get("market", "中国")),
                "price": pairing.get("targetPrice"),
                "currency": pairing.get("targetCurrency", "CNY"),
                "url": pairing.get("targetUrl", ""),
                "observedAt": pairing.get("observedAt", date.today().isoformat()),
                "brand": pairing.get("brand", ""),
                "model": pairing.get("model", ""),
                "variant": pairing.get("targetVariant", ""),
                "color": pairing.get("targetColor", ""),
                "packCount": pairing.get("packCount", ""),
                "attributes": {"配置": pairing.get("targetVariant", "")},
            }, "target")
            matching = match_listings(source, target)
            assumptions = candidate.get("assumptions", {})
            estimate = None
            estimate_error = ""
            if matching["status"] in {"exact_match", "probable_match"}:
                try:
                    estimate = estimate_currency_pair_profit(source, target, assumptions)
                except ValueError as exc:
                    estimate_error = str(exc)
            run = {
                "id": str(uuid4()),
                "trigger": trigger,
                "title": target["title"],
                "status": "needs_product_match_review" if matching["status"] not in {"exact_match", "probable_match"} else (
                    "needs_manual_validation" if estimate else "needs_cost_input"
                ),
                "candidateId": candidate_id,
                "sourceListing": source,
                "targetListing": target,
                "matching": matching,
                "estimate": estimate,
                "estimateError": estimate_error,
                "createdAt": utc_now(),
            }
        else:
            raise ValueError("发现入口必须是 tiktok_trend、cross_market_price_gap 或 price_gap_pair。")

        saved = STORE.update(lambda data: data.setdefault("discoveryRuns", []).append(run) or run)
        self.send_json(201, saved)

    def promote_discovery_run(self, run_id: str) -> None:
        def promote(data):
            run = next((item for item in data.get("discoveryRuns", []) if item.get("id") == run_id), None)
            if not run:
                return None, 404
            if run.get("candidateId"):
                existing = find_candidate(data, run["candidateId"])
                if existing:
                    return candidate_view(existing, data.get("profile", DEFAULT_PROFILE)), 200

            name = run.get("title") or "待研究商品"
            if run.get("trigger") == "tiktok_trend":
                signal = run["signal"]
                hypothesis = f"TikTok 趋势信号待验证：{signal.get('keyword') or signal['title']}。平台趋势数据是需求线索，不直接代表成交销量。"
                evidence_rows = [{
                    "dimension": "demand",
                    "title": signal["title"],
                    "sourceType": signal["source"],
                    "url": signal["url"],
                    "observedAt": signal["observedAt"],
                    "note": f"{signal.get('note') or '用户记录的趋势线索。'} 指标快照：{json.dumps(signal.get('metrics', {}), ensure_ascii=False)}。趋势指标口径以来源页面当时显示为准，不代表成交销量。",
                }]
            else:
                source = run["sourceListing"]
                target = run["targetListing"]
                hypothesis = "中日同品价差线索待核实：需先复核规格、包装数量、供货价、费用、税费和汇率。"
                evidence_rows = [
                    {
                        "dimension": "sourcing",
                        "title": source["title"],
                        "sourceType": source["platform"],
                        "url": source["url"],
                        "observedAt": source["observedAt"],
                        "note": f"采购页快照：{source['price']} {source['currency']}；品牌={source.get('brand') or '未录入'}，型号={source.get('model') or '未录入'}，规格={source.get('variant') or '未录入'}，包装数量={source.get('packCount') or '未录入'}。平台价格、库存、MOQ 和阶梯价仍需确认。",
                    },
                    {
                        "dimension": "competition",
                        "title": target["title"],
                        "sourceType": target["platform"],
                        "url": target["url"],
                        "observedAt": target["observedAt"],
                        "note": f"销售页快照：{target['price']} {target['currency']}；品牌={target.get('brand') or '未录入'}，型号={target.get('model') or '未录入'}，规格={target.get('variant') or '未录入'}，包装数量={target.get('packCount') or '未录入'}。库存、优惠和实际销售费用仍需确认。",
                    },
                ]

            candidate_id = str(uuid4())
            evidence = [{"id": str(uuid4()), **row, "createdAt": utc_now()} for row in evidence_rows]
            candidate = {
                "id": candidate_id,
                "name": name[:160],
                "category": "其他",
                "hypothesis": hypothesis,
                "market": "日本",
                "platform": "Amazon.co.jp" if run.get("trigger") == "cross_market_price_gap" else "待确认销售平台",
                "sourcingCountry": "中国",
                "fulfillment": "待确认",
                "mode": "active_research",
                "status": "researching",
                "assumptions": {},
                "evidence": evidence,
                "scores": {},
                "tasks": build_tasks(candidate_id, "active_research"),
                "discoveryRunId": run_id,
                "createdAt": utc_now(),
                "updatedAt": utc_now(),
            }
            data.setdefault("candidates", []).append(candidate)
            run["candidateId"] = candidate_id
            run["status"] = "promoted_to_research"
            return candidate_view(candidate, data.get("profile", DEFAULT_PROFILE)), 201

        result, status = STORE.update(promote)
        self.send_json(status, result or {"error": "没有找到这条发现记录。"})

    def add_evidence(self, candidate_id: str, body: dict) -> None:
        dimension = str(body.get("dimension", "")).strip()
        title = str(body.get("title", "")).strip()[:240]
        url = str(body.get("url", "")).strip()[:2000]
        observed_at = str(body.get("observedAt", "")).strip()
        note = str(body.get("note", "")).strip()[:4000]
        if dimension not in EVIDENCE_DIMENSIONS:
            raise ValueError("请选择有效的研究维度。")
        if not title or not note:
            raise ValueError("来源名称和研究摘录/备注为必填项。")
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("来源链接必须是 http 或 https URL。")
        try:
            date.fromisoformat(observed_at[:10])
        except ValueError as exc:
            raise ValueError("采集日期必须使用 YYYY-MM-DD 格式。") from exc
        evidence = {
            "id": str(uuid4()),
            "dimension": dimension,
            "title": title,
            "sourceType": str(body.get("sourceType", "其他"))[:80],
            "url": urlunparse(parsed),
            "observedAt": observed_at[:10],
            "note": note,
            "createdAt": utc_now(),
        }

        def append(data):
            candidate = find_candidate(data, candidate_id)
            if not candidate:
                return None
            candidate.setdefault("evidence", []).append(evidence)
            candidate["updatedAt"] = utc_now()
            refresh_task_statuses(candidate)
            return candidate_view(candidate, data.get("profile", DEFAULT_PROFILE))

        result = STORE.update(append)
        self.send_json(201 if result else 404, result or {"error": "没有找到这个候选。"})

    def add_score(self, candidate_id: str, body: dict) -> None:
        key = str(body.get("key", "")).strip()
        if key not in SCORE_KEYS:
            raise ValueError("请选择有效的独立评分。")
        try:
            value = float(body.get("value"))
        except (TypeError, ValueError) as exc:
            raise ValueError("评分必须是 1 到 5 的数字。") from exc
        if not math.isfinite(value) or value < 1 or value > 5:
            raise ValueError("评分必须在 1 到 5 之间。")
        rationale = str(body.get("rationale", "")).strip()[:2000]
        evidence_ids = body.get("evidenceIds")
        if not rationale:
            raise ValueError("请写明本项评分的判断依据。")
        if not isinstance(evidence_ids, list) or not evidence_ids:
            raise ValueError("每项评分至少要关联一条来源证据。")

        def save(data):
            candidate = find_candidate(data, candidate_id)
            if not candidate:
                return None, "missing"
            evidence_by_id = {item.get("id"): item for item in candidate.get("evidence", [])}
            selected = [evidence_by_id.get(str(evidence_id)) for evidence_id in evidence_ids]
            allowed_dimensions = SCORE_EVIDENCE_DIMENSIONS[key]
            if any(
                item is None
                or item.get("dimension") not in allowed_dimensions
                or not item.get("url")
                or not item.get("observedAt")
                for item in selected
            ):
                return None, "evidence_required"
            candidate.setdefault("scores", {})[key] = {
                "value": value,
                "rationale": rationale,
                "evidenceIds": [item["id"] for item in selected],
                "updatedAt": utc_now(),
            }
            candidate["updatedAt"] = utc_now()
            return candidate_view(candidate, data.get("profile", DEFAULT_PROFILE)), "ok"

        result, status = STORE.update(save)
        if status == "evidence_required":
            self.send_json(422, {"error": "所选证据必须属于该评分相关研究范围，并包含来源链接和采集日期。"})
        elif status == "missing":
            self.send_json(404, {"error": "没有找到这个候选。"})
        else:
            self.send_json(200, result)

    def handle_api_patch(self, path: str, body: dict) -> None:
        if path == "/api/profile":
            allowed = set(DEFAULT_PROFILE)
            market = str(body.get("market", "")).strip()[:80]
            if market and market not in MARKETS:
                raise ValueError(f"目标市场需为 {'、'.join(MARKETS)} 之一。")
            source_country = str(body.get("sourcingCountry", "")).strip()[:80]
            if source_country and source_country not in supply_markets():
                raise ValueError(f"采购来源国需为 {'、'.join(supply_markets())} 之一。")

            def save_profile(data):
                profile = data.setdefault("profile", dict(DEFAULT_PROFILE))
                previous_market = str(profile.get("market", "日本"))
                for key, value in body.items():
                    if key in allowed:
                        profile[key] = value
                current_market = str(profile.get("market", "日本"))
                names = {item["name"] for item in sales_channels(current_market)}
                if current_market != previous_market and str(profile.get("platform", "")) not in names:
                    # The old platform does not sell into the new market.
                    profile["platform"] = default_platform_for_market(current_market)
                if not str(profile.get("platform", "")).strip():
                    profile["platform"] = default_platform_for_market(current_market)
                return profile

            self.send_json(200, STORE.update(save_profile))
            return

        supplier_match = re.fullmatch(r"/api/suppliers/([\w-]+)", path)
        if supplier_match:
            def update_supplier(data):
                supplier = next((item for item in data.get("suppliers", []) if item.get("id") == supplier_match.group(1)), None)
                if not supplier:
                    return None
                status = str(body.get("status", supplier.get("status", "待联系"))).strip()[:40]
                if status not in {"待联系", "已联系", "已报价", "已排除"}:
                    raise ValueError("货源状态必须是待联系、已联系、已报价或已排除。")
                supplier["status"] = status
                if "note" in body:
                    supplier["note"] = str(body.get("note", "")).strip()[:600]
                supplier["updatedAt"] = utc_now()
                return supplier

            result = STORE.update(update_supplier)
            self.send_json(200 if result else 404, result or {"error": "没有找到这个货源记录。"})
            return

        match = re.fullmatch(r"/api/candidates/([\w-]+)", path)
        if not match:
            self.send_json(404, {"error": "没有找到这个接口。"})
            return
        allowed = {"name", "category", "hypothesis", "status", "market", "platform", "fulfillment", "assumptions", "purchaseChecklist", "pilot"}

        def update_candidate(data):
            candidate = find_candidate(data, match.group(1))
            if not candidate:
                return None
            for key, value in body.items():
                if key in allowed:
                    if key == "assumptions":
                        if not isinstance(value, dict):
                            raise ValueError("利润假设必须是对象。")
                        # Merge, so the same-currency and the cross-border
                        # profit models can be filled independently.
                        candidate["assumptions"] = {**(candidate.get("assumptions") or {}), **value}
                        continue
                    if key == "status" and value not in {"researching", "ready_for_test", "paused", "archived"}:
                        raise ValueError("研究状态无效。")
                    if key == "pilot":
                        if not isinstance(value, dict):
                            raise ValueError("小测数据必须是对象。")
                        candidate["pilot"] = {**(candidate.get("pilot") or {}), **value}
                        thresholds = (data.get("profile") or DEFAULT_PROFILE).get("pilotThresholds")
                        advance_candidate_workflow(candidate, thresholds)
                        continue
                    if key == "purchaseChecklist":
                        if not isinstance(value, list):
                            raise ValueError("采购清单必须是列表。")
                        candidate["purchaseChecklist"] = merge_purchase_checklist(
                            candidate.get("purchaseChecklist", []), value
                        )
                        thresholds = (data.get("profile") or DEFAULT_PROFILE).get("pilotThresholds")
                        advance_candidate_workflow(candidate, thresholds)
                        continue
                    candidate[key] = value
            candidate["updatedAt"] = utc_now()
            return candidate_view(candidate, data.get("profile", DEFAULT_PROFILE))

        result = STORE.update(update_candidate)
        self.send_json(200 if result else 404, result or {"error": "没有找到这个候选。"})


def main() -> None:
    host = os.environ.get("PROSPECTOR_HOST", "127.0.0.1")
    port = int(os.environ.get("PROSPECTOR_PORT", "8000"))
    print(f"Prospector Agent running at http://{host}:{port}")
    print("Local-only prototype. External marketplace connectors require separate authorization.")
    try:
        ThreadingHTTPServer((host, port), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")


if __name__ == "__main__":
    main()

