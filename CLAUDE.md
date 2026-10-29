# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`rex-sessions` saves and restores Rex (terminal multiplexer) sessions so a server update or restart doesn't lose them. Python standard library only, no runtime dependencies (`requires-python >= 3.11`). VCS is **jj**, one bookmark per feature.

## Commands

```sh
python3 -m unittest discover -s tests -v                                   # all tests
python3 -m unittest tests.test_roundtrip.RestoreTest.test_only_filter       # single test
python3 -m rex_sessions save|restore|list|install-agent|uninstall-agent     # run without installing
REX_SESSIONS_DIR=/some/tmp python3 -m rex_sessions save                     # isolate from the real store
```

Use `REX_SESSIONS_DIR` for any manual testing so the real history in `~/Library/Application Support/rex-sessions/` is not touched.

## Architecture

Data flows `rex CLI -> snapshot -> store (JSON on disk) -> restore -> rex CLI`:

- `rex_client.py`: every server interaction goes through the `rex` CLI (`rex api call METHOD -` / `rex block call ... -`, JSON args on stdin), always with `--autostart=false`. The socket is never used directly: the CLI is the interface that stays stable across Rex updates. `REX_BIN` overrides the binary (launchd has a minimal PATH).
- `snapshot.py`: converts `session.view` output into an ID-free snapshot (format documented in the module docstring). Server IDs and generated window labels (`Window xxxxxxxx`) are dropped because they don't survive a restart. Per-block cwd/program come from the terminal block method `process`. Only `kind == "tiled"` layers are kept.
- `restore.py`: maps each snapshot session to one `session.create` payload (`initial_windows` with the full split tree, shell `options.cwd`), so a session is created atomically. Sessions are matched **by label** to make restore idempotent. `session.create` activates the last window, so the saved active window is re-focused afterwards. Saved `program` is never re-run by design.
- `store.py`: timestamped `snapshot-*.json` history plus `latest.json`; skips writing when `sessions` is unchanged; rotates to `--keep`.
- `agent.py`: launchd agent (`dev.albz.rex-sessions.autosave`) running `save --auto`; pins `sys.executable`, `PYTHONPATH`, `REX_BIN`, `REX_SESSIONS_DIR` into the plist.
- `cli.py`: `save --auto` skips while server uptime (`server.status.started_at`) is below `--min-uptime` (600s). This guard is what keeps the pre-update snapshot as `latest.json`; don't remove it.

## Rex API notes

- Split `direction` is `horizontal` (before = left) or `vertical` (before = top), `ratio` is the share of `before`.
- Discover methods with `rex api list`, `rex api describe <method> --json`, `rex block types --json`.
- `tests/fixtures/view_split.json` is a real `session.view` result; regenerate from a live probe session if the API shape changes.
