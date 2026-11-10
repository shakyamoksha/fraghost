"""Endpoints the user saved. Ghost Mode never looks for a public server."""

from __future__ import annotations

import json
import uuid

from .ghost_identity import decode_key, encode_key
from .paths import data_dir
from .secrets import write_secret

_LIMIT = 32


def list_public() -> list[dict]:
    return [_public(item) for item in _load()]


def get(endpoint_id: str) -> dict | None:
    wanted = str(endpoint_id or "")
    for item in _load():
        if item["id"] == wanted:
            return dict(item)
    return None


def add(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("Endpoint details are required.")
    item = _validate(payload)
    items = _load()
    if len(items) >= _LIMIT:
        raise ValueError("Remove an endpoint before adding another.")
    psk = item.pop("preshared")
    item["has_preshared"] = False
    if psk:
        if not write_secret(_psk_name(item["id"]), psk):
            raise ValueError("The preshared key could not be saved.")
        item["has_preshared"] = True
    items.append(item)
    _save(items)
    return _public(item)


def remove(endpoint_id: str) -> None:
    wanted = str(endpoint_id or "")
    items = _load()
    kept = [item for item in items if item["id"] != wanted]
    if len(kept) == len(items):
        raise ValueError("That endpoint is not saved.")
    write_secret(_psk_name(wanted), b"")
    _save(kept)


def _validate(payload: dict) -> dict:
    host = _text(payload.get("host"), 253)
    if "://" in host or "/" in host or " " in host or host.startswith("."):
        raise ValueError("Endpoint host must be a hostname or address, not a URL.")
    try:
        port = int(payload.get("port") or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("Endpoint port must be a number.") from exc
    if port < 1 or port > 65535:
        raise ValueError("Endpoint port must be between 1 and 65535.")
    public = decode_key(payload.get("public_key"))
    name = _text(payload.get("name"), 64) or host
    country = _text(payload.get("country"), 32)
    psk = _preshared(payload.get("preshared_key"))
    return {
        "id": uuid.uuid4().hex,
        "name": name,
        "host": host,
        "port": port,
        "public_key": encode_key(public),
        "country": country,
        "preshared": psk,
    }


def _preshared(value) -> bytes:
    text = str(value or "").strip()
    if not text:
        return b""
    try:
        raw = decode_key(text)
    except ValueError as exc:
        raise ValueError("The preshared key is not valid.") from exc
    return raw


def _text(value, limit: int) -> str:
    text = str(value or "").strip()
    if any(ch in text for ch in "\r\n\x00"):
        raise ValueError("That endpoint field is not valid.")
    return text[:limit]


def _public(item: dict) -> dict:
    return {
        "id": item["id"],
        "name": item["name"],
        "host": item["host"],
        "port": item["port"],
        "public_key": item["public_key"],
        "country": item.get("country") or "",
        "has_preshared": bool(item.get("has_preshared")),
    }


def _psk_name(endpoint_id: str) -> str:
    return f"ghost_psk_{endpoint_id}"


def _path():
    return data_dir() / "ghost-endpoints.json"


def _load() -> list[dict]:
    path = _path()
    if not path.exists():
        return []
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = loaded.get("endpoints") if isinstance(loaded, dict) else None
    if not isinstance(rows, list):
        return []
    clean = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        try:
            public = decode_key(item.get("public_key"))
            port = int(item.get("port") or 0)
            host = _text(item.get("host"), 253)
            endpoint_id = str(item.get("id") or "")
            if len(endpoint_id) != 32 or port < 1 or port > 65535 or not host:
                continue
            clean.append(
                {
                    "id": endpoint_id,
                    "name": _text(item.get("name"), 64) or host,
                    "host": host,
                    "port": port,
                    "public_key": encode_key(public),
                    "country": _text(item.get("country"), 32),
                    "has_preshared": bool(item.get("has_preshared")),
                }
            )
        except (TypeError, ValueError):
            continue
    return clean


def _save(items: list[dict]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    body = json.dumps({"endpoints": [_public(item) for item in items]}, indent=2)
    temporary.write_bytes(body.encode("utf-8"))
    temporary.replace(path)
