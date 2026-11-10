# Ghost Mode threat model

This model covers the architecture in `NETWORK_ARCHITECTURE.md`. It does not describe a tunnel that has not been built.

## What is being protected

The BitTorrent session inside the fraghost process: peer addresses, transfer contents, and tracker announces, and the fact that those flows are not supposed to use the physical interface while protection is on.

The local UI password, the proxy password, and any future WireGuard private key are secrets. They are not traffic, but they are in scope for storage and logs.

## What is explicitly not protected

- Other programs on the computer.
- RSS downloads and the GitHub update check, until those call sites are routed.
- UDP sockets that libtorrent still opens outside `set_proxy`.
- The operator of a proxy or a future VPN endpoint, who can see the traffic they carry.
- A person who already controls this Windows account. They can read `%APPDATA%\fraghost\settings.json`. The proxy password is stored with DPAPI, not in that file.

## Assets

| Asset | Where it is now |
| --- | --- |
| Proxy host, port, username, and password | `settings.json` in plaintext |
| Torrent resume data and labels | `%APPDATA%\fraghost` |
| Web UI password | `settings.json`, compared with a constant-time check on the loopback API |
| Future WireGuard private key | Must not be created until a later approved phase. Must not be written into the frontend or into logs. |

## Adversaries

| Adversary | Relevant abuse |
| --- | --- |
| Local unprivileged process | Talks to `127.0.0.1:47371`. If no web password is set, it can change settings, including the proxy. |
| Local administrator | Can read the data directory, change firewall rules, and see every interface. Out of scope to defend against. |
| Malicious or compromised proxy | Sees tracker announces and peer flows that libtorrent sends through it. Can drop or alter them. |
| Malicious “free VPN” endpoint | Same as a malicious proxy, plus a reason to refuse any auto-discovered server. |
| Network observer on the physical LAN | Sees direct peer traffic while Ghost Mode is off. While it is on, should not see libtorrent TCP. May still see a UDP listen socket. |
| Person who tampers with `settings.json` | Can point the proxy at themselves or turn the switch off. |
| Supply chain of a future WireGuard binary | A vendored driver or DLL could become the tunnel. Nothing is vendored in this phase. |

## Abuse cases

**DNS leak.** RSS and updates resolve on the system DNS. Tracker names are proxied only when `proxy_hostnames` is on. A later phase must not call the system resolver for a name that is supposed to be inside the tunnel.

**IP leak.** Fail-closed mode points libtorrent at `127.0.0.1:9` when the switch is on and the proxy is incomplete. That blocks direct TCP. It does not by itself prove that no UDP socket is bound to `0.0.0.0`. Status must stay `blocked` or `ERROR`, never `PROTECTED`, until a socket check says otherwise.

**IPv6 leak.** The listen string includes `[::1]` while tunneling and `[::]` while off. A future test has to show that a public IPv6 listen socket is absent while protection is on.

**Fake protection.** Reporting `PROTECTED` because a setting is true, without a successful request through the endpoint, is a failure. The current UI can say “VPN on” when the proxy fields are saved. Ghost Mode’s `PROTECTED` state has to be stricter than that.

**Configuration injection.** `proxy_type` is limited to `none`, `socks4`, `socks5`, `socks5_pw`, `http`, and `http_pw`. Host and port are coerced. A future endpoint registry must reject a config that is not signed or not on an explicit allow list. An unsigned JSON file from the network is not a trust source.

**Credential exposure.** Proxy passwords are in `settings.json` and must not be copied into the log lines that already record `VPN on via host:port`. Private keys must not be sent to a server. A server may receive a public key only.

**Hardware identity.** Do not read the Machine GUID, disk serial, or MAC address to build an account. This phase creates no identifier.

**Privilege escalation.** Ghost Mode must not add a service. Firewall changes stay on the existing one-time installer prompt. No IPC channel beyond the loopback HTTP API, which already accepts optional password protection.

**Telemetry and history.** The app does not phone home. Ghost Mode must not add analytics, connection histories, or URL logs. Libtorrent’s own session log lines stay the existing error and status alerts.

## Required responses

- No trusted endpoint: do not connect, do not invent a server, leave the session fail-closed if the user asked for protection.
- Endpoint fails after protection was real: return to fail-closed, then retry that endpoint or another endpoint the user already approved. Never fall open.
- Tampered config: reject it and keep the last valid settings or stay off.
- Ghost Mode disabled: stop health checks and leave the session on the normal interface, which is the current `vpn_enabled = false` behavior.

## Phase 1 result

No tunnel was built, no key was generated, and no server list was added. The threats above are the bar for any later phase.
