# Cairn

Cairn is a free, open-source BitTorrent client for people who want the classic transfer tools without ads, tracking, or bundled software.

The interface is licensed under GPL-3.0-or-later. Transfers are handled by [libtorrent](https://libtorrent.org/) (BSD-3-Clause), the same engine family used by other full BitTorrent clients. Cairn does not phone home and does not ship a search catalog.

## Features

- Add `.torrent` files, magnet links, and http(s) links to torrent files. A magnet starts immediately; its file list is fetched in the background
- Pause, stop, start, and force start
- Queue limits, queue order, and file priorities (skip, low, normal, high, max)
- Sequential download and super seeding
- Per-torrent and global rate limits
- Week scheduler with full, limited, and stopped hours
- DHT, peer exchange, local peer discovery, µTP, protocol encryption
- UPnP and NAT-PMP
- SOCKS and HTTP proxy
- Labels, watch folder, and move-on-complete
- RSS feeds with title filters
- IP blocklist import (eMule / PeerGuardian style text, including `.gz`)
- Trackers, peers, piece map, and a session speed graph
- Create v1 or hybrid torrents
- Recheck, reannounce, add a peer, copy the magnet link, export the `.torrent`
- Local web UI on `127.0.0.1` only, with an optional password

## Run

From this folder, with Python 3.12:

```bat
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
cd ui
npm install
npm run build
cd ..
cairn.cmd
```

`cairn.cmd` opens the desktop window. `shakya.cmd` does the same thing. The UI is also served at the printed local address if you want it in a browser.

```bat
.venv\Scripts\python -m shakya --server
```

While changing the interface:

```bat
cd ui
npm run dev
```

```bat
.venv\Scripts\python -m shakya --dev
```

Settings and resume data are stored in `%APPDATA%\Shakya`, so an existing library stays where it is.

## Check

```bat
.venv\Scripts\python -m shakya.selftest
```

The self-test creates a tiny torrent, adds it paused, restores it from resume data, checks that a magnet can start before its metadata arrives, and exercises the local API. It does not download anything from the internet.
