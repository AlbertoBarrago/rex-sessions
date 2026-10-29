"""Thin wrapper around the `rex` CLI.

The CLI is used instead of the server socket on purpose: it is the interface
Rex keeps stable across server updates, which is exactly when this tool runs.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Any

DEFAULT_TIMEOUT_SECONDS = 15.0


class RexError(RuntimeError):
    """Raised when a `rex` invocation fails or returns unexpected output."""


def resolve_rex_binary() -> str:
    """Return the `rex` binary path, honoring the REX_BIN override.

    launchd jobs run with a minimal PATH, so the agent plist pins REX_BIN.
    """
    override = os.environ.get("REX_BIN")
    if override:
        return override
    found = shutil.which("rex")
    if found is None:
        raise RexError("rex binary not found on PATH; set REX_BIN")
    return found


class RexClient:
    def __init__(self, binary: str | None = None, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self.binary = binary or resolve_rex_binary()
        self.timeout = timeout

    def _run(self, args: list[str], stdin: str | None = None) -> Any:
        # --autostart=false: saving or restoring must never spawn a server as a side effect.
        cmd = [self.binary, "--autostart=false", *args]
        try:
            proc = subprocess.run(
                cmd,
                input=stdin,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RexError(f"rex timed out: {' '.join(args)}") from exc
        if proc.returncode != 0:
            raise RexError(f"rex {' '.join(args[:3])} failed: {proc.stderr.strip()}")
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise RexError(f"rex returned invalid JSON for {' '.join(args[:3])}") from exc

    def api(self, method: str, args: dict[str, Any] | None = None) -> Any:
        """Call a server API method, passing arguments on stdin to avoid quoting issues."""
        return self._run(["api", "call", method, "-"], stdin=json.dumps(args or {}))

    def block_call(self, session_id: str, block_id: str, method: str, args: dict[str, Any] | None = None) -> Any:
        return self._run(
            ["block", "call", "-s", session_id, "-b", block_id, method, "-"],
            stdin=json.dumps(args or {}),
        )

    def server_status(self) -> dict[str, Any]:
        return self.api("server.status")

    def list_sessions(self) -> list[dict[str, Any]]:
        return self.api("session.list").get("sessions") or []

    def view(self, session_id: str) -> dict[str, Any]:
        return self.api("session.view", {"session_id": session_id})

    def block_process(self, session_id: str, block_id: str) -> dict[str, Any]:
        return self.block_call(session_id, block_id, "process")

    def create_session(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.api("session.create", payload)

    def focus_window(self, session_id: str, window_id: str) -> None:
        self.api("session.focus_window", {"session_id": session_id, "window_id": window_id})
