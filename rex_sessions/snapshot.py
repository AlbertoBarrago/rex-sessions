"""Turn live Rex sessions into a portable, ID-free snapshot.

Snapshot format (version 1):

    {
      "version": 1,
      "saved_at": "<ISO 8601 UTC>",
      "sessions": [
        {
          "label": "work",
          "active_window_index": 0,
          "windows": [
            {"label": "editor" | null, "layout": <node>}
          ]
        }
      ]
    }

A layout node is either
    {"block": {"flavor": str, "label": str | null, "cwd": str | null, "program": str | null}}
or
    {"split": {"direction": "horizontal" | "vertical", "ratio": float, "before": <node>, "after": <node>}}

Server IDs are dropped on purpose: they do not survive a server restart.
"""

from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from typing import Any, Callable

from .rex_client import RexClient, RexError

SNAPSHOT_VERSION = 1

# Labels the server generates from IDs ("Window 89abcdef"); restoring them
# verbatim would pin a stale ID, so they are dropped and regenerated.
_GENERATED_WINDOW_LABEL = re.compile(r"^Window [0-9a-f]{8}$")

_SHELL_NAMES = {"zsh", "bash", "fish", "sh", "nu", "login"}

BlockInfo = dict[str, Any]
BlockInfoFn = Callable[[str], BlockInfo]


def block_info_from_process(process: dict[str, Any]) -> BlockInfo:
    """Extract cwd and foreground program from a terminal `process` result.

    The foreground process cwd is preferred; the nearest shell ancestor is the
    fallback for when the foreground process hides its cwd (e.g. setuid).
    """
    foreground = process.get("foreground") or {}
    cwd = foreground.get("cwd")
    if not cwd:
        cwd = next((a["cwd"] for a in process.get("ancestors") or [] if a.get("cwd")), None)
    name = foreground.get("argv0") or foreground.get("name")
    program = None if not name or name.lstrip("-") in _SHELL_NAMES else name
    return {"cwd": cwd, "program": program}


def _convert_node(node: dict[str, Any], blocks: dict[str, dict[str, Any]], info: BlockInfoFn) -> dict[str, Any]:
    if "split" in node:
        split = node["split"]
        return {
            "split": {
                "direction": split["direction"],
                "ratio": split.get("ratio", 0.5),
                "before": _convert_node(split["before"], blocks, info),
                "after": _convert_node(split["after"], blocks, info),
            }
        }
    block_id = node["block_id"]
    block = blocks.get(block_id, {})
    return {
        "block": {
            "flavor": block.get("flavor", "com.superlogical.terminal.shell"),
            "label": block.get("label"),
            **info(block_id),
        }
    }


def build_session_snapshot(view: dict[str, Any], info: BlockInfoFn) -> dict[str, Any] | None:
    """Convert a `session.view` result into a session snapshot.

    Only the tiled layer of each window is kept; floating layers are reported
    on stderr and skipped. Returns None for sessions with nothing to restore.
    """
    windows: list[dict[str, Any]] = []
    active_index = 0
    for window in view.get("windows") or []:
        tiled = next((l for l in window.get("layers") or [] if l.get("kind") == "tiled"), None)
        skipped = [l for l in window.get("layers") or [] if l.get("kind") != "tiled"]
        if skipped:
            print(
                f"warning: {view.get('label')!r}: skipping {len(skipped)} floating layer(s) in {window.get('label')!r}",
                file=sys.stderr,
            )
        if tiled is None or not tiled.get("layout"):
            continue
        blocks = {b["block_id"]: b for b in tiled.get("blocks") or []}
        label = window.get("label")
        if window.get("window_id") == view.get("active_window_id"):
            active_index = len(windows)
        windows.append(
            {
                "label": None if not label or _GENERATED_WINDOW_LABEL.match(label) else label,
                "layout": _convert_node(tiled["layout"], blocks, info),
            }
        )
    if not windows:
        return None
    return {"label": view.get("label"), "active_window_index": active_index, "windows": windows}


def _pane_lines(node: dict[str, Any], home: str) -> list[str]:
    """Flatten a layout tree into one line per pane, in layout order."""
    if "split" in node:
        split = node["split"]
        return _pane_lines(split["before"], home) + _pane_lines(split["after"], home)
    block = node["block"]
    cwd = block.get("cwd") or "?"
    if home and (cwd == home or cwd.startswith(home + "/")):
        cwd = "~" + cwd[len(home):]
    parts = [cwd]
    if block.get("program"):
        parts.append(f"[{block['program']}]")
    if block.get("label") and block["label"] != "shell":
        parts.append(f"({block['label']})")
    return [" ".join(parts)]


def format_snapshot(snap: dict[str, Any], home: str = "") -> str:
    """Render a snapshot as an indented session > tab > pane tree."""
    lines = [f"saved {snap['saved_at']}"]
    for session in snap["sessions"]:
        lines.append(f"\n{session['label']}")
        for index, window in enumerate(session["windows"]):
            active = " *" if index == session.get("active_window_index") else ""
            lines.append(f"  {index + 1}. {window.get('label') or 'tab'}{active}")
            lines.extend(f"       {pane}" for pane in _pane_lines(window["layout"], home))
    return "\n".join(lines)


def _live_block_info(client: RexClient, session_id: str) -> BlockInfoFn:
    def info(block_id: str) -> BlockInfo:
        try:
            return block_info_from_process(client.block_process(session_id, block_id))
        except RexError as exc:
            # Non-terminal blocks have no `process` method; keep the layout, lose the cwd.
            print(f"warning: no process info for {block_id}: {exc}", file=sys.stderr)
            return {"cwd": None, "program": None}

    return info


def capture(client: RexClient) -> dict[str, Any]:
    """Snapshot every session on the connected server, in server order."""
    sessions = []
    for entry in client.list_sessions():
        session_id = entry["session_id"]
        snap = build_session_snapshot(client.view(session_id), _live_block_info(client, session_id))
        if snap is not None:
            sessions.append(snap)
    return {
        "version": SNAPSHOT_VERSION,
        "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sessions": sessions,
    }
