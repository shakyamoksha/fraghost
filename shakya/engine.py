from __future__ import annotations

import base64
import gzip
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from collections import deque
from pathlib import Path

import libtorrent as lt

from . import __version__
from .build_info import BUILD
from .paths import data_dir
from .settings import load_settings, merge_settings, save_settings, schedule_mode

DHT_ROUTERS = (
    ("dht.libtorrent.org", 25401),
    ("router.bittorrent.com", 6881),
    ("router.utorrent.com", 6881),
    ("dht.transmissionbt.com", 6881),
    ("dht.waho.moe", 6881),
)
RANGE_RE = re.compile(r"(\d{1,3}(?:\.\d{1,3}){3})\s*[-–]\s*(\d{1,3}(?:\.\d{1,3}){3})")
MAGNET_RE = re.compile(r"magnet:\?[^\s<\"]+")
TORRENT_URL_RE = re.compile(r"https?://[^\s<\"]+\.torrent", re.I)
INTERESTING_ALERTS = {
    "listen_succeeded_alert",
    "listen_failed_alert",
    "portmap_alert",
    "portmap_error_alert",
    "torrent_finished_alert",
    "torrent_error_alert",
    "file_error_alert",
    "metadata_failed_alert",
    "metadata_received_alert",
    "fastresume_rejected_alert",
    "storage_moved_alert",
    "storage_moved_failed_alert",
    "tracker_error_alert",
}


def info_id(hashes) -> str:
    for part in (getattr(hashes, "v1", None), getattr(hashes, "v2", None)):
        text = _hexish(part)
        if text:
            return text
    return _hexish(hashes)


def _hexish(value) -> str:
    if value is None:
        return ""
    text = str(value).strip().lower()
    if not text or any(ch not in "0123456789abcdef" for ch in text):
        return ""
    if set(text) == {"0"}:
        return ""
    return text


def _hashes_of(obj):
    attr = getattr(obj, "info_hashes")
    return attr() if callable(attr) else attr


def _hashish(name: str) -> bool:
    text = name.strip().lower()
    return len(text) in (40, 64) and all(ch in "0123456789abcdef" for ch in text)


def _magnet_name(magnet: str) -> str:
    try:
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(magnet).query)
    except Exception:
        return ""
    raw = (query.get("dn") or [""])[0]
    return raw.replace("\x00", "").strip()


def _atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(data)
    temporary.replace(path)


def _blank_record(tid: str, name: str, save_path: str, manual: str, magnet: str = "") -> dict:
    return {
        "id": tid,
        "name": name,
        "label": "",
        "added_at": int(time.time()),
        "manual": manual,
        "sequential": False,
        "super_seeding": False,
        "ratio_limit": None,
        "seed_minutes": None,
        "moved": False,
        "move_pending": False,
        "save_path": save_path,
        "magnet": magnet,
        "base_download": 0,
        "pending_priorities": None,
    }


class Engine:
    def __init__(self) -> None:
        self.root = data_dir()
        self.resume_dir = self.root / "resume"
        self.torrent_dir = self.root / "torrents"
        self.resume_dir.mkdir(parents=True, exist_ok=True)
        self.torrent_dir.mkdir(parents=True, exist_ok=True)
        self.settings = load_settings()
        self._lock = threading.RLock()
        self._logs: deque[str] = deque(maxlen=400)
        self._history: deque[dict] = deque(maxlen=120)
        self._handles: dict[str, object] = {}
        self._records: dict[str, dict] = {}
        self._blobs: dict[str, bytes] = {}
        self._ses = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._started = False
        self._external_ip = ""
        self._vpn_note: str | None = None
        self._sched_mode = "F"
        self._sched_holding = False
        self._next_watch = time.time() + 2
        self._next_rss = time.time() + 20
        self._next_resume = time.time() + 15
        self._next_sample = time.time() + 1
        self._load_library()

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        pack = self._pack()
        try:
            self._ses = lt.session(pack)
        except Exception as exc:
            self._log(f"Starting with default session settings ({exc})")
            self._ses = lt.session({"alert_mask": pack["alert_mask"]})
            self._apply_pack(pack)
        self._apply_proxy()
        self._apply_network_features()
        self._sched_mode = schedule_mode(self.settings)
        if self._sched_mode == "S":
            self._ses.pause()
            self._sched_holding = True
            self._log("Scheduler stopped transfers for this hour")
        if self.settings.get("blocklist_path"):
            try:
                count = self._load_blocklist(self.settings["blocklist_path"])
                self._log(f"Loaded {count} blocklist ranges")
            except Exception as exc:
                self._log(f"Blocklist was not loaded: {exc}")
        self._restore()
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="shakya-session", daemon=True)
        self._thread.start()
        self._log(f"Cairn {__version__} is listening")

    def shutdown(self) -> None:
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        if not self._ses:
            return
        with self._lock:
            self._request_resumes()
            pending = set(self._handles)
            deadline = time.time() + 3
            while pending and time.time() < deadline:
                alerts = self._ses.pop_alerts()
                if not alerts:
                    time.sleep(0.05)
                    continue
                for alert in alerts:
                    if type(alert).__name__ == "save_resume_data_alert":
                        self._write_resume(alert)
                        try:
                            pending.discard(info_id(_hashes_of(alert.handle)))
                        except Exception:
                            pass
                    else:
                        self._handle_alert(alert)
            self._save_library()

    def snapshot(self, detail_id: str | None = None) -> dict:
        with self._lock:
            return self._snapshot(detail_id)

    def logs(self) -> list[str]:
        return list(self._logs)

    def preview(self, source: str, value: str) -> dict:
        data, _magnet = self._resolve_source(source, value)
        if data is None:
            raise ValueError("Open a magnet from Add, then choose files once metadata arrives")
        ti = self._ti_from_bytes(data)
        tid = info_id(_hashes_of(ti))
        if not tid:
            raise ValueError("This torrent has no info hash")
        with self._lock:
            if tid in self._handles:
                raise ValueError("This torrent is already in the list")
            self._blobs[tid] = data
        return self._describe(ti, tid)

    def add(
        self,
        source: str,
        value: str,
        *,
        save_path: str = "",
        label: str = "",
        paused: bool = False,
        sequential: bool = False,
        super_seeding: bool = False,
        priorities: list | None = None,
        stop_when_ready: bool = False,
        manual: str | None = None,
    ) -> dict:
        data = None
        magnet = None
        if source == "preview":
            with self._lock:
                data = self._blobs.get(value)
            if data is None:
                raise ValueError("That preview expired. Choose the torrent again")
        else:
            data, magnet = self._resolve_source(source, value)
        if manual is None:
            manual = "paused" if paused or stop_when_ready else "started"
        if manual not in ("started", "paused", "stopped", "force"):
            raise ValueError("Unknown start mode")
        destination = save_path or self.settings["save_path"]
        os.makedirs(destination, exist_ok=True)
        with self._lock:
            ti = None
            if magnet:
                try:
                    atp = lt.parse_magnet_uri(magnet)
                except Exception as exc:
                    raise ValueError(f"Could not read that magnet link: {exc}") from exc
            else:
                ti = self._ti_from_bytes(data or b"")
                atp = lt.add_torrent_params()
                atp.ti = ti
            expected = info_id(_hashes_of(atp if magnet else ti))
            if not expected:
                raise ValueError("This torrent has no info hash")
            if expected in self._handles:
                raise ValueError("This torrent is already in the list")
            atp.save_path = destination
            atp.flags = self._base_flags(paused=manual in ("paused", "stopped") and not stop_when_ready, stop_when_ready=stop_when_ready)
            if self.settings.get("preallocate"):
                try:
                    atp.storage_mode = lt.storage_mode_t.storage_mode_allocate
                except Exception:
                    pass
            cleaned = None
            if priorities:
                cleaned = [max(0, min(7, int(item))) for item in priorities]
                if ti is not None and len(cleaned) == ti.files().num_files():
                    atp.file_priorities = cleaned
            try:
                handle = self._ses.add_torrent(atp)
            except Exception as exc:
                if "duplicate" in str(exc).lower():
                    raise ValueError("This torrent is already in the list") from exc
                raise ValueError(str(exc)) from exc
            tid = info_id(_hashes_of(handle)) or expected
            hint = _magnet_name(magnet) if magnet else (ti.name() if ti is not None else "")
            name = hint or self._torrent_name(handle, {"name": ""})
            if _hashish(name):
                name = hint or "Magnet link"
            meta = _blank_record(tid, name, destination, manual, magnet or "")
            meta["label"] = str(label or "").strip()[:80]
            meta["sequential"] = bool(sequential)
            meta["super_seeding"] = bool(super_seeding)
            try:
                meta["base_download"] = int(handle.status().all_time_download)
            except Exception:
                meta["base_download"] = 0
            if cleaned and ti is None:
                meta["pending_priorities"] = cleaned
            if sequential:
                handle.set_flags(lt.torrent_flags.sequential_download)
            if super_seeding:
                handle.set_flags(lt.torrent_flags.super_seeding)
            if manual in ("started", "force"):
                try:
                    handle.unset_flags(lt.torrent_flags.stop_when_ready)
                except Exception:
                    pass
                handle.unset_flags(lt.torrent_flags.paused)
                if manual == "force":
                    handle.unset_flags(lt.torrent_flags.auto_managed)
                else:
                    handle.set_flags(lt.torrent_flags.auto_managed)
                handle.resume()
                if magnet:
                    try:
                        handle.force_dht_announce()
                    except Exception:
                        pass
            self._handles[tid] = handle
            self._records[tid] = meta
            if data:
                self._store_torrent_bytes(tid, data)
            self._save_library()
            self._log(f'Added "{name}"')
            return self._summary(handle, meta, handle.status())

    def action(self, tid: str, name: str) -> dict:
        with self._lock:
            handle, meta = self._require(tid)
            if name == "pause":
                self._set_manual(handle, meta, "paused")
            elif name == "stop":
                self._set_manual(handle, meta, "stopped")
            elif name == "resume":
                self._set_manual(handle, meta, "started")
            elif name == "force_start":
                self._set_manual(handle, meta, "force")
            elif name == "recheck":
                handle.force_recheck()
                self._log(f'Rechecking "{meta["name"]}"')
            elif name == "reannounce":
                handle.force_reannounce()
                try:
                    handle.force_dht_announce()
                except Exception:
                    pass
            elif name == "queue_up":
                handle.queue_position_up()
            elif name == "queue_down":
                handle.queue_position_down()
            elif name == "queue_top":
                handle.queue_position_top()
            elif name == "queue_bottom":
                handle.queue_position_bottom()
            else:
                raise ValueError("Unknown action")
            return self._summary(handle, meta, handle.status())

    def update(self, tid: str, patch: dict) -> dict:
        if not isinstance(patch, dict):
            raise ValueError("Expected an object")
        with self._lock:
            handle, meta = self._require(tid)
            if "label" in patch:
                meta["label"] = str(patch.get("label") or "").strip()[:80]
            if "sequential" in patch:
                enabled = bool(patch["sequential"])
                meta["sequential"] = enabled
                if enabled:
                    handle.set_flags(lt.torrent_flags.sequential_download)
                else:
                    handle.unset_flags(lt.torrent_flags.sequential_download)
            if "super_seeding" in patch:
                enabled = bool(patch["super_seeding"])
                meta["super_seeding"] = enabled
                if enabled:
                    handle.set_flags(lt.torrent_flags.super_seeding)
                else:
                    handle.unset_flags(lt.torrent_flags.super_seeding)
            if "download_limit" in patch:
                handle.set_download_limit(self._kb_to_limit(patch["download_limit"]))
            if "upload_limit" in patch:
                handle.set_upload_limit(self._kb_to_limit(patch["upload_limit"]))
            if "ratio_limit" in patch:
                meta["ratio_limit"] = self._optional_number(patch["ratio_limit"], float)
            if "seed_minutes" in patch:
                meta["seed_minutes"] = self._optional_number(patch["seed_minutes"], int)
            if patch.get("save_path"):
                dest = os.path.abspath(str(patch["save_path"]))
                os.makedirs(dest, exist_ok=True)
                handle.move_storage(dest)
                meta["save_path"] = dest
                meta["move_pending"] = True
            if patch.get("file_priorities") is not None:
                self._apply_priorities(handle, meta, patch["file_priorities"])
            if patch.get("add_trackers"):
                for url in patch["add_trackers"]:
                    cleaned = str(url or "").strip()
                    if cleaned:
                        handle.add_tracker({"url": cleaned, "tier": 0})
            if patch.get("remove_trackers"):
                remove = {str(url) for url in patch["remove_trackers"]}
                keep = [item for item in handle.trackers() if item.get("url") not in remove]
                handle.replace_trackers([{"url": item["url"], "tier": int(item.get("tier") or 0)} for item in keep])
            if patch.get("add_peer"):
                self._connect_peer(handle, str(patch["add_peer"]))
            if isinstance(patch.get("rename"), dict):
                self._rename_file(handle, patch["rename"])
            self._save_library()
            return self._summary(handle, meta, handle.status())

    def remove(self, tid: str, delete_files: bool = False) -> None:
        with self._lock:
            handle, meta = self._require(tid)
            flag = int(lt.session.delete_files) if delete_files else 0
            try:
                self._ses.remove_torrent(handle, flag)
            except Exception as exc:
                self._log(f"Remove failed: {exc}")
            self._handles.pop(tid, None)
            self._records.pop(tid, None)
            self._blobs.pop(tid, None)
            for folder, suffix in ((self.resume_dir, ".fastresume"), (self.torrent_dir, ".torrent")):
                path = folder / f"{tid}{suffix}"
                path.unlink(missing_ok=True)
            self._save_library()
            extra = " and deleted the files" if delete_files else ""
            self._log(f'Removed "{meta.get("name") or tid}"{extra}')

    def reveal(self, tid: str) -> str:
        with self._lock:
            handle, _meta = self._require(tid)
            path = self._content_path(handle)
        _open_in_shell(path)
        return path

    def torrent_file_bytes(self, tid: str) -> tuple[str, bytes]:
        with self._lock:
            handle, meta = self._require(tid)
            stored = self.torrent_dir / f"{tid}.torrent"
            data = stored.read_bytes() if stored.exists() else self._export_bytes(handle)
            if not data:
                raise ValueError("Metadata is not ready yet")
            if not stored.exists():
                self._store_torrent_bytes(tid, data)
            stem = re.sub(r"[^A-Za-z0-9._ -]+", "_", meta.get("name") or tid).strip(" .")[:80]
            return (stem or tid) + ".torrent", data

    def create_torrent(
        self,
        content_path: str,
        output: str,
        trackers: str = "",
        comment: str = "",
        private: bool = False,
        piece_size: int = 0,
        hybrid: bool = False,
        start: bool = False,
    ) -> dict:
        content_path = os.path.abspath(content_path)
        if not os.path.exists(content_path):
            raise ValueError("Choose a file or folder that exists")
        output = os.path.abspath(output)
        piece_size = int(piece_size or 0)
        if piece_size and (piece_size < 16384 or piece_size % 16384 != 0):
            raise ValueError("Piece size must be a multiple of 16 KB")
        output_abs = output

        def include(path: str) -> bool:
            try:
                return os.path.abspath(path) != output_abs
            except Exception:
                return True

        entries = lt.list_files(content_path, include)
        if not entries:
            raise ValueError("That path has no files to put in a torrent")
        flags = 0 if hybrid else int(lt.create_torrent.v1_only)
        created = lt.create_torrent(entries, piece_size, flags)
        created.set_creator(f"Cairn/{__version__}")
        created.set_creation_date(int(time.time()))
        if comment:
            created.set_comment(comment)
        created.set_priv(bool(private))
        tier = 0
        for line in (trackers or "").splitlines():
            line = line.strip()
            if not line:
                tier += 1
                continue
            created.add_tracker(line, tier)
        branch = os.path.dirname(content_path)
        try:
            lt.set_piece_hashes(created, branch)
        except Exception as exc:
            raise ValueError(f"Could not hash the files: {exc}") from exc
        data = bytes(lt.bencode(created.generate()))
        _atomic(Path(output), data)
        ti = self._ti_from_bytes(data)
        result = {
            "path": output,
            "magnet": lt.make_magnet_uri(ti),
            "name": ti.name(),
            "size": int(ti.total_size()),
            "files": int(ti.files().num_files()),
            "info_hash": info_id(_hashes_of(ti)),
        }
        if start:
            self.add("path", output, save_path=branch, paused=False)
        self._log(f'Created torrent "{result["name"]}"')
        return result

    def update_settings(self, patch: dict) -> dict:
        with self._lock:
            self.settings = merge_settings(self.settings, patch)
            save_settings(self.settings)
            if self._ses:
                self._apply_pack()
                self._apply_proxy()
                self._apply_network_features()
                self._apply_swarm_flags()
                self._sync_schedule(force=True)
            return self.settings

    def import_blocklist(self, path: str) -> dict:
        path = os.path.abspath(path)
        with self._lock:
            count = self._load_blocklist(path)
            self.settings["blocklist_path"] = path
            save_settings(self.settings)
        self._log(f"Loaded {count} blocklist ranges from {path}")
        return {"ranges": count, "path": path}

    def refresh_rss(self) -> dict:
        with self._lock:
            feeds = [dict(feed) for feed in self.settings.get("rss_feeds") or []]
        added = 0
        for feed in feeds:
            if not feed.get("enabled"):
                continue
            try:
                items = _parse_feed(_fetch_url(feed["url"], limit=2 * 1024 * 1024))
            except Exception as exc:
                self._log(f'RSS {feed["url"]} failed: {exc}')
                continue
            seen = list(feed.get("seen") or [])
            known = set(seen)
            for item in items:
                guid = item["guid"]
                if guid in known:
                    continue
                if not _filter_matches(feed.get("filter") or "", item["title"]):
                    continue
                if not item["links"]:
                    seen.append(guid)
                    known.add(guid)
                    continue
                accepted = False
                for link in item["links"]:
                    try:
                        source = "magnet" if link.startswith("magnet:") else "url"
                        self.add(
                            source,
                            link,
                            save_path=feed.get("save_path") or "",
                            label=feed.get("label") or "",
                            paused=False,
                        )
                        added += 1
                        accepted = True
                        break
                    except ValueError as exc:
                        if "already" in str(exc).lower():
                            accepted = True
                            break
                        self._log(f'RSS skipped "{item["title"]}": {exc}')
                if accepted:
                    seen.append(guid)
                    known.add(guid)
            feed["seen"] = seen[-5000:]
        with self._lock:
            current = {item["id"]: item for item in self.settings.get("rss_feeds") or []}
            for feed in feeds:
                if feed["id"] in current:
                    current[feed["id"]]["seen"] = feed["seen"]
            self.settings["rss_feeds"] = list(current.values())
            save_settings(self.settings)
        if added:
            self._log(f"RSS added {added} torrent{'s' if added != 1 else ''}")
        return {"added": added}

    def _loop(self) -> None:
        while self._running:
            watched: list[Path] = []
            refresh = False
            with self._lock:
                try:
                    self._pump()
                    self._enforce_seed_limits()
                    self._sync_schedule(force=False)
                    now = time.time()
                    if now >= self._next_sample:
                        self._next_sample = now + 1
                        self._sample_speed()
                    if now >= self._next_watch:
                        self._next_watch = now + 5
                        watched = self._watch_paths()
                    if now >= self._next_resume:
                        self._next_resume = now + 30
                        self._request_resumes()
                    if now >= self._next_rss:
                        self._next_rss = now + 900
                        refresh = True
                except Exception as exc:
                    self._log(f"Session loop: {exc}")
            for path in watched:
                self._consume_watched(path)
            if refresh:
                try:
                    self.refresh_rss()
                except Exception as exc:
                    self._log(f"RSS refresh failed: {exc}")
            time.sleep(0.4)

    def _pump(self) -> None:
        for alert in self._ses.pop_alerts():
            self._handle_alert(alert)

    def _handle_alert(self, alert) -> None:
        kind = type(alert).__name__
        if kind in INTERESTING_ALERTS:
            try:
                self._log(alert.message())
            except Exception:
                self._log(kind)
        if kind == "save_resume_data_alert":
            self._write_resume(alert)
        elif kind == "metadata_received_alert":
            self._on_metadata(alert)
        elif kind == "torrent_finished_alert":
            self._on_finished(alert)
        elif kind == "storage_moved_alert":
            self._on_moved(alert, failed=False)
        elif kind == "storage_moved_failed_alert":
            self._on_moved(alert, failed=True)
        elif kind == "external_ip_alert":
            address = getattr(alert, "external_address", None) or getattr(alert, "address", None)
            if address:
                self._external_ip = str(address)

    def _on_metadata(self, alert) -> None:
        handle = alert.handle
        if not handle.is_valid():
            return
        tid = info_id(_hashes_of(handle))
        meta = self._records.get(tid)
        if not meta:
            return
        meta["name"] = self._torrent_name(handle, meta)
        pending = meta.get("pending_priorities")
        if pending:
            try:
                current = list(handle.get_file_priorities())
                if len(pending) == len(current):
                    handle.prioritize_files([int(item) for item in pending])
                meta["pending_priorities"] = None
            except Exception as exc:
                self._log(f"Could not apply file selection: {exc}")
        try:
            data = self._export_bytes(handle)
            if data:
                self._store_torrent_bytes(tid, data)
        except Exception as exc:
            self._log(f"Could not save the torrent file: {exc}")
        try:
            handle.save_resume_data()
        except Exception:
            pass
        self._save_library()

    def _on_finished(self, alert) -> None:
        handle = alert.handle
        if not handle.is_valid():
            return
        tid = info_id(_hashes_of(handle))
        meta = self._records.get(tid)
        if not meta or meta.get("moved") or meta.get("move_pending"):
            return
        if not self.settings.get("move_completed") or not self.settings.get("completed_path"):
            return
        try:
            gained = int(handle.status().all_time_download) - int(meta.get("base_download") or 0)
        except Exception:
            gained = 0
        if gained <= 0:
            return
        dest = self.settings["completed_path"]
        try:
            os.makedirs(dest, exist_ok=True)
            handle.move_storage(dest)
            meta["move_pending"] = True
            self._save_library()
            self._log(f'Moving "{meta.get("name") or tid}" to the completed folder')
        except Exception as exc:
            self._log(f"Could not move the completed download: {exc}")

    def _on_moved(self, alert, failed: bool) -> None:
        handle = getattr(alert, "handle", None)
        if handle is None or not handle.is_valid():
            return
        meta = self._records.get(info_id(_hashes_of(handle)))
        if not meta:
            return
        meta["move_pending"] = False
        if failed:
            meta["moved"] = False
            return
        try:
            meta["save_path"] = handle.save_path()
        except Exception:
            pass
        meta["moved"] = True
        self._save_library()
        try:
            handle.save_resume_data()
        except Exception:
            pass

    def _enforce_seed_limits(self) -> None:
        for tid, handle in list(self._handles.items()):
            meta = self._records.get(tid)
            if not meta or not handle.is_valid():
                continue
            if meta.get("manual") in ("stopped", "paused", "force"):
                continue
            try:
                status = handle.status()
            except Exception:
                continue
            if status.paused or not (status.is_seeding or status.is_finished):
                continue
            ratio_limit = meta.get("ratio_limit")
            if ratio_limit is None:
                ratio_limit = float(self.settings.get("seed_ratio") or 0)
            minutes = meta.get("seed_minutes")
            if minutes is None:
                minutes = int(self.settings.get("seed_minutes") or 0)
            if ratio_limit and status.all_time_download > 0:
                if status.all_time_upload / status.all_time_download >= float(ratio_limit):
                    self._set_manual(handle, meta, "stopped")
                    self._log(f'Stopped "{meta["name"]}" at the seed ratio')
                    continue
            if minutes and int(status.seeding_time) >= int(minutes) * 60:
                self._set_manual(handle, meta, "stopped")
                self._log(f'Stopped "{meta["name"]}" at the seed time limit')

    def _sample_speed(self) -> None:
        status = self._ses.status()
        self._history.append(
            {
                "t": int(time.time()),
                "down": int(status.payload_download_rate),
                "up": int(status.payload_upload_rate),
            }
        )

    def _sync_schedule(self, force: bool) -> None:
        mode = schedule_mode(self.settings)
        changed = mode != self._sched_mode
        self._sched_mode = mode
        if mode == "S":
            if not self._sched_holding:
                self._ses.pause()
                self._sched_holding = True
                self._log("Scheduler stopped transfers for this hour")
            return
        if self._sched_holding:
            self._ses.resume()
            self._sched_holding = False
            self._log("Scheduler resumed transfers")
            force = True
        if force or changed:
            self._apply_pack()

    def _watch_paths(self) -> list[Path]:
        if not self.settings.get("watch_enabled") or not self.settings.get("watch_path"):
            return []
        folder = Path(self.settings["watch_path"])
        if not folder.is_dir():
            return []
        found = []
        for entry in folder.glob("*.torrent"):
            if entry.is_file() and entry.stat().st_size > 0:
                found.append(entry)
        return found

    def _consume_watched(self, path: Path) -> None:
        try:
            self.add("path", str(path), paused=False)
            path.rename(path.with_suffix(".torrent.added"))
            self._log(f"Watch folder added {path.name}")
        except Exception as exc:
            self._log(f"Watch folder skipped {path.name}: {exc}")
            try:
                path.rename(path.with_suffix(".torrent.failed"))
            except Exception:
                pass

    def _restore(self) -> None:
        seen: set[str] = set()
        for path in sorted(self.resume_dir.glob("*.fastresume")):
            tid = path.stem.lower()
            try:
                params = lt.read_resume_data(path.read_bytes())
                self._adopt(params, tid, data=None)
                seen.add(tid)
            except Exception as exc:
                self._log(f"Could not resume {tid}: {exc}")
                torrent = self.torrent_dir / f"{tid}.torrent"
                if torrent.exists():
                    try:
                        self._adopt_bytes(torrent.read_bytes(), tid)
                        seen.add(tid)
                    except Exception as inner:
                        self._log(f"Could not restore {tid}: {inner}")
        for torrent in sorted(self.torrent_dir.glob("*.torrent")):
            tid = torrent.stem.lower()
            if tid in seen or tid in self._handles:
                continue
            try:
                self._adopt_bytes(torrent.read_bytes(), tid)
            except Exception as exc:
                self._log(f"Could not restore {torrent.name}: {exc}")

    def _adopt_bytes(self, data: bytes, tid_hint: str) -> None:
        ti = self._ti_from_bytes(data)
        params = lt.add_torrent_params()
        params.ti = ti
        self._adopt(params, tid_hint, data)

    def _adopt(self, params, tid_hint: str, data: bytes | None) -> None:
        meta = self._records.get(tid_hint) or self._records.get(tid_hint.lower())
        manual = (meta or {}).get("manual") or "started"
        save_path = (meta or {}).get("save_path") or getattr(params, "save_path", "") or self.settings["save_path"]
        if not getattr(params, "save_path", ""):
            params.save_path = save_path
        os.makedirs(params.save_path, exist_ok=True)
        params.flags = self._base_flags(paused=manual in ("paused", "stopped"), stop_when_ready=False)
        if manual == "force":
            params.flags = self._base_flags(paused=False, stop_when_ready=False)
            # force is not auto-managed; _base_flags(paused=False) sets auto_managed.
            # Correct it after add.
        handle = self._ses.add_torrent(params)
        tid = info_id(_hashes_of(handle)) or tid_hint
        if meta is None:
            meta = _blank_record(tid, self._torrent_name(handle, {}), str(params.save_path), manual)
            try:
                meta["base_download"] = int(handle.status().all_time_download)
            except Exception:
                meta["base_download"] = 0
        else:
            meta = dict(meta)
            meta["id"] = tid
            meta["name"] = self._torrent_name(handle, meta)
            meta["save_path"] = str(getattr(params, "save_path", "") or meta.get("save_path") or "")
        self._set_manual(handle, meta, manual if manual in ("started", "paused", "stopped", "force") else "started")
        if meta.get("sequential"):
            handle.set_flags(lt.torrent_flags.sequential_download)
        if meta.get("super_seeding"):
            handle.set_flags(lt.torrent_flags.super_seeding)
        self._handles[tid] = handle
        self._records[tid] = meta
        if data:
            self._store_torrent_bytes(tid, data)
        self._save_library()

    def _request_resumes(self) -> None:
        flag = 0
        for name in ("only_if_modified", "save_info_dict"):
            if hasattr(lt.torrent_handle, name):
                flag |= int(getattr(lt.torrent_handle, name))
        for handle in self._handles.values():
            try:
                if handle.is_valid():
                    handle.save_resume_data(flag)
            except Exception:
                try:
                    handle.save_resume_data()
                except Exception:
                    pass

    def _write_resume(self, alert) -> None:
        handle = alert.handle
        if not handle.is_valid():
            return
        tid = info_id(_hashes_of(handle))
        if not tid:
            return
        try:
            data = bytes(lt.write_resume_data_buf(alert.params))
        except Exception as exc:
            self._log(f"Could not write resume data: {exc}")
            return
        _atomic(self.resume_dir / f"{tid}.fastresume", data)

    def _snapshot(self, detail_id: str | None) -> dict:
        status = self._ses.status()
        vpn_state, vpn_label = self._vpn_status()
        stats = {
            "download_rate": int(status.payload_download_rate),
            "upload_rate": int(status.payload_upload_rate),
            "dht_nodes": int(status.dht_nodes),
            "has_incoming": bool(status.has_incoming_connections),
            "listen_port": int(self._ses.listen_port() or 0),
            "external_ip": "" if self._tunneling() else self._external_ip,
            "total_downloaded": 0,
            "total_uploaded": 0,
            "schedule": self._sched_mode,
            "scheduler_enabled": bool(self.settings.get("scheduler_enabled")),
            "dht": bool(self.settings.get("dht")) and not self._tunneling(),
            "vpn": vpn_state,
            "vpn_label": vpn_label,
        }
        torrents = []
        detail = None
        for tid, handle in list(self._handles.items()):
            meta = self._records.get(tid)
            if meta is None or not handle.is_valid():
                continue
            try:
                item_status = handle.status()
                summary = self._summary(handle, meta, item_status)
            except Exception as exc:
                self._log(f"Status failed for {tid}: {exc}")
                continue
            torrents.append(summary)
            stats["total_downloaded"] += int(item_status.all_time_download)
            stats["total_uploaded"] += int(item_status.all_time_upload)
            if detail_id and tid == detail_id:
                try:
                    detail = self._detail(handle, meta, item_status, summary)
                except Exception as exc:
                    detail = {"id": tid, "error": str(exc)}
        labels = sorted({item["label"] for item in torrents if item.get("label")})
        return {
            "torrents": torrents,
            "stats": stats,
            "speed_history": list(self._history),
            "labels": labels,
            "detail": detail,
            "app": {"name": "Cairn", "version": __version__, "build": int(BUILD), "engine": str(lt.version)},
        }

    def _summary(self, handle, meta: dict, status) -> dict:
        ti = self._torrent_info(handle, status)
        size = int(ti.total_size()) if ti is not None else int(status.total_wanted or 0)
        remaining = max(0, int(status.total_wanted) - int(status.total_wanted_done))
        rate = int(status.download_rate)
        if rate > 0 and remaining > 0 and not status.paused:
            eta = int(remaining / rate)
        else:
            eta = -1
        downloaded_total = int(status.all_time_download)
        uploaded_total = int(status.all_time_upload)
        ratio = (uploaded_total / downloaded_total) if downloaded_total > 0 else None
        seeds_total = int(status.num_complete) if int(status.num_complete) >= 0 else int(status.list_seeds)
        peers_total = int(status.num_incomplete) if int(status.num_incomplete) >= 0 else int(status.list_peers)
        flags = int(handle.flags())
        manual = meta.get("manual") or "started"
        name = self._torrent_name(handle, meta)
        if name and name != meta.get("name") and not _hashish(name):
            meta["name"] = name
        return {
            "id": meta["id"],
            "name": name,
            "progress": float(status.progress or 0),
            "state": self._state_name(status, manual),
            "size": size,
            "downloaded": int(status.total_wanted_done),
            "uploaded": uploaded_total,
            "download_rate": rate,
            "upload_rate": int(status.upload_rate),
            "eta": eta,
            "seeds_connected": int(status.num_seeds),
            "seeds_total": max(0, seeds_total),
            "peers_connected": int(status.num_peers),
            "peers_total": max(0, peers_total),
            "ratio": ratio,
            "added_at": int(meta.get("added_at") or 0),
            "label": meta.get("label") or "",
            "save_path": meta.get("save_path") or "",
            "queue_position": int(status.queue_position),
            "sequential": bool(flags & int(lt.torrent_flags.sequential_download)),
            "super_seeding": bool(flags & int(lt.torrent_flags.super_seeding)),
            "force_start": manual == "force",
            "has_metadata": bool(status.has_metadata),
            "error": self._error_text(status),
            "manual": manual,
        }

    def _detail(self, handle, meta: dict, status, summary: dict) -> dict:
        ti = self._torrent_info(handle, status)
        files = []
        pieces = []
        have_pieces = 0
        piece_size = 0
        num_pieces = 0
        comment = ""
        creator = ""
        if ti is not None:
            piece_size = int(ti.piece_length())
            num_pieces = int(ti.num_pieces())
            try:
                comment = ti.comment() or ""
                creator = ti.creator() or ""
            except Exception:
                pass
            try:
                priorities = list(handle.get_file_priorities())
            except Exception:
                priorities = []
            try:
                progress = list(handle.file_progress())
            except Exception:
                progress = []
            files = _file_rows(ti, priorities, progress)
        try:
            raw_pieces = list(status.pieces)
        except Exception:
            raw_pieces = []
        have_pieces = sum(1 for bit in raw_pieces if bit)
        pieces = _downsample_pieces(raw_pieces)
        download_limit = int(handle.download_limit())
        upload_limit = int(handle.upload_limit())
        peers = _peer_rows(handle)
        detail = dict(summary)
        detail.update(
            {
                "comment": comment,
                "creator": creator,
                "piece_size": piece_size,
                "num_pieces": num_pieces or len(raw_pieces),
                "have_pieces": have_pieces,
                "pieces": pieces,
                "availability": _availability(status),
                "active_time": int(status.active_time),
                "seeding_time": int(status.seeding_time),
                "download_limit": 0 if download_limit <= 0 else int(round(download_limit / 1024)),
                "upload_limit": 0 if upload_limit <= 0 else int(round(upload_limit / 1024)),
                "ratio_limit": meta.get("ratio_limit"),
                "seed_minutes": meta.get("seed_minutes"),
                "magnet": self._magnet(handle, meta, ti),
                "files": files,
                "trackers": _tracker_rows(handle),
                "peers": peers,
            }
        )
        return detail

    def _state_name(self, status, manual: str) -> str:
        if status.errc.value() != 0:
            return "error"
        if status.state in (
            lt.torrent_status.checking_files,
            lt.torrent_status.checking_resume_data,
            lt.torrent_status.allocating,
            lt.torrent_status.queued_for_checking,
        ):
            return "checking"
        if not status.has_metadata:
            if status.paused:
                if manual == "stopped":
                    return "stopped"
                if manual == "started" and status.auto_managed:
                    return "queued"
                return "paused"
            return "metadata"
        if status.paused:
            if manual == "started" and status.auto_managed:
                return "queued"
            if manual == "stopped":
                return "stopped"
            return "paused"
        if status.is_seeding or status.state == lt.torrent_status.seeding:
            return "seeding"
        if status.is_finished or status.state == lt.torrent_status.finished:
            return "finished"
        return "downloading"

    def _error_text(self, status) -> str:
        try:
            if status.errc.value() == 0:
                return ""
        except Exception:
            return ""
        text = str(getattr(status, "error", "") or "")
        return text or status.errc.message()

    def _magnet(self, handle, meta, ti) -> str:
        if ti is not None:
            try:
                return lt.make_magnet_uri(ti)
            except Exception:
                pass
        return meta.get("magnet") or ""

    def _torrent_info(self, handle, status):
        if not status.has_metadata:
            return None
        try:
            info = handle.torrent_file()
        except Exception:
            return None
        if info is None:
            return None
        try:
            if hasattr(info, "is_valid") and not info.is_valid():
                return None
        except Exception:
            return None
        return info

    def _torrent_name(self, handle, meta: dict) -> str:
        live = ""
        try:
            status = handle.status()
            live = (getattr(status, "name", "") or "").strip()
        except Exception:
            pass
        if not live:
            try:
                live = (handle.name() or "").strip()
            except Exception:
                pass
        stored = (meta.get("name") or "").strip()
        if live and not _hashish(live):
            return live
        if stored and not _hashish(stored):
            return stored
        return stored or live or "Magnet link"

    def _set_manual(self, handle, meta: dict, manual: str) -> None:
        meta["manual"] = manual
        try:
            handle.unset_flags(lt.torrent_flags.stop_when_ready)
        except Exception:
            pass
        if manual == "force":
            try:
                handle.clear_error()
            except Exception:
                pass
            handle.unset_flags(lt.torrent_flags.auto_managed)
            handle.unset_flags(lt.torrent_flags.paused)
            handle.resume()
        elif manual == "started":
            try:
                handle.clear_error()
            except Exception:
                pass
            handle.set_flags(lt.torrent_flags.auto_managed)
            handle.unset_flags(lt.torrent_flags.paused)
            handle.resume()
        else:
            handle.unset_flags(lt.torrent_flags.auto_managed)
            handle.pause()
        self._save_library()

    def _apply_priorities(self, handle, meta: dict, raw) -> None:
        current = []
        try:
            current = list(handle.get_file_priorities())
        except Exception:
            current = []
        if raw and isinstance(raw[0], dict):
            if not current:
                raise ValueError("File list is not ready yet")
            for item in raw:
                index = int(item["index"])
                if index < 0 or index >= len(current):
                    raise ValueError("File selection is out of range")
                current[index] = max(0, min(7, int(item["priority"])))
            prios = current
        else:
            prios = [max(0, min(7, int(item))) for item in raw]
        try:
            has_metadata = bool(handle.status().has_metadata)
        except Exception:
            has_metadata = False
        if has_metadata and current and len(prios) != len(current) and not (raw and isinstance(raw[0], dict)):
            raise ValueError("File selection does not match this torrent")
        if has_metadata and prios:
            handle.prioritize_files(prios)
        else:
            meta["pending_priorities"] = prios

    def _connect_peer(self, handle, text: str) -> None:
        text = text.strip()
        try:
            if text.startswith("["):
                host, port_text = text[1:].split("]:", 1)
            else:
                host, port_text = text.rsplit(":", 1)
            port = int(port_text)
        except (ValueError, IndexError) as exc:
            raise ValueError("Enter a peer as ip:port") from exc
        if not host or not (1 <= port <= 65535):
            raise ValueError("Enter a peer as ip:port")
        handle.connect_peer((host, port))

    def _rename_file(self, handle, item: dict) -> None:
        name = str(item.get("name") or "").replace("\\", "/").strip()
        if not name or name.startswith("/") or any(part == ".." for part in name.split("/")):
            raise ValueError("Pick a name inside the torrent")
        handle.rename_file(int(item["index"]), name)

    def _require(self, tid: str):
        handle = self._handles.get(tid)
        meta = self._records.get(tid)
        if handle is None or meta is None or not handle.is_valid():
            raise KeyError(tid)
        return handle, meta

    def _content_path(self, handle) -> str:
        save = handle.save_path() or ""
        try:
            name = handle.name() or ""
        except Exception:
            name = ""
        if save and name:
            candidate = os.path.join(save, name)
            if os.path.exists(candidate):
                return candidate
        return save

    def _base_flags(self, *, paused: bool, stop_when_ready: bool):
        flags = (
            lt.torrent_flags.duplicate_is_error
            | lt.torrent_flags.update_subscribe
            | lt.torrent_flags.apply_ip_filter
        )
        tunnel = self._tunneling()
        if not self.settings.get("pex", True):
            flags |= lt.torrent_flags.disable_pex
        if tunnel or not self.settings.get("dht", True):
            flags |= lt.torrent_flags.disable_dht
        if tunnel or not self.settings.get("lsd", True):
            flags |= lt.torrent_flags.disable_lsd
        if stop_when_ready:
            flags |= lt.torrent_flags.stop_when_ready | lt.torrent_flags.auto_managed
        elif paused:
            flags |= lt.torrent_flags.paused
        else:
            flags |= lt.torrent_flags.auto_managed
        return flags

    def _apply_swarm_flags(self) -> None:
        for handle in self._handles.values():
            if not handle.is_valid():
                continue
            tunnel = self._tunneling()
            self._toggle(handle, lt.torrent_flags.disable_pex, not self.settings.get("pex", True))
            self._toggle(handle, lt.torrent_flags.disable_dht, tunnel or not self.settings.get("dht", True))
            self._toggle(handle, lt.torrent_flags.disable_lsd, tunnel or not self.settings.get("lsd", True))

    def _toggle(self, handle, flag, enabled: bool) -> None:
        if enabled:
            handle.set_flags(flag)
        else:
            handle.unset_flags(flag)

    def _pack(self) -> dict:
        port = 0 if self.settings.get("random_port") else int(self.settings.get("listen_port") or 6881)
        tunnel = self._tunneling()
        # Local bind while the tunnel is on, so peers cannot connect to the real address.
        listen = f"127.0.0.1:{port},[::1]:{port}" if tunnel else f"0.0.0.0:{port},[::]:{port}"
        encryption = self.settings.get("encryption") or "enabled"
        if tunnel and encryption == "disabled":
            encryption = "enabled"
        policy = {"forced": 0, "enabled": 1, "disabled": 2}.get(encryption, 1)
        level = 1 if encryption == "disabled" else 3
        cache_blocks = max(64, int(self.settings.get("disk_cache_mb") or 1) * 64)
        return {
            "listen_interfaces": listen,
            "listen_system_port_fallback": True,
            "enable_dht": bool(self.settings.get("dht")) and not tunnel,
            "enable_lsd": bool(self.settings.get("lsd")) and not tunnel,
            "enable_upnp": bool(self.settings.get("upnp")) and not tunnel,
            "enable_natpmp": bool(self.settings.get("natpmp")) and not tunnel,
            "enable_outgoing_utp": bool(self.settings.get("utp")) and not tunnel,
            "enable_incoming_utp": False if tunnel else bool(self.settings.get("utp")),
            "enable_outgoing_tcp": True,
            "enable_incoming_tcp": not tunnel,
            "download_rate_limit": self._configured_rate("down"),
            "upload_rate_limit": self._configured_rate("up"),
            "connections_limit": int(self.settings.get("max_connections") or 200),
            "unchoke_slots_limit": int(self.settings.get("max_uploads") or 8),
            "active_downloads": int(self.settings.get("max_active_downloads") or 5),
            "active_seeds": int(self.settings.get("max_active_seeds") or 5),
            "active_limit": int(self.settings.get("max_active") or 10),
            "dont_count_slow_torrents": True,
            "announce_to_all_trackers": bool(self.settings.get("announce_to_all_trackers")),
            "announce_to_all_tiers": bool(self.settings.get("announce_to_all_trackers")),
            "anonymous_mode": bool(self.settings.get("anonymous_mode")) or tunnel,
            "out_enc_policy": policy,
            "in_enc_policy": policy,
            "allowed_enc_level": level,
            "prefer_rc4": encryption == "forced",
            "user_agent": f"Cairn/{__version__}",
            "peer_fingerprint": "-CN0100-",
            "share_ratio_limit": -1,
            "seed_time_limit": -1,
            "cache_size": cache_blocks,
            "dht_bootstrap_nodes": "dht.libtorrent.org:25401,router.bittorrent.com:6881,router.utorrent.com:6881,dht.transmissionbt.com:6881",
            "alert_mask": (
                int(lt.alert_category.error)
                | int(lt.alert_category.storage)
                | int(lt.alert_category.status)
                | int(lt.alert_category.port_mapping)
                | int(lt.alert_category.tracker)
            ),
        }

    def _configured_rate(self, direction: str) -> int:
        mode = schedule_mode(self.settings)
        if mode == "L":
            key = "alt_download_limit" if direction == "down" else "alt_upload_limit"
        else:
            key = "download_limit" if direction == "down" else "upload_limit"
        kilobytes = int(self.settings.get(key) or 0)
        if kilobytes <= 0:
            return 0
        return kilobytes * 1024

    def _apply_pack(self, pack: dict | None = None) -> None:
        pack = pack or self._pack()
        if self._tunneling():
            # Close public sockets before the listen address changes, or uTP
            # opens one last socket on every interface.
            self._apply_settings_dict(
                {
                    "enable_incoming_utp": False,
                    "enable_outgoing_utp": False,
                    "enable_incoming_tcp": False,
                    "enable_dht": False,
                    "enable_lsd": False,
                    "enable_upnp": False,
                    "enable_natpmp": False,
                }
            )
            listen = pack.get("listen_interfaces")
            rest = {key: value for key, value in pack.items() if key != "listen_interfaces"}
            self._apply_settings_dict(rest)
            if listen:
                self._apply_settings_dict({"listen_interfaces": listen})
            return
        self._apply_settings_dict(pack)

    def _apply_settings_dict(self, pack: dict) -> None:
        try:
            self._ses.apply_settings(pack)
            return
        except Exception as exc:
            self._log(f"Adjusted session settings ({exc})")
        for key, value in pack.items():
            try:
                trial = self._ses.get_settings()
                trial[key] = value
                self._ses.apply_settings(trial)
            except Exception as exc:
                self._log(f"Skipped setting {key}: {exc}")

    def _tunneling(self) -> bool:
        return bool(self.settings.get("vpn_enabled"))

    def _vpn_status(self) -> tuple[str, str]:
        if not self._tunneling():
            return "off", ""
        kind = str(self.settings.get("proxy_type") or "none")
        host = str(self.settings.get("proxy_host") or "").strip()
        try:
            port = int(self.settings.get("proxy_port") or 0)
        except (TypeError, ValueError):
            port = 0
        if kind == "none" or not host or port <= 0:
            return "blocked", ""
        return "on", f"{host}:{port}"

    def _apply_proxy(self) -> None:
        state, label = self._vpn_status()
        try:
            proxy = lt.proxy_type_t.proxy_settings()
            if state == "on":
                kind = str(self.settings.get("proxy_type") or "socks5")
                proxy.type = getattr(lt.proxy_type_t, kind)
                proxy.hostname = str(self.settings.get("proxy_host") or "").strip()
                proxy.port = int(self.settings.get("proxy_port") or 0)
                proxy.username = str(self.settings.get("proxy_username") or "")
                proxy.password = str(self.settings.get("proxy_password") or "")
            elif state == "blocked":
                # Nothing listens here. Connections fail instead of going direct.
                proxy.type = lt.proxy_type_t.socks5
                proxy.hostname = "127.0.0.1"
                proxy.port = 9
                proxy.username = ""
                proxy.password = ""
            else:
                proxy.type = lt.proxy_type_t.none
                proxy.hostname = ""
                proxy.port = 0
                proxy.username = ""
                proxy.password = ""
            proxy.proxy_peer_connections = state != "off"
            proxy.proxy_hostnames = state != "off"
            if hasattr(proxy, "proxy_tracker_connections"):
                proxy.proxy_tracker_connections = state != "off"
            self._ses.set_proxy(proxy)
        except Exception as exc:
            self._log(f"Proxy was not applied: {exc}")
            return
        note = f"{state}:{label}"
        if note == self._vpn_note:
            return
        previous = self._vpn_note
        self._vpn_note = note
        if previous is None:
            return
        if state == "off":
            self._log("VPN off")
        elif state == "blocked":
            self._log("VPN is on with no server. Transfers stay blocked.")
        else:
            self._log(f"VPN on via {label}")

    def _apply_network_features(self) -> None:
        tunnel = self._tunneling()
        if self.settings.get("dht") and not tunnel:
            try:
                self._ses.start_dht()
                for host, port in DHT_ROUTERS:
                    self._ses.add_dht_router(host, port)
            except Exception as exc:
                self._log(f"DHT: {exc}")
        else:
            try:
                self._ses.stop_dht()
            except Exception as exc:
                self._log(f"DHT: {exc}")
        for name in ("lsd", "upnp", "natpmp"):
            enabled = bool(self.settings.get(name)) and not tunnel
            try:
                getattr(self._ses, f"start_{name}" if enabled else f"stop_{name}")()
            except Exception as exc:
                self._log(f"{name}: {exc}")

    def _load_blocklist(self, path: str) -> int:
        ranges = _read_blocklist(path)
        if not ranges:
            raise ValueError("No ranges found in that blocklist")
        ip_filter = lt.ip_filter()
        for start, end in ranges:
            ip_filter.add_rule(start, end, 1)
        self._ses.set_ip_filter(ip_filter)
        return len(ranges)

    def _resolve_source(self, source: str, value: str) -> tuple[bytes | None, str | None]:
        source = (source or "").lower()
        if source == "magnet":
            magnet = (value or "").strip()
            if not magnet.startswith("magnet:?"):
                raise ValueError("That is not a magnet link")
            try:
                lt.parse_magnet_uri(magnet)
            except Exception as exc:
                raise ValueError(f"Could not read that magnet link: {exc}") from exc
            return None, magnet
        if source == "url":
            return _fetch_url(str(value).strip()), None
        if source == "path":
            path = os.path.abspath(value)
            if not os.path.isfile(path):
                raise ValueError("Torrent file not found")
            return Path(path).read_bytes(), None
        if source == "base64":
            try:
                return base64.b64decode(value), None
            except Exception as exc:
                raise ValueError("Could not read that torrent file") from exc
        raise ValueError("Unknown torrent source")

    def _ti_from_bytes(self, data: bytes):
        if not data:
            raise ValueError("The torrent file is empty")
        try:
            return lt.torrent_info(bytes(data))
        except Exception as exc:
            raise ValueError(f"Could not read this torrent: {exc}") from exc

    def _describe(self, ti, tid: str) -> dict:
        return {
            "id": tid,
            "name": ti.name(),
            "size": int(ti.total_size()),
            "comment": ti.comment() or "",
            "creator": ti.creator() or "",
            "piece_size": int(ti.piece_length()),
            "num_pieces": int(ti.num_pieces()),
            "files": _file_rows(ti, None, None),
            "trackers": _info_trackers(ti),
        }

    def _export_bytes(self, handle) -> bytes | None:
        try:
            info = handle.torrent_file()
        except Exception:
            return None
        if info is None:
            return None
        try:
            created = lt.create_torrent(info)
            return bytes(lt.bencode(created.generate()))
        except Exception:
            return None

    def _store_torrent_bytes(self, tid: str, data: bytes) -> None:
        _atomic(self.torrent_dir / f"{tid}.torrent", data)

    def _load_library(self) -> None:
        path = self.root / "library.json"
        self._records = {}
        if not path.exists():
            return
        try:
            loaded = json_load(path)
        except Exception as exc:
            self._log(f"Could not read the torrent library: {exc}")
            return
        if isinstance(loaded, dict):
            for tid, meta in loaded.items():
                if isinstance(meta, dict):
                    meta.setdefault("id", tid)
                    self._records[str(tid).lower()] = meta

    def _save_library(self) -> None:
        _atomic(self.root / "library.json", json.dumps(self._records, indent=2).encode("utf-8"))

    def _log(self, message: str) -> None:
        self._logs.append(f"{time.strftime('%H:%M:%S')}  {message}")

    @staticmethod
    def _kb_to_limit(value) -> int:
        kilobytes = int(value or 0)
        if kilobytes <= 0:
            return 0
        return kilobytes * 1024

    @staticmethod
    def _optional_number(value, caster):
        if value is None or value == "":
            return None
        return caster(value)


def json_load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _file_rows(ti, priorities, progress) -> list[dict]:
    storage = ti.files()
    rows = []
    count = int(storage.num_files())
    for index in range(count):
        size = int(storage.file_size(index))
        done = int(progress[index]) if progress and index < len(progress) else 0
        priority = int(priorities[index]) if priorities and index < len(priorities) else 4
        rows.append(
            {
                "index": index,
                "path": storage.file_path(index).replace("\\", "/"),
                "size": size,
                "priority": priority,
                "progress": (done / size) if size else 1,
            }
        )
    return rows


def _info_trackers(ti) -> list[str]:
    urls = []
    try:
        trackers = ti.trackers() or []
    except Exception:
        return urls
    for tracker in trackers:
        if isinstance(tracker, dict):
            url = tracker.get("url") or ""
        else:
            url = getattr(tracker, "url", "") or ""
        if url:
            urls.append(str(url))
    return urls


def _tracker_rows(handle) -> list[dict]:
    rows = []
    try:
        trackers = handle.trackers()
    except Exception:
        return rows
    now = int(time.time())
    for tracker in trackers:
        error = tracker.get("last_error") or {}
        error_value = int(error.get("value") or 0) if isinstance(error, dict) else 0
        message = tracker.get("message") or ""
        if tracker.get("updating"):
            status = "Updating"
        elif error_value:
            status = message or f"Error {error_value}"
        elif tracker.get("fails"):
            status = message or f"Failed ({tracker['fails']})"
        else:
            status = message or "Working"
        next_announce = int(tracker.get("next_announce") or 0)
        seeds = tracker.get("scrape_complete")
        peers = tracker.get("scrape_incomplete")
        rows.append(
            {
                "url": tracker.get("url") or "",
                "tier": int(tracker.get("tier") or 0),
                "status": status,
                "seeds": seeds if isinstance(seeds, int) and seeds >= 0 else None,
                "peers": peers if isinstance(peers, int) and peers >= 0 else None,
                "next_announce": max(0, next_announce - now) if next_announce > 0 else 0,
                "message": message,
            }
        )
    return rows


def _peer_rows(handle) -> list[dict]:
    try:
        infos = handle.get_peer_info()
    except Exception:
        return []
    rows = []
    for peer in list(infos)[:400]:
        address = peer.ip
        if isinstance(address, tuple):
            text = f"{address[0]}:{address[1]}"
        else:
            text = str(address)
        rows.append(
            {
                "address": text,
                "client": str(getattr(peer, "client", "") or ""),
                "progress": float(getattr(peer, "progress", 0) or 0),
                "down_speed": int(getattr(peer, "payload_down_speed", 0) or 0),
                "up_speed": int(getattr(peer, "payload_up_speed", 0) or 0),
                "flags": _peer_flags(peer),
            }
        )
    rows.sort(key=lambda item: item["down_speed"], reverse=True)
    return rows


def _peer_flags(peer) -> str:
    flags = int(getattr(peer, "flags", 0) or 0)
    parts = []
    mapping = (
        (lt.peer_info.seed, "seed"),
        (lt.peer_info.interesting, "interested"),
        (lt.peer_info.remote_interested, "interesting"),
        (lt.peer_info.choked, "choked"),
        (lt.peer_info.remote_choked, "peer choked"),
        (lt.peer_info.rc4_encrypted, "encrypted"),
        (lt.peer_info.plaintext_encrypted, "encrypted"),
        (lt.peer_info.optimistic_unchoke, "optimistic"),
        (lt.peer_info.snubbed, "snubbed"),
        (lt.peer_info.upload_only, "upload only"),
        (lt.peer_info.endgame_mode, "endgame"),
        (lt.peer_info.i2p_socket, "I2P"),
        (lt.peer_info.holepunched, "holepunch"),
    )
    for bit, label in mapping:
        if flags & int(bit) and label not in parts:
            parts.append(label)
    try:
        if "utp" in str(peer.connection_type).lower():
            parts.append("uTP")
    except Exception:
        pass
    source = int(getattr(peer, "source", 0) or 0)
    for bit, label in ((1, "tracker"), (2, "DHT"), (4, "PEX"), (8, "LSD")):
        if source & bit:
            parts.append(label)
    return ", ".join(parts)


def _downsample_pieces(raw: list) -> list[int]:
    count = len(raw)
    if count <= 4096:
        return [1 if bit else 0 for bit in raw]
    output = []
    step = count / 4096
    for index in range(4096):
        start = int(index * step)
        end = max(start + 1, int((index + 1) * step))
        chunk = raw[start:end]
        have = sum(1 for bit in chunk if bit)
        if have <= 0:
            output.append(0)
        elif have >= len(chunk):
            output.append(1)
        else:
            output.append(2)
    return output


def _availability(status):
    try:
        copies = float(status.distributed_copies)
    except Exception:
        return None
    if copies < 0:
        return None
    return round(copies, 2)


def _fetch_url(url: str, limit: int = 8 * 1024 * 1024) -> bytes:
    if not url.startswith(("http://", "https://")):
        raise ValueError("Only http and https links are supported")
    request = urllib.request.Request(url, headers={"User-Agent": f"Cairn/{__version__}"})
    with urllib.request.urlopen(request, timeout=20) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("That file is too large")
    return data


def _parse_feed(data: bytes) -> list[dict]:
    import xml.etree.ElementTree as ET

    root = ET.fromstring(data)
    items = []
    nodes = list(root.findall(".//item"))
    atom = "{http://www.w3.org/2005/Atom}"
    nodes.extend(root.findall(f".//{atom}entry"))
    for node in nodes:
        title = _xml_text(node, "title") or _xml_text(node, f"{atom}title") or "Untitled"
        guid = _xml_text(node, "guid") or _xml_text(node, f"{atom}id") or title
        links = []
        enclosure = node.find("enclosure")
        if enclosure is not None and enclosure.get("url"):
            kind = (enclosure.get("type") or "").lower()
            url = enclosure.get("url") or ""
            if url.startswith("magnet:") or "bittorrent" in kind or kind == "application/octet-stream" or _looks_like_torrent(url):
                links.append(url)
        for link_node in node.findall("link") + node.findall(f"{atom}link"):
            href = (link_node.text or "").strip() or link_node.get("href") or ""
            if href.startswith("magnet:") or _looks_like_torrent(href):
                links.append(href)
        blob = "".join(node.itertext())
        links.extend(MAGNET_RE.findall(blob))
        links.extend(TORRENT_URL_RE.findall(blob))
        unique = list(dict.fromkeys(links))
        items.append({"title": title, "guid": guid, "links": unique})
    return items


def _xml_text(node, name: str) -> str:
    found = node.find(name)
    if found is None or found.text is None:
        return ""
    return found.text.strip()


def _looks_like_torrent(url: str) -> bool:
    return url.lower().split("?", 1)[0].endswith(".torrent")


def _filter_matches(pattern: str, title: str) -> bool:
    if not pattern:
        return True
    try:
        return re.search(pattern, title, re.I) is not None
    except re.error:
        return False


def _read_blocklist(path: str) -> list[tuple[str, str]]:
    opener = gzip.open if path.lower().endswith(".gz") else open
    ranges = []
    with opener(path, "rt", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            match = RANGE_RE.search(line)
            if not match:
                continue
            start, end = match.group(1), match.group(2)
            if not (_ip_ok(start) and _ip_ok(end)):
                continue
            if _ip_key(start) > _ip_key(end):
                start, end = end, start
            ranges.append((start, end))
            if len(ranges) >= 2_000_000:
                break
    return ranges


def _ip_ok(text: str) -> bool:
    parts = text.split(".")
    if len(parts) != 4:
        return False
    try:
        return all(0 <= int(part) <= 255 for part in parts)
    except ValueError:
        return False


def _ip_key(text: str) -> tuple[int, int, int, int]:
    return tuple(int(part) for part in text.split("."))  # type: ignore[return-value]


def _open_in_shell(path: str) -> None:
    import subprocess
    import sys

    if not path:
        raise ValueError("This torrent has no folder yet")
    path = os.path.normpath(path)
    if os.name == "nt":
        if os.path.isfile(path):
            subprocess.Popen(["explorer", "/select,", path])
        else:
            os.startfile(path if os.path.isdir(path) else os.path.dirname(path) or path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])
