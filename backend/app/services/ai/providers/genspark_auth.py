"""
genspark_auth.py - Genspark browser-backed and TLS-impersonated chat route.

Functions as a drop-in replacement for authnd_auth.py for Genspark (with Genspark Plus).
Talks to the hidden /api/agent/ask_proxy endpoint with curl_cffi (Chrome TLS impersonation)
to bypass Cloudflare JA3/HTTP2 fingerprinting. Automatically imports or refreshes
cookies from your Firefox profile (cookies.sqlite) or automated Google OAuth login.
Includes built-in Tor SOCKS5 proxy support with automatic circuit rotation.
"""

from __future__ import annotations

import argparse
import codecs
import http.cookiejar
import json
import os
import random
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from curl_cffi import requests

# ==============================================================================
# Constants & Configuration
# ==============================================================================

GENSPARK_HOST = "https://www.genspark.ai"
LOGIN_HOST = "https://login.genspark.ai"
B2C_TENANT = "gensparkad.onmicrosoft.com"
B2C_POLICY = "B2C_1_new_login"

ASK_ENDPOINT = f"{GENSPARK_HOST}/api/agent/ask_proxy"
IS_LOGIN_ENDPOINT = f"{GENSPARK_HOST}/api/is_login"
USER_ENDPOINT = f"{GENSPARK_HOST}/api/user"
MODELS_CONFIG_ENDPOINT = f"{GENSPARK_HOST}/api/models_config"

DEFAULT_MODEL = "gpt-5.6-sol"
DEFAULT_IMPERSONATE = "chrome124"
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

DEFAULT_TIMEOUT = 180
DEFAULT_REQUESTS_PER_PROXY = 5
DEFAULT_TOR_HOST = "127.0.0.1"
DEFAULT_TOR_SOCKS_PORT = 9050
DEFAULT_TOR_CONTROL_PORT = 9051

FIREFOX_PROFILE_DIR = "/home/ubuntu/snap/firefox/common/.mozilla/firefox/q1s88uq8.default"
_CANDIDATE_COOKIE_DIRS = [
    os.path.dirname(os.path.abspath(__file__)),
    "/home/ubuntu/nvidia_chat_bot",
    "/home/ubuntu/NovelForge-EN/backend/app/services/ai/providers",
    "/home/ubuntu/NovelForge-EN/backend",
]
_DEFAULT_COOKIE_DIRS = [
    os.path.abspath(d) for d in _CANDIDATE_COOKIE_DIRS
    if os.path.isdir(d) and os.access(d, os.W_OK)
]
if not _DEFAULT_COOKIE_DIRS:
    _DEFAULT_COOKIE_DIRS = [os.path.dirname(os.path.abspath(__file__))]

def _resolve_cookie_file(filename: str) -> str:
    for d in _DEFAULT_COOKIE_DIRS:
        p = os.path.join(d, filename)
        if os.path.exists(p):
            return p
    return os.path.join(_DEFAULT_COOKIE_DIRS[0], filename)

COOKIE_JAR_PATH = _resolve_cookie_file("genspark_cookies.txt")
COOKIE_JSON_PATH = _resolve_cookie_file("genspark_cookies.json")

_cancel_event = threading.Event()
_thread_local = threading.local()
_metadata_lock = threading.Lock()

# Models mapping
KNOWN_MODELS: Dict[str, str] = {
    # OpenAI GPT-5 / GPT-5.6
    "gpt-5.6-sol": "gpt-5.6-sol",
    "gpt-5.6": "gpt-5.6-sol",
    "gpt-5.6-terra": "gpt-5.6-terra",
    "gpt-5.6-luna": "gpt-5.6-luna",
    "chatgpt-5.6": "gpt-5.6-sol",
    "chatgpt-5.6-sol": "gpt-5.6-sol",
    "gpt-5.5": "gpt-5.5",
    "gpt-5.5-pro": "gpt-5.5-pro",
    "gpt-5.4": "gpt-5.4",
    "gpt-5.4-pro": "gpt-5.4-pro",
    "gpt-5.4-mini": "gpt-5.4-mini",
    "gpt-5.4-nano": "gpt-5.4-nano",
    "gpt-5.2-pro": "gpt-5.2-pro",
    "o3-pro": "o3-pro",
    "gpt-4o": "gpt-5.4-mini",
    # Anthropic Claude
    "claude-sonnet-4-6": "claude-sonnet-4-6",
    "claude-sonnet-4.6": "claude-sonnet-4-6",
    "claude-opus-4-7": "claude-opus-4-7",
    "claude-opus-4.7": "claude-opus-4-7",
    "claude-opus-4-6": "claude-opus-4-6",
    "claude-opus-4.6": "claude-opus-4-6",
    "claude-4-5-haiku": "claude-4-5-haiku",
    "claude-3-5-sonnet": "claude-sonnet-4-6",
    # Google Gemini
    "gemini-2.5-pro": "gemini-2.5-pro",
    "gemini-2.0-flash": "gemini-2.5-pro",
    # xAI Grok
    "grok-4": "grok-4.20-0309-non-reasoning",
    "grok-4.20": "grok-4.20-0309-non-reasoning",
    # Moonshot Kimi
    "kimi-k2": "kimi-k2-instruct",
    "kimi-k2.6": "kimi-k2-instruct",
    "moonshotai/kimi-k2.6": "kimi-k2-instruct",
    "moonshotai/kimi-k3": "kimi-k2-instruct",
    "moonshotai/kimi-k2-instruct": "kimi-k2-instruct",
    "deepseek-ai/deepseek-v4-pro-0813": "gpt-5.6-sol",
    "deepseek-ai/deepseek-r1": "o3-pro",
    "deepseek-ai/deepseek-v3": "gpt-5.6-sol",
}

GENSPARK_PRESET_MODELS = [
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "gpt-5.5",
    "gpt-5.4",
    "gpt-5.4-pro",
    "gpt-5.4-mini",
    "o3-pro",
    "claude-sonnet-4-6",
    "claude-opus-4-7",
    "claude-4-5-haiku",
    "gemini-2.5-pro",
    "grok-4.20-0309-non-reasoning",
    "kimi-k2-instruct",
]
AUTHND_PRESET_MODELS = GENSPARK_PRESET_MODELS


# ==============================================================================
# Tor Proxy & Rotation Management (Identical to authnd_auth.py)
# ==============================================================================

class TorProxyManager:
    """Manages Tor proxy allocation, request counters, and circuit rotation."""

    def __init__(
        self,
        host: str = DEFAULT_TOR_HOST,
        socks_port: int = DEFAULT_TOR_SOCKS_PORT,
        control_port: int = DEFAULT_TOR_CONTROL_PORT,
        control_password: str = "",
        max_requests_per_proxy: int = DEFAULT_REQUESTS_PER_PROXY,
    ) -> None:
        self.host = host
        self.socks_port = socks_port
        self.control_port = control_port
        self.control_password = control_password
        self.max_requests_per_proxy = max(1, max_requests_per_proxy)
        self._lock = threading.Lock()
        self._circuit_id = 0
        self._request_count = 0
        self._session_id = uuid.uuid4().hex[:8]

    def renew_tor_circuit(self, log_fn: Optional[Callable[[str], None]] = None) -> bool:
        try:
            with socket.create_connection((self.host, self.control_port), timeout=3) as s:
                auth_cmd = f'AUTHENTICATE "{self.control_password}"\r\n' if self.control_password else "AUTHENTICATE\r\n"
                s.sendall(auth_cmd.encode("utf-8"))
                resp = s.recv(1024).decode("utf-8")
                if "250" not in resp:
                    return False
                s.sendall(b"SIGNAL NEWNYM\r\n")
                resp = s.recv(1024).decode("utf-8")
                if "250" in resp:
                    _log(log_fn, "🧅 Tor: Circuit renewed via Control Port (SIGNAL NEWNYM)")
                    return True
        except Exception:
            pass
        return False

    def get_proxy_url(self) -> str:
        with self._lock:
            user = f"tor_{self._session_id}_{self._circuit_id}"
            pwd = "genspark_circuit"
            return f"socks5h://{user}:{pwd}@{self.host}:{self.socks_port}"

    def record_request_and_rotate_if_needed(self, log_fn: Optional[Callable[[str], None]] = None) -> Tuple[str, bool]:
        with self._lock:
            self._request_count += 1
            current_count = self._request_count
            proxy = f"socks5h://tor_{self._session_id}_{self._circuit_id}:genspark_circuit@{self.host}:{self.socks_port}"
            if current_count >= self.max_requests_per_proxy:
                self._circuit_id += 1
                self._request_count = 0
                _log(log_fn, f"🧅 Tor: Proxy reached limit of {self.max_requests_per_proxy}. Rotating to Circuit #{self._circuit_id}...")
                self.renew_tor_circuit(log_fn)
                new_proxy = f"socks5h://tor_{self._session_id}_{self._circuit_id}:genspark_circuit@{self.host}:{self.socks_port}"
                return new_proxy, True
            return proxy, False

    def force_rotate(self, log_fn: Optional[Callable[[str], None]] = None) -> str:
        with self._lock:
            self._circuit_id += 1
            self._request_count = 0
            _log(log_fn, f"🧅 Tor: Force rotating to Circuit #{self._circuit_id}...")
            self.renew_tor_circuit(log_fn)
            return f"socks5h://tor_{self._session_id}_{self._circuit_id}:genspark_circuit@{self.host}:{self.socks_port}"


_global_tor_manager: Optional[TorProxyManager] = None
_tor_manager_lock = threading.Lock()


def get_tor_manager() -> TorProxyManager:
    global _global_tor_manager
    with _tor_manager_lock:
        if _global_tor_manager is None:
            host = os.getenv("TOR_HOST", DEFAULT_TOR_HOST)
            socks_port = _env_int("TOR_SOCKS_PORT", DEFAULT_TOR_SOCKS_PORT)
            control_port = _env_int("TOR_CONTROL_PORT", DEFAULT_TOR_CONTROL_PORT)
            control_pwd = os.getenv("TOR_CONTROL_PASSWORD", "")
            req_limit = _env_int("GENSPARK_REQUESTS_PER_PROXY", DEFAULT_REQUESTS_PER_PROXY)
            _global_tor_manager = TorProxyManager(
                host=host,
                socks_port=socks_port,
                control_port=control_port,
                control_password=control_pwd,
                max_requests_per_proxy=req_limit,
            )
        return _global_tor_manager


# ==============================================================================
# Helper Functions & Logging
# ==============================================================================

def _env_bool(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return str(val).strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _debug_enabled() -> bool:
    return _env_bool("GENSPARK_DEBUG", False) or _env_bool("AUTHND_DEBUG", False)


def _log(log_fn: Optional[Callable[[str], None]], message: str, *, debug_only: bool = False) -> None:
    if not log_fn:
        return
    if debug_only and not _debug_enabled():
        return
    log_fn(message)


def _labeled_log_fn(log_fn: Optional[Callable[[str], None]], request_label: Optional[str]) -> Optional[Callable[[str], None]]:
    label = str(request_label or "").strip()
    if not log_fn or not label:
        return log_fn

    def _log_with_label(message: str) -> None:
        log_fn(f"[{label}] {message}")

    return _log_with_label


def cancel_stream() -> None:
    _cancel_event.set()


def reset_cancel() -> None:
    _cancel_event.clear()


def _is_cancelled() -> bool:
    return _cancel_event.is_set()


# ==============================================================================
# Firefox SQLite & Cookie Management
# ==============================================================================

def import_cookies_from_firefox(profile_dir: str = FIREFOX_PROFILE_DIR) -> Dict[str, str]:
    """Extract genspark.ai cookies from Firefox profile's cookies.sqlite."""
    db_path = os.path.join(profile_dir, "cookies.sqlite")
    if not os.path.exists(db_path):
        return {}

    tmp_db = f"/tmp/genspark_cookies_{uuid.uuid4().hex[:6]}.sqlite"
    try:
        shutil.copyfile(db_path, tmp_db)
        conn = sqlite3.connect(tmp_db)
        cur = conn.cursor()
        cur.execute(
            "SELECT name, value FROM moz_cookies WHERE host LIKE '%genspark.ai%'"
        )
        cookies = {row[0]: row[1] for row in cur.fetchall()}
        conn.close()
        return cookies
    except Exception:
        return {}
    finally:
        if os.path.exists(tmp_db):
            try:
                os.remove(tmp_db)
            except OSError:
                pass


class GensparkRateLimitError(RuntimeError):
    """Raised when a Genspark account hits rate limit or 5-hour quota."""
    pass


class GensparkSessionManager:
    """Manages curl_cffi sessions, Cloudflare impersonation, and cookies for an account."""

    def __init__(self, cookie_file: str = COOKIE_JSON_PATH, name: Optional[str] = None) -> None:
        self.cookie_file = cookie_file
        self.name = name or os.path.basename(cookie_file).replace("genspark_cookies_", "").replace(".json", "")
        self.lock = threading.Lock()
        self.session = requests.Session(impersonate=DEFAULT_IMPERSONATE)
        self.session.headers.update({
            "User-Agent": DEFAULT_UA,
            "Accept-Language": "en-US,en;q=0.9",
        })
        self.rate_limited_until: float = 0.0
        self.email: Optional[str] = None
        self.plan: Optional[str] = None
        self._load_cookies()

    def is_rate_limited(self) -> bool:
        return time.time() < self.rate_limited_until

    def mark_rate_limited(self, duration_seconds: float = 1800.0) -> None:
        self.rate_limited_until = time.time() + duration_seconds

    def _load_cookies(self) -> None:
        loaded = False
        if os.path.exists(self.cookie_file):
            if self.cookie_file.endswith(".json"):
                try:
                    with open(self.cookie_file, "r") as f:
                        cookie_list = json.load(f)
                    for c in cookie_list:
                        if "genspark" in c.get("domain", ""):
                            self.session.cookies.set(
                                c["name"], c["value"], domain=c.get("domain", ".genspark.ai"), path=c.get("path", "/")
                            )
                    loaded = True
                except Exception:
                    pass
            else:
                try:
                    jar = http.cookiejar.MozillaCookieJar(self.cookie_file)
                    jar.load(ignore_discard=True, ignore_expires=True)
                    for c in jar:
                        self.session.cookies.set(c.name, c.value, domain=c.domain, path=c.path)
                    loaded = True
                except Exception:
                    pass

        # Only the 'default' account falls back to COOKIE_JSON_PATH or active Firefox profile
        if not loaded and self.name == "default":
            if os.path.exists(COOKIE_JSON_PATH) and self.cookie_file != COOKIE_JSON_PATH:
                try:
                    with open(COOKIE_JSON_PATH, "r") as f:
                        cookie_list = json.load(f)
                    for c in cookie_list:
                        if "genspark" in c.get("domain", ""):
                            self.session.cookies.set(
                                c["name"], c["value"], domain=c.get("domain", ".genspark.ai"), path=c.get("path", "/")
                            )
                    loaded = True
                except Exception:
                    pass

            if not loaded:
                ff_cookies = import_cookies_from_firefox()
                if ff_cookies:
                    for k, v in ff_cookies.items():
                        self.session.cookies.set(k, v, domain=".genspark.ai", path="/")

    def save_cookies(self) -> None:
        with self.lock:
            try:
                cookie_dicts = [
                    {"name": c.name, "value": c.value, "domain": c.domain, "path": c.path}
                    for c in self.session.cookies.jar
                ]
                with open(self.cookie_file, "w") as f:
                    json.dump(cookie_dicts, f, indent=2)
            except Exception:
                pass

    def is_logged_in(self, proxy: Optional[str] = None) -> bool:
        try:
            proxies = {"http": proxy, "https": proxy} if proxy else None
            r = self.session.get(IS_LOGIN_ENDPOINT, timeout=12, proxies=proxies)
            if r.status_code != 200:
                return False
            data = r.json()
            is_login = bool(data.get("data", {}).get("is_login") or data.get("is_login"))
            return is_login
        except Exception:
            return False

    def get_user_info(self, proxy: Optional[str] = None) -> Dict[str, Any]:
        try:
            proxies = {"http": proxy, "https": proxy} if proxy else None
            r = self.session.get(USER_ENDPOINT, timeout=12, proxies=proxies)
            if r.status_code == 200:
                data = r.json().get("data", {})
                cogen = data.get("cogen", {})
                self.email = cogen.get("email")
                self.plan = cogen.get("plan")
                return data
        except Exception:
            pass
        return {}


class GensparkAccountPool:
    """Manages pool of multiple Genspark accounts and automatic rate-limit failover."""

    def __init__(self) -> None:
        self.accounts: Dict[str, GensparkSessionManager] = {}
        self._order: List[str] = []
        self._current_index: int = 0
        self._lock = threading.Lock()
        self.discover_accounts()

    def discover_accounts(self) -> None:
        with self._lock:
            for d in _DEFAULT_COOKIE_DIRS:
                if not os.path.exists(d):
                    continue
                for fn in sorted(os.listdir(d)):
                    if fn.endswith(".json") and fn.startswith("genspark_cookies"):
                        path = os.path.join(d, fn)
                        acc_name = fn.replace("genspark_cookies_", "").replace(".json", "")
                        if not acc_name or acc_name == "genspark_cookies":
                            acc_name = "default"
                        if acc_name not in self.accounts:
                            mgr = GensparkSessionManager(cookie_file=path, name=acc_name)
                            self.accounts[acc_name] = mgr
                            if acc_name not in self._order:
                                self._order.append(acc_name)

            if not self.accounts:
                default_mgr = GensparkSessionManager(cookie_file=COOKIE_JSON_PATH, name="default")
                self.accounts["default"] = default_mgr
                self._order.append("default")

    def get_active_account(self, preferred: Optional[str] = None) -> GensparkSessionManager:
        self.discover_accounts()
        with self._lock:
            if preferred and preferred in self.accounts:
                return self.accounts[preferred]
            if not self._order:
                return GensparkSessionManager(cookie_file=COOKIE_JSON_PATH, name="default")

            for i in range(len(self._order)):
                idx = (self._current_index + i) % len(self._order)
                key = self._order[idx]
                mgr = self.accounts[key]
                if not mgr.is_rate_limited():
                    self._current_index = idx
                    return mgr

            # If all accounts are rate-limited, return the one with earliest cooldown expiry
            sorted_mgrs = sorted(self.accounts.values(), key=lambda m: m.rate_limited_until)
            return sorted_mgrs[0]

    def rotate_to_next(
        self,
        current_name: str,
        reason: str = "rate_limit",
        log_fn: Optional[Callable[[str], None]] = None,
    ) -> Optional[GensparkSessionManager]:
        with self._lock:
            # If 5-hour limit or quota notice reached, apply full 5-hour cooldown (18000s); otherwise default to 30 min (1800s)
            reason_lower = (reason or "").lower()
            is_5h = any(k in reason_lower for k in [
                "5-hour", "5 hour", "usage limit", "quota", "limit", "try again in", "exceeded", "429", "hours", "upgrade to plus"
            ])
            cooldown_sec = 18000.0 if is_5h else 1800.0
            cooldown_str = "5 hours" if is_5h else "30 minutes"

            if current_name in self.accounts:
                self.accounts[current_name].mark_rate_limited(cooldown_sec)

            if log_fn:
                log_fn(f"🚨 Genspark Quota Alert: Account [{current_name}] entered {cooldown_str} cooldown ({reason})")

            for i in range(1, len(self._order) + 1):
                idx = (self._current_index + i) % len(self._order)
                key = self._order[idx]
                mgr = self.accounts[key]
                if not mgr.is_rate_limited():
                    self._current_index = idx
                    if log_fn:
                        log_fn(f"🔄 Genspark Pool: Auto-switched account [{current_name}] -> [{key}]")
                    return mgr

            if log_fn:
                log_fn("⚠️ Genspark Pool: All accounts in pool are currently rate-limited.")
            return None

    def list_accounts(self) -> List[Dict[str, Any]]:
        self.discover_accounts()
        results = []
        with self._lock:
            for name, mgr in self.accounts.items():
                info = mgr.get_user_info() if not mgr.email else {}
                cogen = info.get("cogen", {})
                email = mgr.email or cogen.get("email") or name
                plan = mgr.plan or cogen.get("plan") or "unknown"
                remaining_cooldown = max(0, int(mgr.rate_limited_until - time.time()))
                results.append({
                    "name": name,
                    "email": email,
                    "plan": plan,
                    "rate_limited": mgr.is_rate_limited(),
                    "cooldown_remaining_sec": remaining_cooldown,
                    "cookie_file": mgr.cookie_file,
                })
        return results


_global_account_pool: Optional[GensparkAccountPool] = None
_pool_lock = threading.Lock()


def get_account_pool() -> GensparkAccountPool:
    global _global_account_pool
    with _pool_lock:
        if _global_account_pool is None:
            _global_account_pool = GensparkAccountPool()
        return _global_account_pool


def get_session_manager(preferred: Optional[str] = None) -> GensparkSessionManager:
    return get_account_pool().get_active_account(preferred)


def sync_current_firefox_cookies(
    save_as: Optional[str] = None,
    log_fn: Optional[Callable[[str], None]] = None,
) -> Optional[str]:
    """
    Import active Genspark session cookies from Firefox profile.
    Automatically determines email and saves as genspark_cookies_<clean_email>.json.
    """
    cookies = import_cookies_from_firefox()
    if not cookies:
        if log_fn:
            log_fn("❌ No Genspark cookies found in Firefox profile.")
        return None

    s = requests.Session(impersonate=DEFAULT_IMPERSONATE)
    for k, v in cookies.items():
        s.cookies.set(k, v, domain=".genspark.ai", path="/")

    email = "unknown"
    plan = "unknown"
    try:
        r = s.get(USER_ENDPOINT, timeout=12)
        if r.status_code == 200:
            cogen = r.json().get("data", {}).get("cogen", {})
            email = cogen.get("email") or "unknown"
            plan = cogen.get("plan") or "unknown"
    except Exception:
        pass

    clean_email = re.sub(r"[^a-zA-Z0-9_\-]", "_", email)
    file_name = save_as or (f"genspark_cookies_{clean_email}.json" if email != "unknown" else "genspark_cookies_firefox.json")

    cookie_dicts = [
        {"name": k, "value": v, "domain": ".genspark.ai", "path": "/"}
        for k, v in cookies.items()
    ]

    saved_paths = []
    for d in _DEFAULT_COOKIE_DIRS:
        if os.path.exists(d):
            dest = os.path.join(d, file_name)
            with open(dest, "w") as f:
                json.dump(cookie_dicts, f, indent=2)
            saved_paths.append(dest)

    if log_fn:
        log_fn(f"✅ Synced cookies for [{email}] (Plan: {plan}) to {file_name}")

    get_account_pool().discover_accounts()
    return email


# ==============================================================================
# Model Normalization & Prompt Formatting
# ==============================================================================

def _normalize_model(model: str) -> str:
    raw = (model or "").strip().lower()
    raw = re.sub(r"^(?:genspark|authnd\d{0,4})/", "", raw)
    if raw in KNOWN_MODELS:
        return KNOWN_MODELS[raw]
    return raw or DEFAULT_MODEL


def _content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("type") in ("text", "input_text"):
                parts.append(str(item.get("text") or ""))
        return "\n".join(parts)
    return str(content)


def _normalize_messages(messages: Iterable[Dict[str, Any]]) -> str:
    """Flatten messages list into a single conversation prompt for Genspark."""
    system_parts: List[str] = []
    convo_parts: List[str] = []

    for msg in messages or []:
        role = str(msg.get("role") or "user").lower()
        content = _content_to_text(msg.get("content")).strip()
        if not content:
            continue
        if role == "system":
            system_parts.append(content)
        elif role == "assistant":
            convo_parts.append(f"Assistant: {content}")
        else:
            convo_parts.append(f"User: {content}")

    prompt = ""
    if system_parts:
        prompt += "System Instructions:\n" + "\n\n".join(system_parts) + "\n\n"

    if convo_parts:
        if len(convo_parts) == 1 and convo_parts[0].startswith("User: "):
            # Single user turn
            prompt += convo_parts[0][6:]
        else:
            prompt += "\n\n".join(convo_parts)
    return prompt.strip()


# ==============================================================================
# Response Parsing & SSE Streaming
# ==============================================================================

def _iter_sse_stream(
    response: Any,
    *,
    log_fn: Optional[Callable[[str], None]] = None,
    log_stream: bool = True,
    t_start: Optional[float] = None,
    chunk_callback: Optional[Callable[[str, Optional[str]], None]] = None,
) -> Dict[str, Any]:
    started_at = t_start or time.time()
    content_parts: List[str] = []
    reasoning_parts: List[str] = []
    saw_done = False
    first_token_ts: Optional[float] = None
    stream_chunk_buffer: List[str] = []

    for raw in response.iter_lines():
        if _is_cancelled():
            raise RuntimeError("stream cancelled")
        if not raw:
            continue
        line = raw.decode("utf-8", errors="replace") if isinstance(raw, (bytes, bytearray)) else raw
        line = line.strip()

        if line.startswith("data:"):
            line = line[5:].strip()

        if not line or line == "[DONE]":
            if line == "[DONE]":
                saw_done = True
                break
            continue

        try:
            obj = json.loads(line)
        except (ValueError, json.JSONDecodeError):
            continue

        msg_type = obj.get("type")
        field = obj.get("field_name")
        delta = obj.get("delta") or ""

        # Handle reasoning / thinking deltas
        if field in ("reasoning_content", "thinking", "thought") or msg_type in ("thought", "reasoning"):
            thought = delta or obj.get("thought") or obj.get("reasoning") or ""
            if thought:
                if first_token_ts is None:
                    first_token_ts = time.time()
                    if log_stream:
                        _log(log_fn, f"⏱️ Genspark: First token in {first_token_ts - started_at:.1f}s (thinking)...")
                reasoning_parts.append(thought)
                if chunk_callback:
                    chunk_callback("", thought)

        # Handle text deltas
        if (msg_type == "message_field_delta" and field == "content") or (msg_type == "delta" and delta):
            if first_token_ts is None:
                first_token_ts = time.time()
                if log_stream:
                    _log(log_fn, f"⏱️ Genspark: First token in {first_token_ts - started_at:.1f}s, streaming...")
            content_parts.append(delta)
            if chunk_callback:
                chunk_callback(delta, None)
            if log_fn and log_stream:
                stream_chunk_buffer.append(delta)
                combined = "".join(stream_chunk_buffer)
                if "\n" in combined:
                    lines = combined.split("\n")
                    for l in lines[:-1]:
                        if l:
                            log_fn(l)
                    stream_chunk_buffer[:] = [lines[-1]]
                elif len(combined) >= 120:
                    log_fn(combined)
                    stream_chunk_buffer.clear()

        # Handle full message result (final summary or quota notice)
        if msg_type == "message_result":
            msg_obj = obj.get("message") or {}
            res_content = msg_obj.get("content") or ""
            is_quota_notice = (
                len(res_content) < 300
                and any(
                    phrase in res_content.lower()
                    for phrase in [
                        "rate limit",
                        "usage limit",
                        "quota exceeded",
                        "reached your limit",
                        "reached the limit",
                        "daily limit reached",
                        "free limit",
                        "plan limit",
                    ]
                )
            )
            if is_quota_notice:
                _log(log_fn, f"⚠️ Genspark limit notice: {res_content}")
                raise GensparkRateLimitError(res_content)
            if not content_parts and res_content:
                content_parts.append(res_content)
                if chunk_callback:
                    chunk_callback(res_content, None)

    if log_fn and log_stream and stream_chunk_buffer:
        tail = "".join(stream_chunk_buffer).strip()
        if tail:
            log_fn(tail)

    full_content = "".join(content_parts).strip()
    full_reasoning = "".join(reasoning_parts) if reasoning_parts else None
    elapsed = time.time() - started_at

    # Check if stream body itself contained a quota/rate limit notice
    is_quota_in_full = (
        len(full_content) < 400
        and any(
            phrase in full_content.lower()
            for phrase in [
                "rate limit",
                "usage limit",
                "quota exceeded",
                "reached your limit",
                "reached the limit",
                "daily limit reached",
                "free limit",
                "plan limit",
                "5-hour limit",
                "5 hour limit",
                "try again in 5 hours",
                "try again in",
                "hours until reset",
                "upgrade to plus",
            ]
        )
    )
    if is_quota_in_full:
        _log(log_fn, f"⚠️ Genspark limit notice detected in stream body: {full_content}")
        raise GensparkRateLimitError(full_content)

    if log_stream:
        _log(log_fn, f"✅ Genspark: Stream finished in {elapsed:.1f}s ({len(full_content):,} chars)")

    return {
        "content": full_content,
        "finish_reason": "stop" if (saw_done or full_content) else "incomplete",
        "finish_reason_explicit": saw_done,
        "finish_reason_inference": "provider",
        "usage": {
            "completion_tokens": max(1, len(full_content) // 4),
            "prompt_tokens": 100,
            "total_tokens": max(1, len(full_content) // 4) + 100,
        },
        "reasoning_content": full_reasoning,
        "raw_response": full_content,
    }


# ==============================================================================
# Public API Entrypoint
# ==============================================================================

def send_chat_completion(
    *,
    messages: Iterable[Dict[str, Any]],
    model: str = DEFAULT_MODEL,
    temperature: Optional[float] = 0.3,
    max_tokens: Optional[int] = 32768,
    top_p: Optional[float] = None,
    frequency_penalty: Optional[float] = None,
    presence_penalty: Optional[float] = None,
    timeout: Optional[int] = None,
    log_fn: Optional[Callable[[str], None]] = None,
    connect_timeout: Optional[float] = None,
    account_id: int = 0,
    stream: Optional[bool] = None,
    log_stream: Optional[bool] = None,
    progress_label: Optional[str] = None,
    proxy: Optional[str] = None,
    use_tor: bool = False,
    reasoning_enabled: Optional[bool] = None,
    reasoning_effort: Optional[str] = None,
    request_label: Optional[str] = None,
    enable_search: bool = False,
    is_private: bool = True,
    project_id: Optional[str] = None,
    chunk_callback: Optional[Callable[[str, Optional[str]], None]] = None,
) -> Dict[str, Any]:
    """
    Send chat completion to Genspark's hidden ask_proxy endpoint.
    Fully compatible with authnd_auth.send_chat_completion signature.
    Includes multi-account pool support with automatic rate-limit failover.
    """
    del account_id, top_p, frequency_penalty, presence_penalty, reasoning_effort

    if _is_cancelled():
        raise RuntimeError("stream cancelled")

    log_fn = _labeled_log_fn(log_fn, request_label)

    # Tor / Proxy setup
    effective_use_tor = use_tor or _env_bool("GENSPARK_USE_TOR", False) or _env_bool("AUTHND_USE_TOR", False)
    active_proxy = proxy
    tor_manager = None
    if effective_use_tor:
        tor_manager = get_tor_manager()
        active_proxy, _ = tor_manager.record_request_and_rotate_if_needed(log_fn)

    target_model = _normalize_model(model)
    timeout_val = timeout or _env_int("GENSPARK_TIMEOUT", DEFAULT_TIMEOUT)
    use_stream = True if stream is None else bool(stream)
    status_logs = True if log_stream is None else bool(log_stream)

    pool = get_account_pool()
    max_retries = max(2, len(pool.accounts))
    retry_count = 0

    while retry_count < max_retries:
        manager = pool.get_active_account()

        # Verify or sync cookies from Firefox if not logged in
        if not manager.is_logged_in(proxy=active_proxy):
            _log(log_fn, f"🔄 Genspark [{manager.name}]: Session not logged in, syncing cookies...")
            manager._load_cookies()

        prompt = _normalize_messages(messages)
        msg_id = str(uuid.uuid4())
        message_payload = {"role": "user", "id": msg_id, "content": prompt}

        eff_temp = float(temperature) if temperature is not None else 0.3
        eff_max_tokens = int(max_tokens) if max_tokens is not None and max_tokens > 0 else 32768

        payload = {
            "ai_chat_model": target_model,
            "ai_chat_enable_search": bool(enable_search),
            "ai_chat_disable_personalization": False,
            "use_moa_proxy": False,
            "moa_models": [],
            "writingContent": None,
            "type": "ai_chat",
            "project_id": project_id,
            "messages": [message_payload],
            "user_s_input": prompt,
            "g_recaptcha_token": "",
            "is_private": bool(is_private),
            "push_token": "",
            "session_state": {"steps": [], "messages": [message_payload]},
            "temperature": eff_temp,
            "max_tokens": eff_max_tokens,
        }

        headers = {
            "Origin": GENSPARK_HOST,
            "Referer": f"{GENSPARK_HOST}/agents?type=ai_chat",
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if use_stream else "*/*",
        }

        proxies = {"http": active_proxy, "https": active_proxy} if active_proxy else None
        proxy_note = f" via Tor ({active_proxy})" if effective_use_tor else (f" via {active_proxy}" if active_proxy else "")

        _log(log_fn, f"🌐 Genspark [{manager.name}]: dispatching {target_model}{proxy_note} (chars={len(prompt):,}, temp={eff_temp}, max_tokens={eff_max_tokens})...")
        started_at = time.time()

        try:
            response = manager.session.post(
                ASK_ENDPOINT,
                headers=headers,
                json=payload,
                stream=True,
                timeout=timeout_val,
                proxies=proxies,
            )
        except Exception as exc:
            if effective_use_tor and tor_manager:
                tor_manager.force_rotate(log_fn)
            raise RuntimeError(f"Genspark HTTP dispatch error: {exc}") from exc

        if response.status_code == 429:
            _log(log_fn, f"⚠️ Genspark HTTP 429 on account [{manager.name}]. Rotating...")
            next_mgr = pool.rotate_to_next(manager.name, reason="HTTP 429", log_fn=log_fn)
            if next_mgr and next_mgr != manager:
                retry_count += 1
                continue
            raise RuntimeError("Genspark HTTP 429: Rate limit hit on all available accounts.")

        if response.status_code >= 400:
            if effective_use_tor and tor_manager:
                tor_manager.force_rotate(log_fn)
            body = (response.text or "").strip()
            raise RuntimeError(f"Genspark HTTP {response.status_code}: {body[:300]}")

        manager.save_cookies()

        try:
            return _iter_sse_stream(response, log_fn=log_fn, log_stream=status_logs, t_start=started_at, chunk_callback=chunk_callback)
        except GensparkRateLimitError as rle:
            _log(log_fn, f"⚠️ Genspark: Account [{manager.name}] rate-limited: {rle}")
            next_mgr = pool.rotate_to_next(manager.name, reason=str(rle), log_fn=log_fn)
            if next_mgr and next_mgr != manager:
                _log(log_fn, f"🔄 Genspark: Switching to account [{next_mgr.name}] and retrying prompt...")
                retry_count += 1
                continue
            raise


# ==============================================================================
# Interactive Login Helper
# ==============================================================================

def interactive_login(email: str = "niggrniggr619@gmail.com", display: str = ":1.0") -> bool:
    """
    Launch Firefox to Genspark's login flow so user can authenticate Google account.
    """
    print(f"[*] Opening Genspark Google login on DISPLAY={display}...")
    env = os.environ.copy()
    env["DISPLAY"] = display
    cmd = [
        "firefox",
        "--new-tab",
        f"{GENSPARK_HOST}/api/login?redirect_url={GENSPARK_HOST}/",
    ]
    subprocess.Popen(cmd, env=env)
    print("[*] Browser window launched. Complete the Google sign-in for your Genspark Plus account.")
    print("[*] Waiting up to 120 seconds for active Genspark session...")
    
    manager = get_session_manager()
    for i in range(24):
        time.sleep(5)
        manager._load_cookies()
        if manager.is_logged_in():
            user = manager.get_user_info()
            print(f"🎉 Login successful! Active Genspark user: {user.get('email') or user.get('nickname') or 'Plus Subscriber'}")
            manager.save_cookies()
            return True
        print(f"   [{i*5}s] Waiting for login completion...")

    print("⚠️ Timed out waiting for login. Please ensure you clicked 'Continue with Google' and completed sign in.")
    return False


# ==============================================================================
# CLI Testing Interface
# ==============================================================================

def main() -> None:
    parser = argparse.ArgumentParser(description="Genspark AI Chat / Completion Endpoint")
    parser.add_argument("--prompt", "-p", type=str, default="Hello! Confirm you are working and state what model you are.")
    parser.add_argument("--model", "-m", type=str, default=DEFAULT_MODEL, help="Model ID (e.g. gpt-5.6-sol, claude-sonnet-4-6, gpt-5.4, gemini-2.5-pro)")
    parser.add_argument("--check-auth", action="store_true", help="Check if current session is authenticated")
    parser.add_argument("--login", action="store_true", help="Launch browser login for Google account")
    parser.add_argument("--sync-firefox", action="store_true", help="Import current active cookies from Firefox profile into account pool")
    parser.add_argument("--list-accounts", action="store_true", help="List all accounts in the pool and rate-limit status")
    parser.add_argument("--account", type=str, default=None, help="Target a specific account name in the pool")
    parser.add_argument("--tor", action="store_true", help="Route requests through Tor proxy")
    args = parser.parse_args()

    pool = get_account_pool()

    if args.list_accounts:
        accs = pool.list_accounts()
        print(f"\n📋 Genspark Account Pool ({len(accs)} accounts registered):")
        for a in accs:
            rl = f"⛔ RATE-LIMITED (cooldown: {a['cooldown_remaining_sec']}s)" if a['rate_limited'] else "🟢 ACTIVE"
            print(f"  • [{a['name']}] Email: {a['email']} | Plan: {a['plan']} | Status: {rl}")
            print(f"    Cookie file: {a['cookie_file']}")
        return

    if args.sync_firefox:
        print("[*] Syncing cookies from Firefox profile into account pool...")
        email = sync_current_firefox_cookies(log_fn=print)
        if email:
            print(f"🎉 Successfully imported session for {email} into account pool!")
        else:
            print("❌ Failed to import session from Firefox.")
        return

    manager = pool.get_active_account(args.account)

    if args.check_auth:
        logged_in = manager.is_logged_in()
        print(f"Genspark Login Status [{manager.name}]: {'✅ LOGGED IN' if logged_in else '❌ NOT LOGGED IN'}")
        if logged_in:
            user = manager.get_user_info()
            print("User info:", json.dumps(user, indent=2))
        return

    if args.login:
        interactive_login()
        return

    print(f"[*] Sending prompt to Genspark ({args.model}) via account [{manager.name}]...")
    result = send_chat_completion(
        messages=[{"role": "user", "content": args.prompt}],
        model=args.model,
        use_tor=args.tor,
        log_fn=print,
    )
    print("\n--- Response ---")
    print(result.get("content"))
    if result.get("reasoning_content"):
        print("\n--- Reasoning ---")
        print(result.get("reasoning_content"))


if __name__ == "__main__":
    main()
