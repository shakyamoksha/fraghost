"""Check GitHub for a newer fraghost installer and launch it."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import __version__
from .build_info import BUILD

_HOSTS = {
    "api.github.com",
    "github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
    "github-releases.githubusercontent.com",
}
_SETUP_NAMES = ("fraghost-setup.exe", "CairnSetup.exe")
_OFFICIAL_REPO = "shakyamoksha/fraghost"
_MAX_BYTES = 500 * 1024 * 1024
_INTERVAL = 6 * 60 * 60


def update_mode(settings: dict | None) -> str:
    mode = str((settings or {}).get("update_mode") or "notify").strip().lower()
    if mode not in ("off", "notify", "auto"):
        return "notify"
    return mode


def repo_name(settings: dict | None) -> str:
    """The published fraghost builds. A saved repository name is ignored."""
    del settings
    return _OFFICIAL_REPO


def _check_url(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in _HOSTS:
        raise ValueError("The update link is not from GitHub")


class _Redirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open(url: str, timeout: int = 20):
    _check_url(url)
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": f"fraghost/{__version__}",
            "Accept": "application/vnd.github+json",
        },
    )
    opener = urllib.request.build_opener(_Redirects)
    return opener.open(request, timeout=timeout)


def _get_json(url: str) -> dict:
    with _open(url) as response:
        data = json.loads(response.read().decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("The update feed was not a JSON object")
    return data


def _blank(settings: dict | None) -> dict:
    mode = update_mode(settings)
    return {
        "current_version": __version__,
        "current_build": int(BUILD),
        "repo": repo_name(settings),
        "mode": mode,
        "automatic": mode == "auto" and int(BUILD) > 0,
        "checked_at": 0,
        "available": False,
        "latest_version": "",
        "latest_build": 0,
        "notes": "",
        "url": "",
        "error": "",
    }


def _setup_url(assets: list) -> tuple[str, str]:
    found: dict[str, str] = {}
    version_url = ""
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name") or "")
        link = str(asset.get("browser_download_url") or "")
        if name == "version.json":
            version_url = link
        elif name in _SETUP_NAMES and link:
            found[name] = link
    for name in _SETUP_NAMES:
        if name in found:
            return found[name], version_url
    return "", version_url


class Updates:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._status: dict | None = None
        self._checked = 0.0
        self._applying = False
        self._gave_up: set[int] = set()

    def check(self, settings: dict | None, force: bool = False) -> dict:
        mode = update_mode(settings)
        repo = repo_name(settings)
        with self._lock:
            now = time.time()
            cached = self._status
            if (
                not force
                and cached is not None
                and cached.get("mode") == mode
                and cached.get("repo") == repo
                and now - self._checked < _INTERVAL
            ):
                return dict(cached)
            if mode == "off" and not force:
                status = _blank(settings)
                status["checked_at"] = int(self._checked or 0)
                return status
            status = self._fetch(settings)
            self._checked = time.time()
            status["checked_at"] = int(self._checked)
            status["mode"] = mode
            status["automatic"] = mode == "auto" and int(BUILD) > 0
            self._status = status
            return dict(status)

    def _fetch(self, settings: dict | None) -> dict:
        status = _blank(settings)
        if not status["repo"]:
            status["error"] = "Could not reach the update feed."
            return status
        try:
            release = _get_json(f"https://api.github.com/repos/{status['repo']}/releases/latest")
            assets = release.get("assets") if isinstance(release.get("assets"), list) else []
            setup_url, version_url = _setup_url(assets)
            info: dict = {}
            if version_url:
                _check_url(version_url)
                info = _get_json(version_url)
            latest_build = int(info.get("build") or 0)
            latest_version = str(info.get("version") or "").strip()
            notes = str(info.get("notes") or release.get("body") or "").strip()
            if setup_url:
                _check_url(setup_url)
            status.update(
                {
                    "latest_version": latest_version,
                    "latest_build": latest_build,
                    "notes": notes,
                    "url": setup_url,
                    "available": bool(setup_url) and latest_build > int(BUILD),
                }
            )
            if not setup_url:
                status["error"] = "The latest release has no installer yet."
            elif not status["available"]:
                status["error"] = ""
        except urllib.error.HTTPError as exc:
            status["error"] = "No published build yet." if exc.code == 404 else "Could not reach the update feed."
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError, OSError):
            status["error"] = "Could not reach the update feed."
        return status

    def maybe_install(self, settings: dict | None, busy: bool) -> None:
        status = self.check(settings)
        if update_mode(settings) != "auto" or int(BUILD) <= 0:
            return
        if not status.get("available") or busy:
            return
        build = int(status.get("latest_build") or 0)
        with self._lock:
            if self._applying or build in self._gave_up:
                return
        try:
            self.apply(settings)
        except Exception:
            with self._lock:
                self._gave_up.add(build)
            raise

    def apply(self, settings: dict | None) -> dict:
        with self._lock:
            if self._applying:
                raise ValueError("An update is already starting")
            self._applying = True
        try:
            status = self.check(settings, force=True)
            if not status["available"]:
                raise ValueError(status["error"] or "fraghost is already up to date")
            destination = Path(tempfile.gettempdir()) / f"fraghost-setup-{status['latest_build']}.exe"
            _download(status["url"], destination)
            _launch_installer(destination)
            return {"ok": True}
        except Exception:
            with self._lock:
                self._applying = False
            raise


def _download(url: str, destination: Path) -> None:
    temporary = destination.with_suffix(".part")
    size = 0
    try:
        with _open(url, timeout=60) as response, temporary.open("wb") as handle:
            while True:
                chunk = response.read(1024 * 256)
                if not chunk:
                    break
                size += len(chunk)
                if size > _MAX_BYTES:
                    raise ValueError("The installer is larger than expected")
                handle.write(chunk)
        if size < 100_000:
            raise ValueError("The downloaded installer looks incomplete")
        temporary.replace(destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _launch_installer(installer: Path) -> None:
    helper = installer.with_suffix(".cmd")
    helper.write_text(
        "\r\n".join(
            [
                "@echo off",
                "timeout /t 2 /nobreak >nul",
                f'start "" "{installer}" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART',
                "del \"%~f0\"",
                "",
            ]
        ),
        encoding="utf-8",
    )
    flags = 0x08000000 | 0x00000200 | 0x01000000
    subprocess.Popen(
        ["cmd", "/c", str(helper)],
        close_fds=True,
        creationflags=flags,
    )

    def _exit() -> None:
        import time

        time.sleep(0.6)
        os._exit(0)

    threading.Thread(target=_exit, daemon=True).start()


updates = Updates()
