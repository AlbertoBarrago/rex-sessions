import unittest
from datetime import datetime, timezone

from rex_sessions.agent import AgentStatus
from rex_sessions.snapshot import format_snapshot
from rex_sessions.status import FAIL, OK, WARN, StatusInputs, evaluate

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)
AGENT_ON = AgentStatus(installed=True, loaded=True, interval=120, last_exit="0")


def inputs(**overrides) -> StatusInputs:
    base = dict(
        rex_binary="/bin/rex",
        server={"started_at": "2026-10-29T10:00:00Z", "session_count": 2},
        server_error=None,
        live_labels=["a", "b"],
        agent=AGENT_ON,
        log_tail=None,
        latest={"saved_at": "2026-10-29T11:58:00+00:00", "sessions": [{"label": "a"}, {"label": "b"}]},
        now=NOW,
        min_uptime=600,
    )
    base.update(overrides)
    return StatusInputs(**base)


def levels(checks) -> dict:
    return {c.title: c.level for c in checks}


class EvaluateTest(unittest.TestCase):
    def test_healthy(self):
        checks = evaluate(inputs())
        self.assertEqual(
            levels(checks), {"server": OK, "autosave": OK, "snapshot": OK, "restore": OK}
        )

    def test_missing_rex_binary_fails(self):
        self.assertEqual(levels(evaluate(inputs(rex_binary=None, server=None)))["rex"], FAIL)

    def test_unreachable_server_fails(self):
        checks = evaluate(inputs(server=None, server_error="no socket", live_labels=None))
        self.assertEqual(levels(checks)["server"], FAIL)
        self.assertNotIn("restore", levels(checks))

    def test_recent_restart_warns_and_lists_missing_sessions(self):
        checks = evaluate(
            inputs(server={"started_at": "2026-10-29T11:58:00Z"}, live_labels=["a"])
        )
        by_title = {c.title: c for c in checks}
        self.assertEqual(by_title["autosave guard"].level, WARN)
        self.assertIn("8m", by_title["autosave guard"].detail)
        self.assertEqual(by_title["restore"].level, WARN)
        self.assertIn("b", by_title["restore"].detail)

    def test_agent_states(self):
        off = AgentStatus(installed=False, loaded=False, interval=None, last_exit=None)
        self.assertEqual(levels(evaluate(inputs(agent=off)))["autosave"], WARN)
        unloaded = AgentStatus(installed=True, loaded=False, interval=120, last_exit=None)
        self.assertEqual(levels(evaluate(inputs(agent=unloaded)))["autosave"], FAIL)
        crashed = AgentStatus(installed=True, loaded=True, interval=120, last_exit="1")
        check = next(c for c in evaluate(inputs(agent=crashed, log_tail="error: boom")) if c.title == "autosave")
        self.assertEqual(check.level, WARN)
        self.assertIn("boom", check.detail)

    def test_no_snapshot_yet(self):
        checks = evaluate(inputs(latest=None))
        self.assertEqual(levels(checks)["snapshot"], WARN)
        self.assertNotIn("restore", levels(checks))


class FormatSnapshotTest(unittest.TestCase):
    def test_tree(self):
        snap = {
            "saved_at": "2026-10-29T11:58:00+00:00",
            "sessions": [
                {
                    "label": "work",
                    "active_window_index": 1,
                    "windows": [
                        {"label": None, "layout": {"block": {"flavor": "f", "label": "shell", "cwd": "/home/u/a", "program": None}}},
                        {
                            "label": "dev",
                            "layout": {
                                "split": {
                                    "direction": "horizontal",
                                    "ratio": 0.5,
                                    "before": {"block": {"flavor": "f", "label": "shell", "cwd": "/srv", "program": "nvim"}},
                                    "after": {"block": {"flavor": "f", "label": "logs", "cwd": None, "program": None}},
                                }
                            },
                        },
                    ],
                }
            ],
        }
        out = format_snapshot(snap, home="/home/u")
        self.assertIn("  1. tab\n       ~/a", out)
        self.assertIn("  2. dev *\n       /srv [nvim]\n       ? (logs)", out)


if __name__ == "__main__":
    unittest.main()
