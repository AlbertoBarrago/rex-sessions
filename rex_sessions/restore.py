"""Recreate sessions from a snapshot with one atomic `session.create` per session."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from typing import Any, Callable

from .rex_client import RexClient

SHELL_FLAVOR = "com.superlogical.terminal.shell"


@dataclass
class RestoreReport:
    created: list[str] = field(default_factory=list)
    skipped_existing: list[str] = field(default_factory=list)


def _convert_node(node: dict[str, Any], dir_exists: Callable[[str], bool]) -> dict[str, Any]:
    if "split" in node:
        split = node["split"]
        return {
            "split": {
                "direction": split["direction"],
                "ratio": split["ratio"],
                "before": _convert_node(split["before"], dir_exists),
                "after": _convert_node(split["after"], dir_exists),
            }
        }
    block = node["block"]
    out: dict[str, Any] = {"flavor": block["flavor"]}
    if block.get("label"):
        out["label"] = block["label"]
    # Panes restart as plain shells in their old cwd: re-running the saved
    # program is deliberately not done, it may have side effects.
    cwd = block.get("cwd")
    if block["flavor"] == SHELL_FLAVOR and cwd:
        if dir_exists(cwd):
            out["options"] = {"cwd": cwd}
        else:
            print(f"warning: cwd {cwd!r} no longer exists, using home", file=sys.stderr)
    return {"block": out}


def build_create_payload(
    session: dict[str, Any], dir_exists: Callable[[str], bool] = os.path.isdir
) -> dict[str, Any]:
    """Map a session snapshot to `session.create` arguments, preserving window order."""
    windows = []
    for window in session["windows"]:
        entry: dict[str, Any] = {"layout": _convert_node(window["layout"], dir_exists)}
        if window.get("label"):
            entry["window_label"] = window["label"]
        windows.append(entry)
    return {"label": session["label"], "initial_windows": windows}


def restore(
    snapshot: dict[str, Any],
    client: RexClient,
    only: set[str] | None = None,
    dry_run: bool = False,
) -> RestoreReport:
    """Create every snapshot session whose label is not already on the server.

    Matching by label makes restore idempotent: running it twice, or after a
    partial restore, never duplicates sessions.
    """
    report = RestoreReport()
    existing = {s.get("label") for s in client.list_sessions()}
    for session in snapshot["sessions"]:
        label = session["label"]
        if only is not None and label not in only:
            continue
        if label in existing:
            report.skipped_existing.append(label)
            continue
        payload = build_create_payload(session)
        if dry_run:
            report.created.append(label)
            continue
        result = client.create_session(payload)
        # session.create activates the last window; restore the saved one instead.
        created_windows = result.get("initial_windows") or []
        index = session.get("active_window_index", 0)
        if 0 <= index < len(created_windows):
            client.focus_window(result["session_id"], created_windows[index]["window_id"])
        report.created.append(label)
    return report
