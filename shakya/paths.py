from __future__ import annotations

import os
from pathlib import Path


def data_dir() -> Path:
    override = os.environ.get("SHAKYA_HOME")
    if override:
        path = Path(override)
    else:
        base = os.environ.get("APPDATA") or str(Path.home())
        path = Path(base) / "Shakya"
    path.mkdir(parents=True, exist_ok=True)
    return path
