# Ghost Mode phases 4–7

These phases score and retry only endpoints the user saved. They do not add servers, and they do not mark Ghost Mode protected.

## Phase 4 — Selection

Saved endpoints are tried with the selected one first. A handshake failure is recorded as unreachable with score 0. A handshake that decrypts records its round-trip time. The score is `1000 - latency` and is only a handshake score. Throughput is not measured, because BitTorrent is not inside the tunnel. If the first endpoint fails, the next saved endpoint is tried. Nothing outside the saved list is contacted.

## Phase 5 — Hold

While Ghost Mode is enabled and not protected:

- The kill switch keeps the libtorrent session on the closed proxy path.
- Public IPv4 and IPv6 listen addresses are replaced with loopback.
- Torrent hostname lookup stays on that closed path.
- RSS, update checks, and every other program stay outside.

The WireGuard endpoint address itself is resolved with the system resolver. That name is the path to the endpoint, not torrent DNS.

## Phase 6 — Retry

After every saved endpoint fails, Ghost Mode waits 5, 15, 45, then 120 seconds and tries again. Disabling Ghost Mode cancels the wait. A change of outbound network, detected by a hash of the local address, starts the same selection again. The address is not logged and is not shown in the interface. Diagnostics are the state, the reason, the handshake score, and the next retry time.

## Phase 7 — Release

Automated coverage is `python -m fraghost.test_ghost` and `python -m fraghost.selftest`. The installer removes the saved endpoint list on uninstall. It leaves the torrent library and the DPAPI secret file in place, because that file also holds the proxy password. The local WireGuard private key therefore remains until that file is removed by hand.

## Still true

`protected` stays false. UDP is not tunneled. A saved endpoint is not a VPN for the rest of Windows.
