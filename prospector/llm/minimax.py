"""MiniMax Anthropic Messages adapter with local Pi credential discovery."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.parse import urlsplit, urlunsplit


DEFAULT_AUTH_FILE = Path.home() / ".pi" / "agent" / "auth.json"
DEFAULT_MODELS_FILE = Path.home() / ".pi" / "agent" / "models.json"
DEFAULT_BASE_URL = "https://api.minimaxi.com/anthropic"
DEFAULT_MODEL = "MiniMax-M3.1-Flash"
REQUEST_TIMEOUT_SECONDS = 75
ALLOWED_API_HOSTS = {"api.minimaxi.com", "api.minimax.io"}


class MiniMaxError(RuntimeError):
    """A provider failure safe to return to the local UI."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _config() -> tuple[str, str, str]:
    auth_path = Path(os.environ.get("PROSPECTOR_MINIMAX_AUTH_FILE", DEFAULT_AUTH_FILE))
    models_path = Path(os.environ.get("PROSPECTOR_MINIMAX_MODELS_FILE", DEFAULT_MODELS_FILE))
    auth = _read_json(auth_path)
    credential = auth.get("minimax-cn", {})
    key = os.environ.get("MINIMAX_API_KEY", "").strip() or str(credential.get("key", "")).strip()

    provider = _read_json(models_path).get("providers", {}).get("minimax-cn", {})
    base_url = os.environ.get("MINIMAX_BASE_URL", "").strip() or str(provider.get("baseUrl", DEFAULT_BASE_URL)).strip()
    models = provider.get("models", [])
    configured_model = models[0].get("id") if models and isinstance(models[0], dict) else None
    model = os.environ.get("PROSPECTOR_MINIMAX_MODEL", "").strip() or configured_model or DEFAULT_MODEL
    return key, base_url.rstrip("/"), model


def provider_status() -> dict:
    """Return safe provider metadata; credential material is never returned."""
    key, base_url, model = _config()
    parsed = urlsplit(base_url)
    safe_host = parsed.hostname if parsed.scheme == "https" else None
    configured = bool(key and "\r" not in key and "\n" not in key and safe_host in ALLOWED_API_HOSTS and parsed.port in (None, 443))
    return {
        "provider": "MiniMax",
        "model": model,
        "configured": configured,
        "status": "ready" if configured else "missing_configuration",
        "host": safe_host or "",
        "message": "MiniMax Agent 可用；点击任务时会发送该任务资料进行分析。" if configured else "未找到 MiniMax 密钥或 HTTPS 接口配置。",
    }


def complete(*, system: str, prompt: str, max_tokens: int = 1800) -> tuple[str, str]:
    """Run one Anthropic-compatible Messages request and return visible text."""
    key, base_url, model = _config()
    parsed = urlsplit(base_url)
    if not key or "\r" in key or "\n" in key:
        raise MiniMaxError("未找到 MiniMax API 密钥。请检查本机 Pi Agent 配置或设置 MINIMAX_API_KEY。", 503)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_API_HOSTS or parsed.port not in (None, 443):
        raise MiniMaxError("MiniMax 接口必须使用受支持的官方 HTTPS 主机。", 503)

    endpoint = urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/") + "/v1/messages", "", ""))
    payload = {
        "model": model,
        "max_tokens": max(256, min(int(max_tokens), 3000)),
        "output_config": {"effort": "low"},
        "system": system,
        "messages": [{"role": "user", "content": prompt}],
    }
    try:
        with tempfile.TemporaryDirectory(prefix="prospector-minimax-") as directory:
            directory_path = Path(directory)
            headers_path = directory_path / "headers"
            request_path = directory_path / "request.json"
            response_path = directory_path / "response.json"
            headers_path.write_text(
                f"Authorization: Bearer {key}\nContent-Type: application/json\n",
                encoding="utf-8",
            )
            request_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            headers_path.chmod(0o600)
            request_path.chmod(0o600)
            completed = subprocess.run(
                [
                    "curl", "--silent", "--show-error", "--max-time", str(REQUEST_TIMEOUT_SECONDS),
                    "--header", f"@{headers_path}", "--data-binary", f"@{request_path}",
                    "--output", str(response_path), "--write-out", "%{http_code}", endpoint,
                ],
                capture_output=True,
                timeout=REQUEST_TIMEOUT_SECONDS + 5,
                check=False,
            )
            code_text = completed.stdout.decode("ascii", errors="ignore").strip()
            if not re.fullmatch(r"\d{3}", code_text):
                raise MiniMaxError("连接 MiniMax 超时或失败，请检查网络后重试。", 504)
            status = int(code_text)
            if status == 0:
                raise MiniMaxError("连接 MiniMax 超时或失败，请检查网络后重试。", 504)
            if status in (401, 403):
                raise MiniMaxError("MiniMax 拒绝了请求，请检查密钥状态或模型权限。", 502)
            if status == 429:
                raise MiniMaxError("MiniMax 当前请求频率受限，请稍后重试。", 503)
            if status >= 500:
                raise MiniMaxError("MiniMax 服务暂时不可用，请稍后重试。", 503)
            if status != 200:
                raise MiniMaxError("MiniMax 请求未被接受，请检查模型配置。", 502)
            if not response_path.exists() or response_path.stat().st_size > 2_000_000:
                raise MiniMaxError("MiniMax 返回内容为空或超出大小限制。", 502)
            try:
                result = json.loads(response_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                raise MiniMaxError("MiniMax 返回了无法读取的响应。", 502) from None
    except FileNotFoundError:
        raise MiniMaxError("此服务环境找不到 curl，无法连接 MiniMax。", 503) from None
    except subprocess.TimeoutExpired:
        raise MiniMaxError("连接 MiniMax 超时或失败，请检查网络后重试。", 504) from None
    except OSError:
        raise MiniMaxError("连接 MiniMax 超时或失败，请检查网络后重试。", 504) from None

    blocks = result.get("content", []) if isinstance(result, dict) else []
    text = "\n".join(
        str(block.get("text", "")).strip()
        for block in blocks
        if isinstance(block, dict) and block.get("type") == "text" and block.get("text")
    ).strip()
    if not text:
        raise MiniMaxError("MiniMax 没有返回可展示的分析文本。", 502)
    return text, model
