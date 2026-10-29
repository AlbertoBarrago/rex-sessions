"""On-disk snapshot history: timestamped files plus a `latest.json` pointer copy."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

DEFAULT_DIR = Path.home() / "Library" / "Application Support" / "rex-sessions"
DEFAULT_KEEP = 50
LATEST_NAME = "latest.json"
_PREFIX = "snapshot-"


def store_dir() -> Path:
    return Path(os.environ.get("REX_SESSIONS_DIR", DEFAULT_DIR))


def _atomic_write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def load(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def history(directory: Path | None = None) -> list[Path]:
    """Timestamped snapshots, oldest first (names sort chronologically)."""
    directory = directory or store_dir()
    if not directory.is_dir():
        return []
    return sorted(directory.glob(f"{_PREFIX}*.json"))


def save(snapshot: dict[str, Any], directory: Path | None = None, keep: int = DEFAULT_KEEP) -> Path | None:
    """Persist a snapshot; return its path, or None when nothing changed.

    Unchanged state is not written, so a frequent autosave does not flood the
    history with duplicates and older distinct states survive rotation longer.
    """
    directory = directory or store_dir()
    latest = directory / LATEST_NAME
    if latest.is_file() and load(latest).get("sessions") == snapshot["sessions"]:
        return None
    stamp = snapshot["saved_at"].replace(":", "").replace("-", "").replace("+0000", "Z")
    path = directory / f"{_PREFIX}{stamp}.json"
    _atomic_write(path, snapshot)
    _atomic_write(latest, snapshot)
    for old in history(directory)[:-keep]:
        old.unlink()
    return path
