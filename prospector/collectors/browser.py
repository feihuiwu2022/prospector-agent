"""Collect visible prices with the operator's own logged-in browser session.

The Agent's web search only returns public snippets.  Taobao, JD, Douyin and
1688 render their prices in JavaScript pages that, in practice, only show them
to a logged-in visitor, so those platforms look "empty" in a search-only run.

This collector drives the operator's own Chrome against a dedicated profile
directory.  The operator logs in once (QR code, or the credentials they put in
the local .env), and every later run reuses that session.

Rules the collector keeps:
- it never solves a captcha and never spoofs a fingerprint: when a platform
  shows a slider or a QR check, the operator completes it in the open window;
- it reads only what the page already shows the logged-in operator;
- it never buys, bids, chats or places an order.

It runs on its own virtualenv (.venv) because the main service stays
standard-library only.  The server calls it as a subprocess and reads JSON.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PROFILE_DIR = Path(os.environ.get("PROSPECTOR_BROWSER_PROFILE", ROOT / "data" / "browser-profile"))
ENV_FILE = Path(os.environ.get("PROSPECTOR_ENV_FILE", ROOT / ".env"))
DEBUG_PORT = int(os.environ.get("PROSPECTOR_BROWSER_PORT", "9333"))

CHROME_PATHS = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
)

# One place for every platform-specific detail: where to search, where to log
# in, which links are product links, and how to tell a logged-in session.
BROWSER_PLATFORMS = {
    "taobao": {
        "id": "taobao",
        "name": "淘宝 / 天猫",
        "currency": "CNY",
        "searchUrl": "https://s.taobao.com/search?q={query}",
        "loginUrl": "https://login.taobao.com/",
        "loginCookies": ("cookie2", "unb", "tracknick"),
        "selectors": (
            "a[href*='item.taobao.com/item.htm']",
            "a[href*='detail.tmall.com/item.htm']",
            "a[href*='/item.htm?id=']",
        ),
        "usernameField": "css:input[name='login']",
        "passwordField": "css:input[name='password']",
        "submitButton": "css:button[type='submit']",
        "envPrefix": "TAOBAO",
    },
    "jd": {
        "id": "jd",
        "name": "京东",
        "currency": "CNY",
        "searchUrl": "https://search.jd.com/Search?keyword={query}&enc=utf-8",
        "loginUrl": "https://passport.jd.com/new/login.aspx",
        "loginCookies": ("pt_key", "pt_pin", "thor"),
        "selectors": (
            "a[href*='item.jd.com']",
            "a[href*='item.m.jd.com']",
        ),
        "usernameField": "css:#loginname",
        "passwordField": "css:#passwd",
        "submitButton": "css:.btn-login",
        "passwordTab": "text:密码登录",
        "envPrefix": "JD",
    },
    "douyin": {
        "id": "douyin",
        "name": "抖音（抖音商城）",
        "currency": "CNY",
        "searchUrl": "https://haohuo.jinritemai.com/views/search/index?keyword={query}",
        "loginUrl": "https://www.douyin.com/",
        "loginCookies": ("sessionid", "passport_csrf_token", "sid_guard"),
        "selectors": (
            "a[href*='/goods/detail']",
            "a[href*='douyin.com/goods']",
        ),
        "usernameField": "",
        "passwordField": "",
        "submitButton": "",
        "envPrefix": "DOUYIN",
    },
    "1688": {
        "id": "1688",
        "name": "1688",
        "currency": "CNY",
        "searchUrl": "https://s.1688.com/selloffer/offer_search.htm?keywords={query}",
        "loginUrl": "https://login.1688.com/",
        "loginCookies": ("cookie2", "unb", "_tb_token_", "cna"),
        "selectors": (
            "a[href*='detail.1688.com/offer']",
            "a[href*='offer_search.htm']",
        ),
        "usernameField": "css:input[name='login']",
        "passwordField": "css:input[name='password']",
        "submitButton": "css:button[type='submit']",
        "envPrefix": "ALIBABA",
    },
}

PRICE_PATTERNS = (
    re.compile(r"[¥￥]\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)"),
    re.compile(r"([0-9][0-9,]*(?:\.[0-9]{1,2})?)\s*元"),
    re.compile(r"\"(?:price|priceText|priceValue)\"\s*:\s*\"?([0-9][0-9,]*(?:\.[0-9]{1,2})?)"),
    re.compile(r"(?:售价|价格|到手价)\s*[:：]?\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)"),
)

LOGIN_WALLS = ("请登录", "立即登录", "扫码登录", "登录后查看", "亲，请登录", "账号登录", "login.taobao.com")


def log(message: str) -> None:
    """Progress goes to stderr; stdout carries only the JSON result."""
    print(message, file=sys.stderr, flush=True)


def load_env(path: Path = ENV_FILE) -> dict:
    """Read a local .env file.  Values never leave this machine."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        values[key.strip()] = value
    return values


def chrome_path() -> str:
    for candidate in CHROME_PATHS:
        if Path(candidate).exists():
            return candidate
    raise RuntimeError("没有找到 Chrome/Chromium，请安装 Google Chrome 或设置 PROSPECTOR_BROWSER_PROFILE。")


def venv_python() -> Path:
    candidate = ROOT / ".venv" / "bin" / "python"
    if not candidate.exists():
        raise RuntimeError("缺少 .venv 虚拟环境，无法启动浏览器采集。")
    return candidate


def _options():
    from DrissionPage import ChromiumOptions

    options = ChromiumOptions()
    options.set_browser_path(chrome_path())
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    options.set_user_data_path(str(PROFILE_DIR))
    options.set_local_port(DEBUG_PORT)
    options.headless(False)
    options.set_argument("--no-first-run")
    options.set_argument("--no-default-browser-check")
    options.set_load_mode("normal")
    return options


def _wait_ready(tab) -> None:
    """Wait for the page to settle; some search pages reload themselves."""
    try:
        tab.wait.doc_loaded(timeout=25, raise_err=False)
    except TypeError:
        try:
            tab.wait.doc_loaded()
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001
        pass


def open_browser():
    """Open (or attach to) the collector's Chrome instance."""
    from DrissionPage import Chromium

    browser = Chromium(_options())
    try:
        browser.set.window.max()
    except Exception:  # noqa: BLE001 - window sizing is cosmetic
        pass
    return browser


def _cookie_names(tab) -> set:
    names = set()
    try:
        for cookie in tab.cookies(all_domains=True) or []:
            name = cookie.get("name") if isinstance(cookie, dict) else getattr(cookie, "name", "")
            if name:
                names.add(str(name))
    except Exception:  # noqa: BLE001 - cookie reading must never stop a run
        pass
    return names


def logged_in(tab, platform: dict) -> bool:
    names = _cookie_names(tab)
    return any(cookie in names for cookie in platform["loginCookies"])


def _fill_login(tab, platform: dict, env: dict) -> str:
    """Fill the login form when the operator stored credentials in .env."""
    prefix = platform["envPrefix"]
    username = env.get(f"{prefix}_USERNAME", "")
    password = env.get(f"{prefix}_PASSWORD", "")
    if not username or not password:
        return "no_credentials"
    try:
        # Some pages open on a QR tab; the password form is one click away.
        tab_hint = platform.get("passwordTab")
        if tab_hint:
            try:
                tab.ele(tab_hint, timeout=3).click()
                time.sleep(1)
            except Exception:  # noqa: BLE001 - the tab may already be active
                pass
        user_field = tab.ele(platform["usernameField"], timeout=8)
        pass_field = tab.ele(platform["passwordField"], timeout=8)
        user_field.input(username)
        pass_field.input(password)
        button = tab.ele(platform["submitButton"], timeout=5)
        if button:
            button.click()
        return "filled"
    except Exception as exc:  # noqa: BLE001 - the operator can still log in by hand
        log(f"自动填表失败（{exc}），请在打开的窗口里手动登录。")
        return "failed"


def wait_for_login(tab, platform: dict, env: dict, timeout: int = 180) -> dict:
    """Open the login page and wait until the session is usable."""
    tab.get(platform["loginUrl"])
    _wait_ready(tab)
    action = _fill_login(tab, platform, env)
    if action == "no_credentials":
        log("没有在 .env 里找到账号密码，请在打开的浏览器窗口里手动完成登录（扫码或密码）。")
    deadline = time.time() + timeout
    while time.time() < deadline:
        if logged_in(tab, platform):
            return {"loggedIn": True, "method": action}
        time.sleep(2)
    return {"loggedIn": logged_in(tab, platform), "method": action}


EXTRACT_JS = """
() => {
  const out = [];
  for (const selector of SELECTORS) {
    let anchors = [];
    try { anchors = document.querySelectorAll(selector); } catch (e) { continue; }
    for (const anchor of anchors) {
      const href = anchor.href || '';
      let node = anchor;
      let text = '';
      for (let i = 0; i < 7 && node; i++) {
        text = (node.innerText || '').trim();
        if (text.length > 10) break;
        node = node.parentElement;
      }
      out.push({
        href,
        title: (anchor.innerText || '').trim().slice(0, 160),
        context: text.slice(0, 800)
      });
    }
  }
  return out;
}
"""


def _price_of(text: str) -> float | None:
    for pattern in PRICE_PATTERNS:
        match = pattern.search(text or "")
        if not match:
            continue
        try:
            value = float(match.group(1).replace(",", ""))
        except ValueError:
            continue
        if value > 0:
            return value
    return None


def _item_key(url: str) -> str:
    match = re.search(r"(?:id=|/)([0-9]{6,})", url or "")
    return match.group(1) if match else (url or "")


def collect_platform(tab, platform: dict, product: str, limit: int = 20) -> dict:
    """Open the platform search page and read the visible prices."""
    query = urllib_quote(product)
    url = platform["searchUrl"].format(query=query)
    result = {"platformId": platform["id"], "platform": platform["name"], "url": url, "rows": [], "reason": ""}
    try:
        tab.get(url)
    except Exception as exc:  # noqa: BLE001 - a page that reloads itself is still usable
        result["reason"] = f"页面打开有提示：{exc}"
    _wait_ready(tab)
    time.sleep(3)
    for _ in range(3):
        try:
            tab.run_js("window.scrollTo(0, document.body.scrollHeight);")
        except Exception:  # noqa: BLE001
            pass
        time.sleep(1.2)

    landed = ""
    try:
        landed = str(tab.url or "")
    except Exception:  # noqa: BLE001
        landed = ""
    if any(host in landed for host in ("passport.jd.com", "login.taobao.com", "login.1688.com", "login.alibaba.com", "haohuo.jinritemai.com/login")):
        result["reason"] = "页面跳到了登录页：请先点下面的「登录」按钮扫码一次，再回来采集。"

    page_text = ""
    try:
        page_text = tab.run_js("return document.body ? document.body.innerText : ''") or ""
    except Exception:  # noqa: BLE001
        page_text = ""
    if any(wall in page_text for wall in LOGIN_WALLS) and not logged_in(tab, platform) and not result["reason"]:
        result["reason"] = "页面要求登录：请先在该平台登录一次，再采集。"

    script = EXTRACT_JS.replace("SELECTORS", json.dumps(list(platform["selectors"])))
    try:
        raw_rows = tab.run_js(script) or []
    except Exception as exc:  # noqa: BLE001
        result["reason"] = f"页面结构读取失败：{exc}"
        return result

    seen: set = set()
    for raw in raw_rows:
        href = str(raw.get("href", ""))
        title = str(raw.get("title", "")).strip()
        context = str(raw.get("context", ""))
        price = _price_of(context) or _price_of(title)
        if not href or price is None:
            continue
        key = _item_key(href)
        if key in seen:
            continue
        seen.add(key)
        rows = result["rows"]
        rows.append({
            "platformId": platform["id"],
            "platform": platform["name"],
            "title": title[:200] or href[:120],
            "url": href[:1000],
            "price": round(price, 2),
            "currency": platform["currency"],
            "snippet": context[:400],
            "observedAt": date.today().isoformat(),
            "source": "本机浏览器采集",
        })
        if len(rows) >= limit:
            break
    if not result["rows"] and not result["reason"]:
        result["reason"] = "页面上没有读到带价格的商品卡片（可能需要登录或页面结构已改版）。"
    return result


def urllib_quote(value: str) -> str:
    from urllib.parse import quote

    return quote(str(value), safe="")


def status() -> dict:
    """Report whether the browser path is usable, without opening a window."""
    python = ROOT / ".venv" / "bin" / "python"
    return {
        "ready": python.exists() and Path(PROFILE_DIR).exists(),
        "venv": str(python),
        "profile": str(PROFILE_DIR),
        "profileExists": Path(PROFILE_DIR).exists(),
        "envFile": str(ENV_FILE),
        "envExists": ENV_FILE.exists(),
        "platforms": [
            {"id": key, "name": item["name"], "hasCredentials": bool(
                load_env().get(f"{item['envPrefix']}_USERNAME")
                and load_env().get(f"{item['envPrefix']}_PASSWORD")
            )}
            for key, item in BROWSER_PLATFORMS.items()
        ],
    }


def run_collect(product: str, platform_ids: list[str], limit: int = 20, wait_login: int = 0) -> dict:
    env = load_env()
    browser = open_browser()
    tab = browser.latest_tab
    platforms = []
    try:
        for platform_id in platform_ids:
            platform = BROWSER_PLATFORMS.get(platform_id)
            if not platform:
                continue
            state = {"loggedIn": logged_in(tab, platform)}
            if not state["loggedIn"] and wait_login:
                state = wait_for_login(tab, platform, env, timeout=wait_login)
            collected = collect_platform(tab, platform, product, limit=limit)
            collected["loggedIn"] = state["loggedIn"]
            if not state["loggedIn"]:
                collected["reason"] = (collected.get("reason") or "") + "（未登录：页面只显示部分结果）"
            platforms.append(collected)
    finally:
        try:
            browser.quit()
        except Exception:  # noqa: BLE001
            pass
    return {"product": product, "platforms": platforms, "collectedAt": date.today().isoformat()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="用本机浏览器会话采集平台可见报价")
    parser.add_argument("command", choices=("status", "login", "collect"))
    parser.add_argument("--product", default="")
    parser.add_argument("--platforms", default="taobao,jd")
    parser.add_argument("--platform", default="")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--wait-login", type=int, default=0, help="未登录时等待人工登录的秒数")
    args = parser.parse_args(argv)

    if args.command == "status":
        print(json.dumps(status(), ensure_ascii=False))
        return 0

    env = load_env()
    if args.command == "login":
        wanted = [item.strip() for item in args.platform.split(",") if item.strip()]
        wanted = [item for item in wanted if item in BROWSER_PLATFORMS]
        if not wanted:
            print(json.dumps({"error": "未知平台"}, ensure_ascii=False))
            return 2
        browser = open_browser()
        results = []
        try:
            for platform_id in wanted:
                platform = BROWSER_PLATFORMS[platform_id]
                result = wait_for_login(browser.latest_tab, platform, env, timeout=max(args.wait_login, 180))
                result["platformId"] = platform_id
                result["platform"] = platform["name"]
                results.append(result)
        finally:
            try:
                browser.quit()
            except Exception:  # noqa: BLE001
                pass
        print(json.dumps({"loggedIn": any(item.get("loggedIn") for item in results), "results": results}, ensure_ascii=False))
        return 0

    if not args.product.strip():
        print(json.dumps({"error": "缺少商品名称"}, ensure_ascii=False))
        return 2
    ids = [item.strip() for item in args.platforms.split(",") if item.strip()]
    result = run_collect(
        args.product.strip(),
        ids,
        limit=args.limit,
        wait_login=args.wait_login,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())


def collect_cli(args: list[str], timeout: int = 300) -> dict:
    """Run this module inside the collector virtualenv and return its JSON.

    The service itself stays standard-library only, so the browser work
    happens in a child process with its own dependencies.
    """
    import subprocess

    python = venv_python()
    command = [str(python), "-m", "prospector.collectors.browser", *args]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("浏览器采集超时，请先在平台登录一次再试。") from exc
    output = (completed.stdout or "").strip()
    if not output:
        detail = (completed.stderr or "浏览器采集没有返回结果。").strip()
        raise RuntimeError(detail[-500:] or "浏览器采集没有返回结果。")
    try:
        return json.loads(output.splitlines()[-1])
    except json.JSONDecodeError as exc:
        raise RuntimeError("浏览器采集返回的不是有效结果。") from exc


def status_cli() -> dict:
    """Report the browser setup without opening a window."""
    return status()
