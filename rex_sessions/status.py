"""Health checks behind `rex-sessions status`.

`evaluate` is pure (inputs in, checks out) so it is unit-testable; `gather`
does the I/O against Rex, launchd and the store.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from . import agent, store
from .rex_client import RexClient, RexError, resolve_rex_binary

OK, WARN, FAIL = "ok", "warn", "fail"


@dataclass
class Check:
    level: str
    title: str
    detail: str


@dataclass
class StatusInputs:
    rex_binary: str | None
    server: dict[str, Any] | None
    server_error: str | None
    live_labels: list[str] | None
    agent: agent.AgentStatus
    log_tail: str | None
    latest: dict[str, Any] | None
    now: datetime
    min_uptime: int


def _uptime(server: dict[str, Any], now: datetime) -> float:
    started = datetime.fromisoformat(server["started_at"].replace("Z", "+00:00"))
    return (now - started).total_seconds()


def _human(seconds: float) -> str:
    seconds = int(seconds)
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{seconds // size}{unit}"
    return f"{seconds}s"


def evaluate(inp: StatusInputs) -> list[Check]:
    checks: list[Check] = []

    if inp.rex_binary is None:
        checks.append(Check(FAIL, "rex", "binary not found on PATH (set REX_BIN)"))
    elif inp.server is None:
        checks.append(Check(FAIL, "server", f"not reachable: {inp.server_error}"))
    else:
        uptime = _uptime(inp.server, inp.now)
        checks.append(
            Check(OK, "server", f"up {_human(uptime)}, {inp.server.get('session_count', '?')} session(s)")
        )
        if uptime < inp.min_uptime:
            checks.append(
                Check(
                    WARN,
                    "autosave guard",
                    f"server restarted recently, autosave paused for {_human(inp.min_uptime - uptime)} more",
                )
            )

    a = inp.agent
    if not a.installed:
        checks.append(Check(WARN, "autosave", "off (enable with: rex-sessions autosave on)"))
    elif not a.loaded:
        checks.append(Check(FAIL, "autosave", f"plist present but not loaded: {agent.PLIST_PATH}"))
    else:
        detail = f"every {a.interval}s, last exit {a.last_exit or '?'}"
        failed = a.last_exit not in (None, "0", "(never exited)")
        if failed and inp.log_tail:
            detail += f"; log: {inp.log_tail}"
        checks.append(Check(WARN if failed else OK, "autosave", detail))

    if inp.latest is None:
        checks.append(Check(WARN, "snapshot", "none yet (run: rex-sessions save)"))
        return checks
    saved_at = datetime.fromisoformat(inp.latest["saved_at"])
    saved = [s["label"] for s in inp.latest["sessions"]]
    checks.append(
        Check(OK, "snapshot", f"{len(saved)} session(s), last change {_human((inp.now - saved_at).total_seconds())} ago")
    )

    if inp.live_labels is not None:
        missing = [label for label in saved if label not in inp.live_labels]
        if missing:
            checks.append(
                Check(WARN, "restore", f"missing on server: {', '.join(missing)} (run: rex-sessions restore)")
            )
        else:
            checks.append(Check(OK, "restore", "every saved session is on the server"))
    return checks


def _log_tail() -> str | None:
    log = store.store_dir() / "autosave.log"
    if not log.is_file():
        return None
    lines = log.read_text(encoding="utf-8", errors="replace").strip().splitlines()
    return lines[-1] if lines else None


def gather(min_uptime: int) -> StatusInputs:
    try:
        binary: str | None = resolve_rex_binary()
    except RexError:
        binary = None
    server = server_error = live_labels = None
    if binary is not None:
        client = RexClient(binary)
        try:
            server = client.server_status()
            live_labels = [s.get("label") for s in client.list_sessions()]
        except RexError as exc:
            server_error = str(exc)
    latest_path = store.store_dir() / store.LATEST_NAME
    return StatusInputs(
        rex_binary=binary,
        server=server,
        server_error=server_error,
        live_labels=live_labels,
        agent=agent.status(),
        log_tail=_log_tail(),
        latest=store.load(latest_path) if latest_path.is_file() else None,
        now=datetime.now(timezone.utc),
        min_uptime=min_uptime,
    )
