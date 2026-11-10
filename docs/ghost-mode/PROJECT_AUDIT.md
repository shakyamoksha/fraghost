# Ghost Mode project audit

Phase 1 inspection of the repository as it exists. No Ghost Mode engine was added.

## Discovered technology stack

| Layer | What is in the tree |
| --- | --- |
| Product | fraghost `0.1.0`, Python package `shakya` |
| Language | Python 3.12 (installer embeds CPython 3.12 amd64) and TypeScript |
| Transfer engine | `libtorrent==2.1.1` (BSD-3-Clause), imported as `libtorrent` |
| Desktop shell | `pywebview==6.2.1` on Windows, WinForms plus WebView2 |
| Interface | React 19 and Vite 6. The built files in `ui/dist` are served by the app. There is no separate frontend server in the installed app. |
| Local API | `ThreadingHTTPServer` bound to `127.0.0.1` in `shakya/server.py` |
| Settings | JSON at `%APPDATA%\Shakya\settings.json` via `shakya/paths.py` |
| Packaging | Inno Setup script `packaging/cairn.iss`, per-user, `PrivilegesRequired=lowest`. Output name `fraghost-setup.exe`. |
| License | Application text is GPL-3.0-or-later (`LICENSE`, `README.md`). libtorrent is BSD-3-Clause. The UI npm packages are MIT, ISC, Apache-2.0, or CC-BY-4.0. |
| Windows target | 64-bit Windows 10/11. The installer allows `x64compatible`. |

There is no Rust, Go, or C toolchain in this repository. There is no existing WireGuard, Wintun, or Windows Filtering Platform code.

## Current networking architecture

One process, started as `pythonw.exe -m shakya`, owns every socket that belongs to the app.

1. **BitTorrent session.** `shakya/engine.py` creates one libtorrent session. With the VPN switch off, it listens on `0.0.0.0:<port>` and `[::]:<port>`. The default port is `6881` unless `random_port` is set. Outgoing traffic uses TCP and, when enabled, µTP (UDP). Peer sources are trackers, DHT, PEX, and LSD.
2. **Existing “VPN” switch.** This is not a tunnel. `vpn_enabled` calls libtorrent `set_proxy` with a user-supplied SOCKS4, SOCKS5, or HTTP proxy. Tracker hostnames can be sent to that proxy (`proxy_hostnames`). DHT, LSD, µTP, UPnP, and NAT-PMP are turned off while the switch is on. The listen address is moved to `127.0.0.1` and `[::1]`. If the switch is on and no proxy is configured, the session is pointed at `127.0.0.1:9` so transfers fail instead of going direct.
3. **Local UI.** `shakya/server.py` listens only on `127.0.0.1`, starting at port `47371`. The React UI talks to that API. WebView2 loads that local origin.
4. **Other HTTP.** RSS feeds and the GitHub update check use `urllib.request` with the process default route. They do not use the libtorrent proxy.
5. **Subprocesses.** Opening a download uses `explorer`. Applying an update starts `cmd.exe` to run the installer. Neither is a torrent transfer.

The UI copy in `ui/src/App.tsx` already states the boundary: only BitTorrent traffic uses the configured proxy, and other programs stay on the normal connection.

## Network traffic inventory

| Traffic | Protocol | Owner | Uses the current proxy when VPN is on? |
| --- | --- | --- | --- |
| Peer upload and download | TCP, and UDP when µTP is on | libtorrent in this process | Intended yes. µTP is disabled while the switch is on. |
| Tracker announce | HTTP(S) or UDP | libtorrent | HTTP(S) yes when `proxy_tracker_connections` is set. UDP trackers are not a proxied TCP path. |
| DHT | UDP | libtorrent | Disabled while the switch is on. |
| PEX | Inside peer connections | libtorrent | Follows peer connections. |
| LSD | Local multicast | libtorrent | Disabled while the switch is on. |
| UPnP and NAT-PMP | Local UDP | libtorrent | Disabled while the switch is on. |
| Incoming peer port | TCP and UDP on the listen port | libtorrent | Incoming TCP and µTP are disabled and the listen address is loopback. A previous measurement of this same `set_proxy` path still showed a UDP socket on the listen port. That is an existing leak, not a sealed tunnel. |
| RSS fetch | HTTP(S) via `urllib` | this process | No. Uses the normal route and the system resolver. |
| Update check and installer download | HTTPS to GitHub via `urllib` | this process | No. |
| Local API and WebView2 document | HTTP to `127.0.0.1` | this process | No, and it must stay local. |
| WebView2 runtime update | Microsoft component, outside this source | WebView2 | Not controlled by libtorrent. |
| “Open folder” and the update installer | `explorer.exe`, `cmd.exe` | child processes | No. They are not torrent traffic. |
| Windows Update, browsers, and other apps | anything | other processes | No. Nothing in this app installs a system route. |

There is no WebSocket client or server in the application source. DNS for BitTorrent hostnames is either the system resolver or, when the proxy is on, libtorrent’s `proxy_hostnames` flag. RSS and updates always use the system resolver.

## Application lifecycle

`shakya/app.py` `main()` loads settings, starts the engine, starts the loopback HTTP server, then opens the pywebview window. Closing the window hides it when the tray icon is up. Quit from the tray shuts the engine down and stops the HTTP server. There is no Windows service and no startup registration in the installer.

Settings changes call `Engine.update_settings`, which reapplies the proxy, the listen pack, and the swarm flags under one lock.

The per-user installer writes a Windows Firewall allow rule named `fraghost` for `{app}\pythonw.exe` on private, public, and domain profiles. That rule is the normal BitTorrent listen permission. It is not a VPN.

## Integration points

- Settings keys already exist: `vpn_enabled`, `proxy_type`, `proxy_host`, `proxy_port`, `proxy_username`, `proxy_password`, `proxy_peers`, `proxy_hostnames`.
- Status is already reported as `off`, `on`, or `blocked` on the session snapshot.
- The toolbar switch and the VPN dialog in `ui/src/App.tsx` are the current controls.
- Fail-closed behavior already exists for “switch on, no proxy”.
- Any Ghost Mode state must go through `Engine.update_settings` so the libtorrent session, not a second process, owns the sockets.

## Windows-specific constraints

- The installed process image is `pythonw.exe`, so Windows Firewall dialogs say Python. A rule now covers that installed path. A new path needs a new rule.
- A per-user install cannot silently add or change firewall rules. Adding one takes an administrator prompt.
- WebView2 is required for the window. Its network activity is not the libtorrent session.
- There is no code-signing certificate in this repository.
- Embedding a kernel driver would change the install from per-user to a privileged install and would need a signed driver.

## Compatibility problems

- libtorrent’s proxy mode is not a packet capture of the process. UDP listen sockets have already been observed outside that proxy.
- `urllib` traffic ignores the VPN switch.
- GPL-3.0-or-later cannot casually absorb a GPL-2.0-only library. That has to be checked before any WireGuard binary is vendored.
- A system-wide WireGuard default route would send Chrome, Windows Update, and every other program through the tunnel. That violates the product requirement.
- No trusted WireGuard endpoint is defined anywhere in this repository. Inventing one, or auto-trusting a public server, is not allowed by the spec or by the current product rule against a bundled public relay.

## Recommended architecture

Use the existing libtorrent session as the only data path for BitTorrent, and keep the tunnel inside this process.

Do not install a default route. Do not add a custom kernel driver. Do not ship a public VPN fleet.

The smallest safe design is an optional, user-supplied endpoint that the current fail-closed proxy already understands, with Ghost Mode as a stricter name and state machine around that switch. A WireGuard tunnel is only justified later, and only if three conditions are true at once: a license-compatible userspace implementation, a local address or local proxy this process can bind to, and an endpoint the user chose whose public key is pinned. Until those exist, Ghost Mode must stay off rather than pretend it is protected.

Details, rejected options, and the traffic that still cannot be claimed as sealed are in `NETWORK_ARCHITECTURE.md`.

## Dependencies and licensing

| Component | Role today | License note |
| --- | --- | --- |
| This application | UI and shell | GPL-3.0-or-later |
| libtorrent 2.1.1 | All peer, tracker, DHT, and µTP I/O | BSD-3-Clause. Compatible with GPL-3. |
| pywebview 6.2.1 | Window | BSD-3-Clause. |
| React, Vite, TypeScript | UI build | Permissive. Dev-only except the built JS shipped in the installer. |
| WireGuard, Wintun, WireGuardNT | Not present | Do not vendor in this phase. Confirm license and Windows signing before any later phase. `wireguard-go` is a known MIT userspace implementation. Official Wintun is a kernel driver with its own redistribution terms. GPL-2.0-only code is a problem next to GPL-3.0-or-later. |

No new dependency is required for Phase 1, and none was added.
