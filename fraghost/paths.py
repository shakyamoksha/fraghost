from __future__ import annotations

import os
from pathlib import Path


def data_dir() -> Path:
    override = os.environ.get("FRAGHOST_HOME")
    if override:
        path = Path(override)
    else:
        base = os.environ.get("APPDATA") or str(Path.home())
        path = Path(base) / "fraghost"
        previous = Path(base) / "Shakya"
        if not path.exists() and previous.is_dir():
            try:
                previous.rename(path)
            except OSError:
                path = previous
    path.mkdir(parents=True, exist_ok=True)
    return path
