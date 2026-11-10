"""Ghost Mode state machine, endpoint registry, and a loopback WireGuard handshake."""

from __future__ import annotations

import json
import os
import socket
import tempfile
import threading
import time
from pathlib import Path

from .ghost import GhostController, route_changed, route_token
from .ghost_identity import _generate, _public_bytes, encode_key, public_key
from .ghost_registry import add, list_public
from .secrets import read_password
from .settings import load_settings, public_settings, save_settings
from .wireguard import HandshakeError, connect, serve_once


def main() -> None:
    home = tempfile.mkdtemp(prefix="fraghost-ghost-")
    os.environ["SHAKYA_HOME"] = home
    controller = GhostController()
    assert controller.status()["state"] == "DISABLED"
    assert controller.status()["protected"] is False
    enabled = controller.apply_enabled(True)
    assert enabled["state"] == "BLOCKED"
    assert enabled["protected"] is False
    assert "unknown server" in enabled["reason"]
    controller.transition("DISABLED", "")
    controller.transition("INITIALIZING", "")
    controller.transition("DISCOVERING", "")
    controller.transition("CONNECTING", "")
    controller.transition("VERIFYING", "")
    refused = controller.transition("PROTECTED", "should not stick")
    assert refused["state"] == "ERROR"
    assert refused["protected"] is False
    assert controller.apply_enabled(False)["state"] == "DISABLED"

    path = Path(home) / "settings.json"
    path.write_text(json.dumps({"proxy_password": "kept-secret", "proxy_host": "10.0.0.8"}), encoding="utf-8")
    loaded = load_settings()
    assert loaded["proxy_password"] == "kept-secret"
    assert loaded["proxy_host"] == "10.0.0.8"
    disk = path.read_text(encoding="utf-8")
    assert "kept-secret" not in disk
    assert read_password() == "kept-secret"
    again = load_settings()
    assert again["proxy_password"] == "kept-secret"
    view = public_settings(again)
    assert view["proxy_password"] == ""
    assert view["proxy_has_password"] is True
    assert "kept-secret" not in json.dumps(view)
    save_settings(again)
    assert read_password() == "kept-secret"
    again["proxy_password"] = ""
    save_settings(again)
    assert read_password() == ""

    try:
        add({"host": "https://relay.example", "port": 51820, "public_key": encode_key(b"\x01" * 32)})
    except ValueError:
        pass
    else:
        raise AssertionError("a URL endpoint was accepted")
    try:
        add({"host": "vpn.example", "port": 51820, "public_key": "not-a-key"})
    except ValueError:
        pass
    else:
        raise AssertionError("a bad public key was accepted")
    preshared = _generate()
    assert preshared is not None
    created = add(
        {
            "name": "Lab",
            "host": "vpn.example",
            "port": 51820,
            "public_key": encode_key(b"\x02" * 32),
            "country": "Lab",
            "preshared_key": encode_key(preshared),
        }
    )
    stored = (Path(home) / "ghost-endpoints.json").read_text(encoding="utf-8")
    assert encode_key(preshared) not in stored
    assert created["has_preshared"] is True
    assert list_public()[0]["host"] == "vpn.example"
    assert "preshared_key" not in stored

    class _Quiet:
        def close(self) -> None:
            return None

    ready = GhostController(establish=lambda _endpoint: _Quiet())
    ready.apply_enabled(True, {"id": created["id"], "host": "vpn.example", "port": 51820, "country": "Lab", "public_key": created["public_key"]})
    deadline = time.time() + 2
    status = ready.status()
    while time.time() < deadline and status["state"] not in ("DEGRADED", "ERROR"):
        time.sleep(0.02)
        status = ready.status()
    assert status["state"] == "DEGRADED", status
    assert status["protected"] is False
    assert "not protected" in status["reason"]
    refused_again = ready.transition("PROTECTED", "should not stick")
    assert refused_again["state"] == "ERROR"
    assert refused_again["protected"] is False
    ready.stop()

    failed = GhostController(establish=lambda _endpoint: (_ for _ in ()).throw(HandshakeError("The pinned endpoint did not answer a WireGuard handshake. Transfers stay paused.")))
    failed.apply_enabled(True, {"host": "vpn.example", "port": 51820, "public_key": created["public_key"], "country": ""})
    deadline = time.time() + 2
    status = failed.status()
    while time.time() < deadline and status["state"] != "ERROR":
        time.sleep(0.02)
        status = failed.status()
    assert status["state"] == "ERROR"
    assert status["protected"] is False
    assert "did not answer" in status["reason"]
    failed.stop()

    left = _generate()
    right = _generate()
    assert left is not None and right is not None
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    holder: dict = {}

    def _serve() -> None:
        try:
            holder["payload"] = serve_once(sock, right, _public_bytes(left))
        except Exception as exc:
            holder["error"] = exc
        finally:
            sock.close()

    worker = threading.Thread(target=_serve, daemon=True)
    worker.start()
    tunnel = connect("127.0.0.1", port, left, _public_bytes(right))
    tunnel.close()
    worker.join(timeout=5)
    assert "error" not in holder, holder.get("error")
    assert holder.get("payload") == b""
    secret_text = (Path(home) / "secrets.json").read_text(encoding="utf-8")
    assert encode_key(left) not in secret_text
    assert encode_key(right) not in secret_text
    local = public_key(create=True)
    assert local
    assert local not in secret_text

    calls = {"n": 0}

    def _failover(endpoint):
        calls["n"] += 1
        if endpoint["host"] == "down.example":
            raise HandshakeError("The pinned endpoint did not answer a WireGuard handshake. Transfers stay paused.")
        return _Quiet()

    failover = GhostController(establish=_failover)
    failover.apply_enabled(
        True,
        [
            {"id": "a" * 32, "host": "down.example", "port": 51820, "country": ""},
            {"id": "b" * 32, "host": "up.example", "port": 51820, "country": "Lab"},
        ],
    )
    deadline = time.time() + 2
    status = failover.status()
    while time.time() < deadline and status["state"] not in ("DEGRADED", "ERROR"):
        time.sleep(0.02)
        status = failover.status()
    assert status["state"] == "DEGRADED", status
    assert status["endpoint"] == "up.example:51820"
    assert status["protected"] is False
    assert "Switched to another saved endpoint" in status["reason"]
    health = {item["id"]: item for item in failover.health()}
    assert health["a" * 32]["ok"] is False
    assert health["a" * 32]["score"] == 0
    assert health["b" * 32]["ok"] is True
    assert health["b" * 32]["score"] >= 1
    failover.stop()

    attempts = {"n": 0}

    def _retry(endpoint):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise HandshakeError("The pinned endpoint did not answer a WireGuard handshake. Transfers stay paused.")
        return _Quiet()

    retry = GhostController(establish=_retry, retry_delays=(0.05,))
    retry.apply_enabled(True, {"id": "c" * 32, "host": "vpn.example", "port": 51820, "public_key": "x"})
    deadline = time.time() + 2
    status = retry.status()
    while time.time() < deadline and not (status["state"] == "DEGRADED" and attempts["n"] >= 2):
        time.sleep(0.02)
        status = retry.status()
    assert attempts["n"] >= 2
    assert status["state"] == "DEGRADED", status
    assert status["protected"] is False
    retry.stop()

    assert route_changed("a", "b") is True
    assert route_changed("", "b") is False
    assert route_changed("a", "a") is False
    token = route_token()
    assert token == "" or (len(token) == 64 and "." not in token)
    print("ghost tests ok")


if __name__ == "__main__":
    main()
