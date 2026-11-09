from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

from .engine import Engine
from .server import serve
from .settings import schedule_mode


def main() -> None:
    home = tempfile.mkdtemp(prefix="shakya-selftest-")
    os.environ["SHAKYA_HOME"] = home
    content = Path(home) / "seed"
    content.mkdir()
    (content / "notes.txt").write_text("shakya self test\n", encoding="utf-8")
    (content / "extra.txt").write_text("second file for priorities\n", encoding="utf-8")
    output = Path(home) / "seed.torrent"
    block = Path(home) / "block.txt"
    block.write_text("spam:1.2.3.0 - 1.2.3.255 , 000 , test\n9.9.9.9 - 9.9.9.10\n", encoding="utf-8")

    engine = Engine()
    engine.settings["dht"] = False
    engine.settings["pex"] = False
    engine.settings["lsd"] = False
    engine.settings["upnp"] = False
    engine.settings["natpmp"] = False
    engine.settings["listen_port"] = 47621
    engine.settings["save_path"] = str(Path(home) / "downloads")
    engine.start()
    try:
        created = engine.create_torrent(
            str(content),
            str(output),
            trackers="udp://tracker.opentrackr.org:1337/announce\n",
            comment="self test",
            private=False,
            piece_size=16384,
            hybrid=False,
            start=False,
        )
        assert created["info_hash"], created
        assert output.exists()
        preview = engine.preview("path", str(output))
        assert len(preview["files"]) == 2, preview
        assert preview["id"] == created["info_hash"]
        added = engine.add(
            "preview",
            preview["id"],
            save_path=str(content.parent),
            label="docs",
            paused=True,
            priorities=[0, 4],
            sequential=True,
        )
        assert added["state"] in ("paused", "checking", "stopped", "queued", "seeding", "finished"), added
        assert added["label"] == "docs"
        engine.action(added["id"], "resume")
        time.sleep(1.2)
        updated = engine.update(added["id"], {"download_limit": 32, "label": "kept"})
        assert updated["label"] == "kept"
        detail = engine.snapshot(added["id"])["detail"]
        assert detail["files"], detail
        assert detail["download_limit"] == 32, detail["download_limit"]
        assert any(item["priority"] == 0 for item in detail["files"]), detail["files"]
        engine.update(added["id"], {"download_limit": 0})
        loaded = engine.import_blocklist(str(block))
        assert loaded["ranges"] == 2, loaded
        grid = "F" * 168
        assert schedule_mode({"scheduler_enabled": True, "scheduler": grid}, time.localtime()) == "F"
        try:
            engine.preview("base64", "aaa")
            raise AssertionError("bad torrent should fail")
        except ValueError:
            pass
        httpd, url = serve(engine, port=0, dist_dir=None)
        try:
            health = json.loads(urllib.request.urlopen(url + "api/health", timeout=5).read().decode())
            assert health["ok"] is True
            state = json.loads(urllib.request.urlopen(url + "api/state", timeout=5).read().decode())
            assert any(item["id"] == added["id"] for item in state["torrents"])
        finally:
            httpd.shutdown()
        magnet = "magnet:?xt=urn:btih:" + ("ab" * 20) + "&dn=Background+Fetch"
        added_magnet = engine.add("magnet", magnet, paused=False)
        assert added_magnet["name"] == "Background Fetch", added_magnet
        assert added_magnet["manual"] == "started", added_magnet
        assert added_magnet["has_metadata"] is False
        deadline = time.time() + 3
        running = added_magnet
        while time.time() < deadline:
            running = next(item for item in engine.snapshot()["torrents"] if item["id"] == added_magnet["id"])
            if running["state"] not in ("paused", "stopped", "error", "checking"):
                break
            time.sleep(0.1)
        assert running["state"] == "metadata", running
        engine.action(added_magnet["id"], "pause")
        time.sleep(0.4)
        paused = next(item for item in engine.snapshot()["torrents"] if item["id"] == added_magnet["id"])
        assert paused["state"] == "paused", paused
        engine.action(added_magnet["id"], "resume")
        time.sleep(0.4)
        resumed = next(item for item in engine.snapshot()["torrents"] if item["id"] == added_magnet["id"])
        assert resumed["state"] == "metadata", resumed
        assert resumed["manual"] == "started"
        blocked = engine.update_settings({"vpn_enabled": True})
        assert blocked["vpn_enabled"] is True
        assert engine.snapshot()["stats"]["vpn"] == "blocked"
        engine.update_settings(
            {
                "vpn_enabled": True,
                "proxy_type": "socks5",
                "proxy_host": "127.0.0.1",
                "proxy_port": 9,
            }
        )
        tunneled = engine.snapshot()["stats"]
        assert tunneled["vpn"] == "on", tunneled
        assert tunneled["vpn_label"] == "127.0.0.1:9"
        assert tunneled["external_ip"] == ""
        engine.update_settings(
            {
                "vpn_enabled": False,
                "proxy_type": "none",
                "proxy_host": "",
                "proxy_port": 0,
            }
        )
        assert engine.snapshot()["stats"]["vpn"] == "off"
        engine.remove(added_magnet["id"], delete_files=False)
        engine.shutdown()
        restored = Engine()
        restored.settings["dht"] = False
        restored.settings["upnp"] = False
        restored.settings["natpmp"] = False
        restored.settings["lsd"] = False
        restored.settings["listen_port"] = 47622
        restored.start()
        try:
            deadline = time.time() + 3
            found = None
            while time.time() < deadline and found is None:
                for item in restored.snapshot()["torrents"]:
                    if item["id"] == added["id"]:
                        found = item
                        break
                time.sleep(0.1)
            assert found is not None, "torrent did not restore from resume data"
            assert found["label"] == "kept"
            restored.remove(added["id"], delete_files=False)
            assert all(item["id"] != added["id"] for item in restored.snapshot()["torrents"])
        finally:
            restored.shutdown()
    finally:
        if engine._running or engine._ses:
            engine.shutdown()
    print("selftest ok")


if __name__ == "__main__":
    main()
