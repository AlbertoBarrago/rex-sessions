"""Command-line entry point for `rex-sessions`."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import agent, snapshot, status, store
from .restore import restore
from .rex_client import RexClient, RexError

DEFAULT_MIN_UPTIME = 600
DEFAULT_INTERVAL = 120


def _server_uptime(client: RexClient) -> float:
    started = datetime.fromisoformat(client.server_status()["started_at"].replace("Z", "+00:00"))
    return (datetime.now(timezone.utc) - started).total_seconds()


def cmd_save(args: argparse.Namespace) -> int:
    client = RexClient()
    if args.auto:
        # A freshly (re)started server holds none of the old sessions yet.
        # Saving now would make latest.json point at that empty state and
        # make the pre-update snapshot the non-default one, so wait it out.
        uptime = _server_uptime(client)
        if uptime < args.min_uptime:
            print(f"skip: server up {uptime:.0f}s < {args.min_uptime}s")
            return 0
    snap = snapshot.capture(client)
    path = store.save(snap, keep=args.keep)
    print(f"saved {len(snap['sessions'])} session(s) to {path}" if path else "unchanged, nothing written")
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    source = Path(args.source) if args.source else store.store_dir() / store.LATEST_NAME
    if not source.is_file():
        print(f"error: snapshot not found: {source}", file=sys.stderr)
        return 1
    snap = store.load(source)
    report = restore(snap, RexClient(), only=set(args.session) if args.session else None, dry_run=args.dry_run)
    verb = "would create" if args.dry_run else "created"
    print(f"{verb}: {', '.join(report.created) or '-'}")
    print(f"already present: {', '.join(report.skipped_existing) or '-'}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    paths = store.history()
    if not paths:
        print(f"no snapshots in {store.store_dir()}")
        return 0
    for path in paths[-args.limit:]:
        snap = store.load(path)
        labels = ", ".join(s["label"] for s in snap["sessions"])
        print(f"{path.name}  {len(snap['sessions'])} session(s): {labels}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    source = Path(args.source) if args.source else store.store_dir() / store.LATEST_NAME
    if not source.is_file():
        print(f"error: snapshot not found: {source}", file=sys.stderr)
        return 1
    print(snapshot.format_snapshot(store.load(source), home=str(Path.home())))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    marks = {status.OK: "✓", status.WARN: "!", status.FAIL: "✗"}
    checks = status.evaluate(status.gather(args.min_uptime))
    width = max(len(c.title) for c in checks)
    for check in checks:
        print(f"{marks[check.level]} {check.title.ljust(width)}  {check.detail}")
    print(f"\nstore: {store.store_dir()}")
    return 1 if any(c.level == status.FAIL for c in checks) else 0


def cmd_autosave_on(args: argparse.Namespace) -> int:
    path = agent.install(args.interval)
    print(f"autosave on: every {args.interval}s ({path})")
    return 0


def cmd_autosave_off(_: argparse.Namespace) -> int:
    print("autosave off" if agent.uninstall() else "autosave was not on")
    return 0


def cmd_uninstall(args: argparse.Namespace) -> int:
    target = Path(args.bin_dir).expanduser() / "rex-sessions"
    launcher = Path(__file__).resolve().parent.parent / "bin" / "rex-sessions"
    if target.is_symlink():
        if target.resolve() != launcher.resolve():
            print(f"error: refusing to remove unrelated symlink: {target}", file=sys.stderr)
            return 1
    elif target.exists():
        print(f"error: refusing to remove non-symlink: {target}", file=sys.stderr)
        return 1

    cmd_autosave_off(args)
    if target.is_symlink():
        target.unlink()
        print(f"removed {target}")
    else:
        print(f"CLI link already absent: {target}")
    print(f"snapshots kept in {store.store_dir()}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rex-sessions", description="Save and restore Rex sessions across server restarts.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("save", help="snapshot all sessions")
    p.add_argument("--auto", action="store_true", help="autosave mode: skip right after a server restart")
    p.add_argument("--min-uptime", type=int, default=DEFAULT_MIN_UPTIME, help="seconds (with --auto)")
    p.add_argument("--keep", type=int, default=store.DEFAULT_KEEP, help="snapshots to keep")
    p.set_defaults(func=cmd_save)

    p = sub.add_parser("restore", help="recreate sessions missing from the server")
    p.add_argument("--from", dest="source", help="snapshot file (default: latest)")
    p.add_argument("--session", action="append", help="restore only this label (repeatable)")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_restore)

    p = sub.add_parser("list", help="show saved snapshots")
    p.add_argument("--limit", type=int, default=10)
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("show", help="print a snapshot as a session > tab > pane tree")
    p.add_argument("source", nargs="?", help="snapshot file (default: latest)")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("status", help="check server, autosave agent and snapshots")
    p.add_argument("--min-uptime", type=int, default=DEFAULT_MIN_UPTIME, help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("autosave", help="turn the launchd autosave agent on or off")
    autosave = p.add_subparsers(dest="state", required=True)
    on = autosave.add_parser("on", help="install and start the agent")
    on.add_argument("--interval", type=int, default=DEFAULT_INTERVAL, help="seconds between saves")
    on.set_defaults(func=cmd_autosave_on)
    autosave.add_parser("off", help="stop and remove the agent").set_defaults(func=cmd_autosave_off)

    p = sub.add_parser("uninstall", help="remove the autosave agent and CLI symlink; keep snapshots")
    p.add_argument("--bin-dir", default=str(Path.home() / ".local" / "bin"), help="installation directory (default: ~/.local/bin)")
    p.set_defaults(func=cmd_uninstall)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (RexError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
