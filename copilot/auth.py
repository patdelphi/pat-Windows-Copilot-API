"""程序说明：为纯 HTTP 驱动缓存并刷新浏览器登录态与聊天协议快照。

Bridges the interactive browser login to the headless :class:`copilot.client.Copilot`
driver: keeps a short-lived snapshot of cookies + MSAL access token on disk and
transparently refreshes it from the persistent browser profile when it goes stale.
"""

import json
import time
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

# All session state (browser profile + cached auth) lives under one folder.
SESSION_DIR = "session"
DEFAULT_PROFILE_DIR = f"{SESSION_DIR}/profile"
DEFAULT_AUTH_FILE = f"{SESSION_DIR}/token.json"
DEFAULT_WS_CAPTURE_FILE = f"{SESSION_DIR}/ws_capture.log"
# Microsoft access tokens live ~60-90 min; refresh well before that.
AUTH_MAX_AGE = 50 * 60


def _has_chat_hub_snapshot(cached: dict) -> bool:
    """当前新协议要求 token 快照里同时存在 ChatHub 元数据和发送模板。"""
    return bool(cached.get("chat_hub") and cached.get("chat_request_template"))


def _has_cookie_snapshot(cached: dict) -> bool:
    """纯 HTTP 文本链路需要保留域名信息完整的 Cookie 明细。"""
    records = cached.get("cookie_records") or []
    if not records:
        return False
    return any("cloud.microsoft" in str(item.get("domain", "")).lower() for item in records)


def _extract_first_json(payload: str) -> Optional[dict]:
    """从一行混有分隔符的 ws 日志里提取第一个完整 JSON 对象。"""
    start = payload.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    escape = False
    end = None
    for idx, ch in enumerate(payload[start:], start):
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = idx + 1
                break
    if end is None:
        return None
    try:
        return json.loads(payload[start:end])
    except ValueError:
        return None


def _load_chat_hub_snapshot(path: str = DEFAULT_WS_CAPTURE_FILE) -> Optional[dict]:
    """从诊断抓包里回填新协议所需的 ChatHub URL 和发送模板。"""
    p = Path(path)
    if not p.exists():
        return None
    try:
        lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return None
    open_line = next(
        (line for line in lines if "wss://substrate.office.com/m365Copilot/Chathub/" in line),
        None,
    )
    sent_line = next((line for line in lines if line.startswith("[SENT] {\"arguments\":")), None)
    if not open_line or not sent_line:
        return None
    url = open_line[open_line.index("wss://"):]
    parsed = urlparse(url)
    frame = _extract_first_json(sent_line)
    if not parsed.path or frame is None:
        return None
    return {
        "chat_hub": {
            "path": parsed.path,
            "query": {k: v[-1] for k, v in parse_qs(parsed.query).items() if v},
        },
        "chat_request_template": frame,
    }


def _merge_chat_hub_snapshot(auth: dict, fallback: Optional[dict] = None) -> dict:
    """确保导出的 auth 快照里始终带有 ChatHub 协议字段。"""
    if _has_chat_hub_snapshot(auth):
        return auth
    snapshot = fallback or _load_chat_hub_snapshot()
    if snapshot:
        auth.update(snapshot)
    return auth


def load_auth(
    path: str = DEFAULT_AUTH_FILE,
    profile_dir: str = DEFAULT_PROFILE_DIR,
    max_age: int = AUTH_MAX_AGE,
    proxy: Optional[str] = None,
    auto_login: bool = True,
) -> dict:
    """Return ``{cookies, access_token, saved_at}`` for the signed-in user.

    Uses the cached snapshot at ``path`` while fresh; otherwise spins up a
    headless browser against the persistent ``profile_dir`` to read a fresh MSAL
    token (the profile stays signed in via its long-lived refresh token) and
    re-snapshots.

    When the profile is *not* signed in (e.g. first-ever use) and ``auto_login``
    is true, this opens a visible browser for interactive Microsoft sign-in
    instead of failing — so the very first call just works. Set
    ``auto_login=False`` (or run headless/CI) to get a ``RuntimeError`` instead.

    Intended for the pure-HTTP :class:`copilot.client.Copilot` path::

        auth = load_auth()
        Copilot().create_completion(..., cookies=auth["cookies"],
                                    access_token=auth["access_token"])
    """
    p = Path(path)
    cached_snapshot = None
    if p.exists():
        try:
            cached = json.loads(p.read_text(encoding="utf-8"))
            if _has_chat_hub_snapshot(cached):
                cached_snapshot = {
                    "chat_hub": cached.get("chat_hub"),
                    "chat_request_template": cached.get("chat_request_template"),
                }
            if cached.get("access_token") and not _has_chat_hub_snapshot(cached):
                snapshot = _load_chat_hub_snapshot()
                if snapshot:
                    cached.update(snapshot)
                    try:
                        p.write_text(json.dumps(cached, indent=2), encoding="utf-8")
                    except OSError:
                        pass
            if (
                cached.get("access_token")
                and _has_chat_hub_snapshot(cached)
                and _has_cookie_snapshot(cached)
                and (time.time() - cached.get("saved_at", 0)) < max_age
            ):
                return cached
        except (ValueError, OSError):
            pass  # corrupt/unreadable -> refresh below

    from .browser import BrowserCopilot

    # Try a headless read first: a signed-in profile just needs a fresh token.
    # For encrypted-cache sessions (e.g. Google) the token can't be read from
    # storage, so acquire_chat_token warms up one turn to capture it off the chat
    # socket; Microsoft sessions return their cached token instantly (no warm-up).
    bot = BrowserCopilot(profile_dir=profile_dir, headless=True, proxy=proxy)
    try:
        bot.start()
        token = bot.acquire_chat_token()
        if token and not bot.region_blocked():
            auth = bot.export_auth(path=path, stamp=time.time())
            auth["access_token"] = token or auth.get("access_token")
            auth = _merge_chat_hub_snapshot(auth, cached_snapshot)
            try:
                p.write_text(json.dumps(auth, indent=2), encoding="utf-8")
            except OSError:
                pass
            return auth
    finally:
        bot.close()

    # No signed-in session in the profile.
    if not auto_login:
        raise RuntimeError(
            "Not signed in (no access token in the browser profile). "
            "Run `python -m copilot login` and sign in first."
        )

    # First-time use: create the session interactively, then return its auth.
    print("No saved Copilot session found — opening a browser to sign in...")
    auth = BrowserCopilot(profile_dir=profile_dir, headless=False, proxy=proxy).login(path=path)
    auth = _merge_chat_hub_snapshot(auth, cached_snapshot)
    try:
        p.write_text(json.dumps(auth, indent=2), encoding="utf-8")
    except OSError:
        pass
    if not auth.get("access_token"):
        raise RuntimeError(
            "Sign-in did not complete (no access token captured). "
            "Re-run and finish the Microsoft sign-in before pressing Enter, "
            "or sign in manually with `python -m copilot login`."
        )
    return auth
