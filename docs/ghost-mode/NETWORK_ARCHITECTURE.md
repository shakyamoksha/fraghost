# Ghost Mode network architecture

## Selected architecture

**Application-level integration with the existing libtorrent session. No system-wide VPN. No custom kernel driver. No automatic public server.**

Ghost Mode, when it is built, should be a state machine and a stricter control surface over the session that already exists in `fraghost/engine.py`. Protected traffic means traffic libtorrent sends after `set_proxy`, with DHT, LSD, µTP, UPnP, and NAT-PMP off, and with the listen address on loopback. Unprotected traffic means the switch is off and the session uses the normal interface, which is how the app works today.

Phase 3 adds a userspace WireGuard handshake in front of that session. It uses the published Noise_IKpsk2 message format with X25519 and ChaCha20-Poly1305 from the `cryptography` library. There is no default route, no kernel driver, and no bundled server. The endpoint and its public key come from the user. A successful handshake is `DEGRADED`, not `PROTECTED`, because BitTorrent is not carried inside the tunnel. While Ghost Mode is on and not protected, the libtorrent session is fail-closed. UDP on the physical interface is still not claimed to be sealed.

## Why this was selected

The traffic that matters is not HTTP. It is a long-lived libtorrent session: TCP peers, UDP trackers, DHT, µTP, LSD, UPnP, and NAT-PMP. An HTTP-client proxy would miss that session. A Windows VPN profile or a default-route tunnel would take every other program with it.

| Option | Result of the inspection |
| --- | --- |
| A. Application-level integration | Fits. libtorrent already has a proxy setting, a listen-address setting, and flags that disable DHT, LSD, µTP, UPnP, and NAT-PMP. The UI already exposes this as the VPN switch. |
| B. Windows per-app VPN platform | Does not fit this app. The per-app VPN API is an enterprise and UWP facility. This is a WinForms `pythonw.exe` process, not a packaged UWP app, and the installer is per-user. |
| C. Windows Filtering Platform and a callout driver | Could force every socket from this process into a tunnel, including the UDP listen socket the proxy misses. It needs a signed kernel driver, an elevated install, and a privileged service. The spec forbids a custom driver as an MVP shortcut. Not selected. |

## Supported traffic types

These can be brought under the current fail-closed proxy, and are the only paths Ghost Mode may call protected after a real check:

- Outgoing TCP peer connections, when libtorrent is actually using the configured proxy.
- Outgoing TCP tracker announces, when `proxy_tracker_connections` is on.
- Tracker hostname resolution, when `proxy_hostnames` is on, because the name is handed to the proxy instead of the system DNS.

Protection is true only when a connection test through that proxy succeeds. A saved host and port are not proof.

## Unsupported traffic types

Ghost Mode must not claim these are inside a tunnel:

- **UDP listen sockets.** Turning on `set_proxy` has still left a UDP socket bound on the listen port. Incoming UDP is not sealed.
- **UDP trackers and DHT.** DHT is switched off rather than tunneled. UDP trackers are not carried by the SOCKS path used here. Disabling them is containment, not encryption.
- **µTP.** It is disabled while the switch is on. It is not carried through the proxy.
- **LSD, UPnP, NAT-PMP.** Disabled while the switch is on. They are local-network protocols.
- **RSS fetches and the GitHub update client.** Both use `urllib` on the normal route and the system DNS.
- **WebView2.** The document is `http://127.0.0.1`. Runtime update checks, if WebView2 makes them, are outside libtorrent.
- **Child processes.** `explorer.exe` and the update `cmd.exe` are intentionally outside the session.
- **Any other Windows program.** Nothing in this design installs a default route, so browsers and Windows Update stay on the physical network. That is required.

## DNS

- Libtorrent tracker names: `proxy_hostnames` while the switch is on. That is the DNS control that exists today.
- RSS and updates: system resolver. They are outside Ghost Mode until a later phase routes those two `urllib` call sites on purpose.
- Do not change the system DNS, the NRPT, or the hosts file. That would affect every application.

## UDP

Do not claim UDP is tunneled. The safe behavior already in the engine is to disable µTP, DHT, LSD, UPnP, and NAT-PMP, and to refuse direct TCP when the proxy is missing. A future userspace WireGuard interface could carry UDP only if libtorrent is bound to that interface and a test shows no socket left on the physical NIC. Until that test exists, UDP is unsupported.

## Subprocesses

Do not include `explorer.exe`, `cmd.exe`, or the installer. They are not the torrent session. A future design that redirects “the whole process tree” would send an update download and a folder window through the tunnel and is rejected.

## Process identity

The only identity that matters is the `pythonw.exe` that is running `-m fraghost` from the fraghost install directory. Firewall rules must name that full path. They must not allow every Python on the machine. There is no service process beside this one.

## Windows permissions

- Listening for peers needs the existing inbound firewall rule for that `pythonw.exe`. Ghost Mode does not need a second rule.
- A tunnel device or a firewall change that is not already covered needs one administrator prompt, at install or at first enable. It must not prompt on every launch.
- No Windows service, no startup entry, and no scheduled task.

## Installation and removal

- Install stays per-user.
- The firewall rule named `fraghost` is added once and removed on uninstall. That rule is for peer connectivity, not for Ghost Mode.
- If a later phase adds a tunnel interface, the uninstaller has to remove that interface and any rule that points at it.
- Private keys, if they are ever created, live in `%APPDATA%\fraghost`, protected with DPAPI. They are not created in this phase.

## Security limitations

This architecture does **not** provide a complete process-wide VPN.

It does provide a fail-closed switch for the libtorrent TCP session when a proxy the user trusts is configured, with the UDP and side-channel features turned off. The operator of that proxy can see the BitTorrent traffic. The app must keep saying that, as the current VPN dialog already does.

If no user-supplied endpoint is configured, Ghost Mode stays `BLOCKED`. It must not search the public internet for an open server and it must not mark itself `PROTECTED`. A saved endpoint is not proof. Only a decrypted handshake response moves the state to `DEGRADED`, and even that leaves `protected` false.
