"""MiniMax Coding Plan web_search MCP client over its documented stdio transport."""

from __future__ import annotations

import json
import os
import select
import shutil
import subprocess
import time
from urllib.parse import urlsplit

from prospector.llm.minimax import _config, provider_status
from prospector.platforms import platform_for_url


class WebSearchError(RuntimeError):
    """A safe-to-display MiniMax search failure."""


def search_status() -> dict:
    key, base_url, _ = _config()
    host = urlsplit(base_url).hostname
    ready = bool(provider_status().get("configured") and key and host in {"api.minimaxi.com", "api.minimax.io"} and shutil.which("uvx"))
    return {
        "provider": "MiniMax Web Search",
        "configured": ready,
        "status": "ready" if ready else "missing_configuration",
        "message": "Agent 可搜索公开网页并保留来源链接。搜索摘要不能替代平台 API 的完整商品数据。" if ready else "MiniMax Web Search 暂不可用，请检查 Pi 密钥、区域接口和 uvx。",
    }


class _MCPClient:
    def __init__(self, key: str, host: str):
        executable = shutil.which("uvx")
        if not executable:
            raise WebSearchError("没有找到 uvx，MiniMax Web Search 无法启动。")
        env = dict(os.environ)
        env["MINIMAX_API_KEY"] = key
        env["MINIMAX_API_HOST"] = host
        env.setdefault("UV_NO_PROGRESS", "1")
        try:
            self.process = subprocess.Popen(
                [executable, "minimax-coding-plan-mcp", "-y"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=env,
                bufsize=0,
            )
        except OSError:
            raise WebSearchError("MiniMax Web Search 服务无法启动。") from None
        self.buffer = bytearray()
        self.next_id = 1

    def _send(self, method: str, params: dict | None = None, *, notification: bool = False, timeout: int = 45) -> dict | None:
        request_id = self.next_id
        if not notification:
            self.next_id += 1
        message = {"jsonrpc": "2.0", "method": method}
        if not notification:
            message["id"] = request_id
        if params is not None:
            message["params"] = params
        try:
            self.process.stdin.write((json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8"))
            self.process.stdin.flush()
        except (BrokenPipeError, OSError):
            raise WebSearchError("MiniMax Web Search 连接中断。") from None
        if notification:
            return None

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            newline = self.buffer.find(b"\n")
            if newline >= 0:
                line = bytes(self.buffer[:newline]).strip()
                del self.buffer[: newline + 1]
                if not line:
                    continue
                try:
                    response = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if response.get("id") == request_id:
                    if response.get("error"):
                        raise WebSearchError("MiniMax Web Search 请求失败，请检查密钥权限或稍后重试。")
                    return response
                continue
            if self.process.poll() is not None:
                break
            wait = max(0.05, deadline - time.monotonic())
            ready, _, _ = select.select([self.process.stdout], [], [], wait)
            if not ready:
                break
            try:
                chunk = os.read(self.process.stdout.fileno(), 65536)
            except OSError:
                break
            if not chunk:
                break
            self.buffer.extend(chunk)
            if len(self.buffer) > 2_000_000:
                raise WebSearchError("MiniMax Web Search 返回内容超出限制。")
        raise WebSearchError("MiniMax Web Search 响应超时或连接中断。")

    def initialize(self) -> None:
        self._send(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "prospector-agent", "version": "1.0"},
            },
            timeout=90,
        )
        self._send("notifications/initialized", notification=True)
        response = self._send("tools/list", {})
        tools = response.get("result", {}).get("tools", []) if response else []
        if not any(tool.get("name") == "web_search" for tool in tools):
            raise WebSearchError("MiniMax Web Search 未提供 web_search 工具。")

    def search(self, query: str) -> list[dict]:
        response = self._send("tools/call", {"name": "web_search", "arguments": {"query": query[:240]}}, timeout=40)
        result = response.get("result", {}) if response else {}
        if result.get("isError"):
            raise WebSearchError("MiniMax Web Search 查询失败，请稍后重试。")
        for block in result.get("content", []):
            if block.get("type") != "text":
                continue
            try:
                payload = json.loads(block.get("text", ""))
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            organic = payload.get("organic", [])
            if not isinstance(organic, list):
                continue
            normalized = []
            for item in organic[:6]:
                if not isinstance(item, dict):
                    continue
                link = str(item.get("link", "")).strip()
                parsed = urlsplit(link)
                if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                    continue
                normalized.append({
                    "title": str(item.get("title", ""))[:240],
                    "url": link[:1000],
                    "snippet": str(item.get("snippet", ""))[:1200],
                    "date": str(item.get("date", ""))[:80],
                })
            return normalized
        raise WebSearchError("MiniMax Web Search 返回了无法识别的结果。")

    def close(self) -> None:
        if self.process.poll() is None:
            try:
                self.process.terminate()
                self.process.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                self.process.kill()
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    pass


def search_many(queries: list[dict]) -> list[dict]:
    """Run a bounded batch of searches through the official MiniMax MCP tool."""
    if not queries or len(queries) > 12:
        raise WebSearchError("本次搜索数量无效。")
    key, base_url, _ = _config()
    provider = provider_status()
    if not provider.get("configured") or not key:
        raise WebSearchError("MiniMax API 配置不可用。")
    host = f"{urlsplit(base_url).scheme}://{urlsplit(base_url).netloc}"
    client = _MCPClient(key, host)
    results = []
    try:
        client.initialize()
        for entry in queries:
            query = str(entry.get("query", "")).strip()
            if not query:
                continue
            for item in client.search(query):
                matched = platform_for_url(item.get("url", ""))
                results.append({
                    **item,
                    "query": query,
                    "stage": str(entry.get("stage", "demand")),
                    "sourceLabel": str(entry.get("sourceLabel", "Web Search")),
                    "candidateRef": str(entry.get("candidateRef", "")),
                    "platformId": matched["id"] if matched else "",
                    "platformName": matched["name"] if matched else "",
                    "platformRole": matched["role"] if matched else "",
                })
    finally:
        client.close()
    return results
