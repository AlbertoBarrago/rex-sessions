"""launchd agent that runs `rex-sessions save --auto` periodically."""

from __future__ import annotations

import os
import plistlib
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .rex_client import resolve_rex_binary
from .store import store_dir

LABEL = "dev.albz.rex-sessions.autosave"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def build_plist(interval: int) -> dict:
    package_root = Path(__file__).resolve().parent.parent
    log = store_dir() / "autosave.log"
    return {
        "Label": LABEL,
        # sys.executable + -m keeps the agent on the interpreter that installed it.
        "ProgramArguments": [sys.executable, "-m", "rex_sessions", "save", "--auto"],
        "WorkingDirectory": str(package_root),
        "EnvironmentVariables": {
            "REX_BIN": resolve_rex_binary(),
            "PYTHONPATH": str(package_root),
            "REX_SESSIONS_DIR": str(store_dir()),
        },
        "StartInterval": interval,
        "RunAtLoad": True,
        "StandardOutPath": str(log),
        "StandardErrorPath": str(log),
    }


def _launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, check=False)


def install(interval: int) -> Path:
    store_dir().mkdir(parents=True, exist_ok=True)
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    domain = f"gui/{os.getuid()}"
    # bootout fails when the agent is not loaded yet; that is expected on first install.
    _launchctl("bootout", f"{domain}/{LABEL}")
    with PLIST_PATH.open("wb") as fh:
        plistlib.dump(build_plist(interval), fh)
    result = _launchctl("bootstrap", domain, str(PLIST_PATH))
    if result.returncode != 0:
        raise RuntimeError(f"launchctl bootstrap failed: {result.stderr.strip()}")
    return PLIST_PATH


@dataclass
class AgentStatus:
    installed: bool
    loaded: bool
    interval: int | None
    last_exit: str | None


def status() -> AgentStatus:
    """Read the agent state from its plist and `launchctl print`."""
    if not PLIST_PATH.exists():
        return AgentStatus(installed=False, loaded=False, interval=None, last_exit=None)
    with PLIST_PATH.open("rb") as fh:
        interval = plistlib.load(fh).get("StartInterval")
    result = _launchctl("print", f"gui/{os.getuid()}/{LABEL}")
    # Top-level job fields are indented with exactly one tab; nested sections use more.
    match = re.search(r"^\tlast exit code = (.+)$", result.stdout, re.MULTILINE)
    return AgentStatus(
        installed=True,
        loaded=result.returncode == 0,
        interval=interval,
        last_exit=match.group(1) if match else None,
    )


def uninstall() -> bool:
    """Unload and remove the agent; return False if it was not installed."""
    if not PLIST_PATH.exists():
        return False
    _launchctl("bootout", f"gui/{os.getuid()}/{LABEL}")
    PLIST_PATH.unlink()
    return True
