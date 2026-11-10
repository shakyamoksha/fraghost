"""Store the proxy password with Windows DPAPI. The React UI never receives it."""

from __future__ import annotations

import base64
import ctypes
import json
import re
import sys
from ctypes import wintypes

from .paths import data_dir

_UI_FORBIDDEN = 0x01


class _Blob(ctypes.Structure):
    _fields_ = (("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char)))


def secrets_path():
    return data_dir() / "secrets.json"


def read_password() -> str:
    try:
        return read_secret("proxy_password").decode("utf-8")
    except UnicodeError:
        return ""


def write_password(password: str) -> bool:
    """Persist a password. An empty value removes it. False means DPAPI refused and nothing was discarded."""
    if not password:
        return write_secret("proxy_password", b"")
    return write_secret("proxy_password", password.encode("utf-8"))


def read_secret(name: str) -> bytes:
    if not _secret_name(name):
        return b""
    raw = _read().get(name) or ""
    if not isinstance(raw, str) or not raw:
        return b""
    try:
        return _unprotect(base64.b64decode(raw))
    except (OSError, ValueError):
        return b""


def write_secret(name: str, data: bytes) -> bool:
    """Store bytes with DPAPI. Empty data removes the name. False leaves the previous value in place."""
    if not _secret_name(name):
        return False
    current = _read()
    if not data:
        current.pop(name, None)
        _write(current)
        return True
    try:
        current[name] = base64.b64encode(_protect(data)).decode("ascii")
    except OSError:
        return False
    _write(current)
    return True


def _secret_name(name: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9._-]{1,80}", name or ""))


def _read() -> dict:
    path = secrets_path()
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _write(payload: dict) -> None:
    path = secrets_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload), encoding="utf-8")
    temporary.replace(path)


def _protect(data: bytes) -> bytes:
    if sys.platform != "win32":
        raise OSError("DPAPI is only available on Windows")
    return _crypt(data, protect=True)


def _unprotect(data: bytes) -> bytes:
    if sys.platform != "win32":
        raise OSError("DPAPI is only available on Windows")
    return _crypt(data, protect=False)


def _crypt(data: bytes, *, protect: bool) -> bytes:
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    buffer = ctypes.create_string_buffer(data)
    incoming = _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    outgoing = _Blob()
    function = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    function.argtypes = [
        ctypes.POINTER(_Blob),
        ctypes.c_wchar_p,
        ctypes.POINTER(_Blob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_Blob),
    ]
    function.restype = wintypes.BOOL
    description = "fraghost" if protect else None
    ok = function(ctypes.byref(incoming), description, None, None, None, _UI_FORBIDDEN, ctypes.byref(outgoing))
    if not ok:
        raise OSError(ctypes.get_last_error() or "DPAPI failed")
    try:
        return ctypes.string_at(outgoing.pbData, outgoing.cbData)
    finally:
        kernel32.LocalFree(outgoing.pbData)
