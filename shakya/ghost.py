"""Ghost Mode state machine. A handshake is not protection, and an unknown server is never used."""

from __future__ import annotations

import hashlib
import socket
import threading
import time

from .ghost_identity import IdentityError
from .wireguard import HandshakeError, connect_endpoint

STATES = (
    "DISABLED",
    "INITIALIZING",
    "DISCOVERING",
    "CONNECTING",
    "VERIFYING",
    "PROTECTED",
    "DEGRADED",
    "BLOCKED",
    "ERROR",
)

_EDGES = {
    "DISABLED": {"INITIALIZING"},
    "INITIALIZING": {"DISCOVERING", "ERROR", "DISABLED"},
    "DISCOVERING": {"CONNECTING", "BLOCKED", "ERROR", "DISABLED"},
    "CONNECTING": {"VERIFYING", "BLOCKED", "ERROR", "DISABLED"},
    "VERIFYING": {"PROTECTED", "DEGRADED", "BLOCKED", "ERROR", "DISABLED"},
    "PROTECTED": {"DEGRADED", "BLOCKED", "ERROR", "DISABLED"},
    "DEGRADED": {"CONNECTING", "BLOCKED", "ERROR", "DISABLED"},
    "BLOCKED": {"INITIALIZING", "ERROR", "DISABLED"},
    "ERROR": {"INITIALIZING", "DISABLED"},
}

_NO_ENDPOINT = "No trusted endpoint is configured. Ghost Mode will not connect to an unknown server."
_NO_TUNNEL = "Tunnel verification is not available."
_NO_ANSWER = "The pinned endpoint did not answer a WireGuard handshake. Transfers stay paused."
_HANDSHAKE_ONLY = (
    "WireGuard handshake verified with the pinned endpoint. "
    "BitTorrent is not inside that tunnel, so transfers stay paused and this is not protected."
)
_RETRY_DELAYS = (5, 15, 45, 120)


def route_token() -> str:
    """Hash of the current outbound address. The address itself is not returned."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("192.0.2.1", 9))
            address = sock.getsockname()[0]
        finally:
            sock.close()
    except OSError:
        return ""
    if not address:
        return ""
    return hashlib.sha256(address.encode("utf-8")).hexdigest()


def route_changed(previous: str, current: str) -> bool:
    return bool(previous and current and previous != current)


class GhostController:
    def __init__(self, on_change=None, establish=None, retry_delays: tuple[int, ...] | tuple[float, ...] = _RETRY_DELAYS) -> None:
        self.state = "DISABLED"
        self.reason = ""
        self.since = time.time()
        self.endpoint = ""
        self.country = ""
        self._traffic_verified = False
        self._on_change = on_change
        self._establish = establish or connect_endpoint
        self._retry_delays = retry_delays or _RETRY_DELAYS
        self._lock = threading.RLock()
        self._generation = 0
        self._tunnel = None
        self._timer: threading.Timer | None = None
        self._health: dict[str, dict] = {}
        self._attempts = 0
        self._next_retry = 0
        self._items: list[dict] = []

    def apply_enabled(self, enabled: bool, endpoints=None) -> dict:
        items = _endpoints(endpoints)
        self._cancel_timer()
        with self._lock:
            self._generation += 1
            generation = self._generation
            tunnel = self._tunnel
            self._tunnel = None
            self._traffic_verified = False
            self._next_retry = 0
            self.endpoint = ""
            self.country = ""
            self._items = items
            if enabled:
                self._attempts = 0
            if enabled and self.state not in ("DISABLED", "BLOCKED", "ERROR"):
                self.transition("DISABLED", "")
        if tunnel is not None:
            tunnel.close()
        if not enabled:
            self._attempts = 0
            return self.transition("DISABLED", "")
        self.transition("INITIALIZING", "")
        self.transition("DISCOVERING", "")
        if not items:
            return self.transition("BLOCKED", _NO_ENDPOINT)
        self.transition("CONNECTING", "")
        threading.Thread(
            target=self._choose,
            args=(generation, items),
            name="fraghost-ghost",
            daemon=True,
        ).start()
        return self.status()

    def stop(self) -> None:
        self._cancel_timer()
        with self._lock:
            self._generation += 1
            tunnel = self._tunnel
            self._tunnel = None
        if tunnel is not None:
            tunnel.close()

    def transition(self, state: str, reason: str = "") -> dict:
        with self._lock:
            if state not in STATES:
                raise ValueError("Unknown Ghost Mode state")
            if state != "DISABLED" and state not in _EDGES[self.state] and state != "ERROR":
                state = "ERROR"
                reason = reason or "That Ghost Mode transition is not allowed."
            if state == "PROTECTED" and not self._traffic_verified:
                state = "ERROR"
                reason = _NO_TUNNEL
            self.state = state
            self.reason = reason
            self.since = time.time()
            return self.status()

    def health(self) -> list[dict]:
        with self._lock:
            rows = []
            for endpoint_id, item in self._health.items():
                latency = item.get("latency_ms")
                score = 0 if not item.get("ok") else max(1, 1000 - int(latency or 1000))
                rows.append(
                    {
                        "id": endpoint_id,
                        "ok": bool(item.get("ok")),
                        "latency_ms": latency,
                        "score": score,
                    }
                )
            return rows

    def status(self) -> dict:
        with self._lock:
            return {
                "enabled": self.state != "DISABLED",
                "state": self.state,
                "reason": self.reason,
                "since": int(self.since),
                "protected": self._traffic_verified,
                "endpoint": self.endpoint,
                "country": self.country,
                "next_retry": int(self._next_retry) if self._next_retry else 0,
            }

    def _choose(self, generation: int, items: list[dict]) -> None:
        ordered = self._order(items)
        last = _NO_ANSWER
        switched = False
        for index, endpoint in enumerate(ordered):
            with self._lock:
                if generation != self._generation:
                    return
                self.endpoint = f"{endpoint.get('host')}:{endpoint.get('port')}"
                self.country = str(endpoint.get("country") or "")
            started = time.perf_counter()
            try:
                tunnel = self._establish(endpoint)
            except IdentityError as exc:
                self._remember(endpoint, ok=False, latency_ms=None)
                self._commit(generation, "ERROR", str(exc) or _NO_ANSWER)
                return
            except HandshakeError as exc:
                self._remember(endpoint, ok=False, latency_ms=None)
                last = str(exc) or _NO_ANSWER
                switched = index > 0 or switched
                continue
            except Exception:
                self._remember(endpoint, ok=False, latency_ms=None)
                last = _NO_ANSWER
                continue
            with self._lock:
                if generation != self._generation:
                    tunnel.close()
                    return
                self._tunnel = tunnel
                self._attempts = 0
                self._next_retry = 0
            latency = max(0, int((time.perf_counter() - started) * 1000))
            self._remember(endpoint, ok=True, latency_ms=latency)
            if not self._commit(generation, "VERIFYING", ""):
                return
            reason = _HANDSHAKE_ONLY
            if switched or index > 0:
                reason = "Switched to another saved endpoint. " + reason
            self._commit(generation, "DEGRADED", reason)
            return
        if self._commit(generation, "ERROR", last):
            self._schedule(generation)

    def _order(self, items: list[dict]) -> list[dict]:
        with self._lock:
            health = dict(self._health)

        def sort_key(pair: tuple[int, dict]) -> tuple:
            index, item = pair
            info = health.get(str(item.get("id") or ""), {})
            latency = info.get("latency_ms")
            return (0 if info.get("ok") else 1, latency if isinstance(latency, int) else 10**9, index)

        return [item for _, item in sorted(enumerate(items), key=sort_key)]

    def _remember(self, endpoint: dict, *, ok: bool, latency_ms: int | None) -> None:
        endpoint_id = str(endpoint.get("id") or "")
        if not endpoint_id:
            return
        with self._lock:
            self._health[endpoint_id] = {"ok": ok, "latency_ms": latency_ms, "checked_at": int(time.time())}

    def _schedule(self, generation: int) -> None:
        with self._lock:
            if generation != self._generation:
                return
            delay = self._retry_delays[min(self._attempts, len(self._retry_delays) - 1)]
            self._attempts += 1
            self._next_retry = time.time() + delay
            timer = threading.Timer(delay, self._retry, args=(generation,))
            timer.daemon = True
            self._timer = timer
        timer.start()

    def _retry(self, generation: int) -> None:
        with self._lock:
            if generation != self._generation or self.state != "ERROR":
                return
            items = list(self._items)
            self._next_retry = 0
        if not items:
            return
        self.transition("INITIALIZING", "")
        self.transition("DISCOVERING", "")
        self.transition("CONNECTING", "")
        self._choose(generation, items)

    def _cancel_timer(self) -> None:
        with self._lock:
            timer = self._timer
            self._timer = None
            self._next_retry = 0
        if timer is not None:
            timer.cancel()

    def _commit(self, generation: int, state: str, reason: str) -> bool:
        with self._lock:
            if generation != self._generation:
                return False
            status = self.transition(state, reason)
        if self._on_change is not None:
            self._on_change(status)
        return True


def _endpoints(value) -> list[dict]:
    if not value:
        return []
    rows = [value] if isinstance(value, dict) else list(value)
    clean = []
    for item in rows:
        if isinstance(item, dict) and str(item.get("host") or "").strip() and int(item.get("port") or 0) > 0:
            clean.append(item)
    return clean
