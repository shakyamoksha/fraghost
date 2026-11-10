from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path

from .paths import data_dir
from .secrets import read_password, write_password

DEFAULTS: dict = {
    "save_path": "",
    "completed_path": "",
    "move_completed": False,
    "watch_path": "",
    "watch_enabled": False,
    "show_add_dialog": True,
    "preallocate": False,
    "listen_port": 6881,
    "random_port": False,
    "dht": True,
    "pex": True,
    "lsd": True,
    "utp": True,
    "upnp": True,
    "natpmp": True,
    "encryption": "enabled",
    "anonymous_mode": False,
    "announce_to_all_trackers": True,
    "max_connections": 200,
    "max_uploads": 8,
    "download_limit": 0,
    "upload_limit": 0,
    "alt_download_limit": 50,
    "alt_upload_limit": 20,
    "scheduler_enabled": False,
    "scheduler": "F" * 168,
    "max_active_downloads": 5,
    "max_active_seeds": 5,
    "max_active": 10,
    "seed_ratio": 0.0,
    "seed_minutes": 0,
    "vpn_enabled": False,
    "proxy_type": "none",
    "proxy_host": "",
    "proxy_port": 0,
    "proxy_username": "",
    "proxy_password": "",
    "proxy_peers": True,
    "proxy_hostnames": True,
    "web_password": "",
    "blocklist_path": "",
    "rss_feeds": [],
    "disk_cache_mb": 32,
    "confirm_remove": True,
    "theme": "system",
    "update_repo": "",
    "update_mode": "notify",
    "ghost_enabled": False,
    "ghost_endpoint_id": "",
}

_BOOL = {
    "move_completed",
    "watch_enabled",
    "show_add_dialog",
    "preallocate",
    "random_port",
    "dht",
    "pex",
    "lsd",
    "utp",
    "upnp",
    "natpmp",
    "anonymous_mode",
    "announce_to_all_trackers",
    "vpn_enabled",
    "ghost_enabled",
    "proxy_peers",
    "proxy_hostnames",
    "scheduler_enabled",
    "confirm_remove",
}
_INT = {
    "listen_port": (0, 65535),
    "max_connections": (2, 10000),
    "max_uploads": (1, 1000),
    "download_limit": (0, 1_000_000),
    "upload_limit": (0, 1_000_000),
    "alt_download_limit": (0, 1_000_000),
    "alt_upload_limit": (0, 1_000_000),
    "max_active_downloads": (1, 1000),
    "max_active_seeds": (1, 1000),
    "max_active": (1, 2000),
    "seed_minutes": (0, 1_000_000),
    "proxy_port": (0, 65535),
    "disk_cache_mb": (1, 4096),
}
_TEXT = {
    "save_path",
    "completed_path",
    "watch_path",
    "proxy_host",
    "proxy_username",
    "proxy_password",
    "web_password",
    "blocklist_path",
    "update_repo",
}


def settings_path() -> Path:
    return data_dir() / "settings.json"


def default_download_dir() -> str:
    return str(Path.home() / "Downloads")


def schedule_mode(settings: dict, when: time.struct_time | None = None) -> str:
    if not settings.get("scheduler_enabled"):
        return "F"
    grid = str(settings.get("scheduler") or "")
    if len(grid) != 168:
        return "F"
    moment = when or time.localtime()
    cell = grid[moment.tm_wday * 24 + moment.tm_hour]
    return cell if cell in "FLS" else "F"


def load_settings() -> dict:
    path = settings_path()
    raw: dict = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                raw = loaded
        except (OSError, json.JSONDecodeError):
            raw = {}
    merged = dict(DEFAULTS)
    for key in DEFAULTS:
        if key in raw:
            try:
                merged[key] = _coerce(key, raw[key], strict=False)
            except ValueError:
                pass
    if not merged["save_path"]:
        merged["save_path"] = default_download_dir()
    plain = str(merged.get("proxy_password") or "")
    if plain:
        if write_password(plain):
            merged["proxy_password"] = plain
            _write_public(merged)
        return merged
    merged["proxy_password"] = read_password()
    return merged


def save_settings(settings: dict) -> None:
    password = str(settings.get("proxy_password") or "")
    if password and not write_password(password):
        _write_public(settings, keep_password=True)
        return
    if not password:
        write_password("")
    _write_public(settings, keep_password=False)


def _write_public(settings: dict, *, keep_password: bool = False) -> None:
    stored = {key: settings.get(key, DEFAULTS[key]) for key in DEFAULTS}
    if not keep_password:
        stored["proxy_password"] = ""
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_bytes(json.dumps(stored, indent=2).encode("utf-8"))
    temporary.replace(path)


def public_settings(settings: dict) -> dict:
    view = {key: settings.get(key, DEFAULTS[key]) for key in DEFAULTS}
    view["proxy_has_password"] = bool(settings.get("proxy_password"))
    view["proxy_password"] = ""
    return view


def merge_settings(current: dict, patch: dict) -> dict:
    if not isinstance(patch, dict):
        raise ValueError("Settings must be an object")
    nxt = dict(current)
    for key, value in patch.items():
        if key not in DEFAULTS:
            continue
        if key == "rss_feeds":
            nxt[key] = _merge_feeds(current.get("rss_feeds") or [], value)
        else:
            nxt[key] = _coerce(key, value, strict=True)
    if not nxt["save_path"]:
        nxt["save_path"] = default_download_dir()
    return nxt


def _merge_feeds(previous: list, value) -> list:
    cleaned = _coerce("rss_feeds", value, strict=True)
    prior = {item["id"]: item for item in previous if isinstance(item, dict) and item.get("id")}
    for feed in cleaned:
        old = prior.get(feed["id"])
        if not old:
            continue
        feed["seen"] = list(dict.fromkeys([*old.get("seen", []), *feed.get("seen", [])]))[:5000]
    return cleaned


def _coerce(key: str, value, strict: bool):
    if key in _BOOL:
        return bool(value)
    if key in _INT:
        low, high = _INT[key]
        try:
            number = int(value)
        except (TypeError, ValueError) as exc:
            if strict:
                raise ValueError(f"{key} must be a number") from exc
            return DEFAULTS[key]
        if number < low or number > high:
            if strict:
                raise ValueError(f"{key} must be between {low} and {high}")
            return DEFAULTS[key]
        return number
    if key == "seed_ratio":
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            if strict:
                raise ValueError("Seed ratio must be a number") from exc
            return 0.0
        if number < 0:
            if strict:
                raise ValueError("Seed ratio cannot be negative")
            return 0.0
        return number
    if key == "encryption":
        if value not in ("enabled", "forced", "disabled"):
            if strict:
                raise ValueError("Encryption must be enabled, forced, or disabled")
            return "enabled"
        return value
    if key == "update_repo":
        text = str(value or "").strip()
        if text and not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", text):
            if strict:
                raise ValueError("Update repository must look like owner/name")
            return ""
        return text
    if key == "update_mode":
        text = str(value or "").strip().lower()
        if text not in ("off", "notify", "auto"):
            if strict:
                raise ValueError("Update mode must be off, notify, or auto")
            return "notify"
        return text
    if key == "theme":
        if value not in ("system", "light", "dark"):
            if strict:
                raise ValueError("Theme must be system, light, or dark")
            return "system"
        return value
    if key == "ghost_endpoint_id":
        text = str(value or "").strip().lower()
        if text and not re.fullmatch(r"[0-9a-f]{32}", text):
            if strict:
                raise ValueError("Unknown Ghost Mode endpoint")
            return ""
        return text
    if key == "proxy_type":
        allowed = ("none", "socks4", "socks5", "socks5_pw", "http", "http_pw")
        if value not in allowed:
            if strict:
                raise ValueError("Unsupported proxy type")
            return "none"
        return value
    if key == "scheduler":
        text = "".join(ch if ch in "FLS" else "F" for ch in str(value))
        if len(text) != 168:
            if strict:
                raise ValueError("The scheduler must cover all 7 days and 24 hours")
            return "F" * 168
        return text
    if key == "rss_feeds":
        return _clean_feeds(value, strict)
    if key in _TEXT:
        return str(value or "")
    return DEFAULTS[key]


def _clean_feeds(value, strict: bool) -> list:
    if not isinstance(value, list):
        if strict:
            raise ValueError("RSS feeds must be a list")
        return []
    feeds = []
    for item in value[:100]:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            continue
        feeds.append(
            {
                "id": str(item.get("id") or uuid.uuid4()),
                "url": url,
                "enabled": bool(item.get("enabled", True)),
                "filter": str(item.get("filter") or ""),
                "label": str(item.get("label") or "")[:80],
                "save_path": str(item.get("save_path") or ""),
                "seen": [str(entry) for entry in (item.get("seen") or [])][:5000],
            }
        )
    return feeds
