import json
import tempfile
import unittest
from pathlib import Path

from rex_sessions import store
from rex_sessions.restore import RestoreReport, build_create_payload, restore
from rex_sessions.snapshot import block_info_from_process, build_session_snapshot

FIXTURES = Path(__file__).parent / "fixtures"


def load_view() -> dict:
    return json.loads((FIXTURES / "view_split.json").read_text())


def fake_info(block_id: str) -> dict:
    return {"cwd": f"/work/{block_id[-4:]}", "program": None}


class SnapshotTest(unittest.TestCase):
    def test_split_tree_is_preserved_without_ids(self):
        snap = build_session_snapshot(load_view(), fake_info)
        self.assertEqual(snap["label"], "rexsess-probe")
        self.assertEqual(snap["active_window_index"], 0)
        layout = snap["windows"][0]["layout"]["split"]
        self.assertEqual((layout["direction"], layout["ratio"]), ("horizontal", 0.3))
        self.assertEqual(layout["before"]["block"]["cwd"], "/work/4fbd")
        inner = layout["after"]["split"]
        self.assertEqual(inner["direction"], "vertical")
        self.assertEqual(inner["after"]["block"]["label"], "logs")
        self.assertNotIn("block:", json.dumps(snap))

    def test_generated_window_label_is_dropped(self):
        view = load_view()
        view["windows"][0]["label"] = "Window 6e3a4593"
        snap = build_session_snapshot(view, fake_info)
        self.assertIsNone(snap["windows"][0]["label"])

    def test_session_without_windows_is_skipped(self):
        self.assertIsNone(build_session_snapshot({"label": "x", "windows": []}, fake_info))

    def test_process_info_ignores_shells_and_keeps_programs(self):
        shell = {"foreground": {"name": "zsh", "argv0": "-zsh", "cwd": "/a"}}
        self.assertEqual(block_info_from_process(shell), {"cwd": "/a", "program": None})
        editor = {"foreground": {"name": "nvim", "argv0": "nvim", "cwd": "/b"}}
        self.assertEqual(block_info_from_process(editor), {"cwd": "/b", "program": "nvim"})

    def test_process_info_falls_back_to_ancestor_cwd(self):
        proc = {"foreground": {"name": "sudo"}, "ancestors": [{"name": "zsh", "cwd": "/c"}]}
        self.assertEqual(block_info_from_process(proc)["cwd"], "/c")


class PayloadTest(unittest.TestCase):
    def test_payload_matches_session_create_shape(self):
        snap = build_session_snapshot(load_view(), fake_info)
        payload = build_create_payload(snap, dir_exists=lambda _: True)
        self.assertEqual(payload["label"], "rexsess-probe")
        window = payload["initial_windows"][0]
        self.assertEqual(window["window_label"], "w1")
        before = window["layout"]["split"]["before"]["block"]
        self.assertEqual(before["options"], {"cwd": "/work/4fbd"})
        self.assertNotIn("program", json.dumps(payload))

    def test_missing_cwd_is_dropped(self):
        snap = build_session_snapshot(load_view(), fake_info)
        payload = build_create_payload(snap, dir_exists=lambda _: False)
        self.assertNotIn("options", json.dumps(payload))


class FakeClient:
    def __init__(self, existing):
        self.existing = existing
        self.created = []
        self.focused = []

    def list_sessions(self):
        return [{"label": label} for label in self.existing]

    def create_session(self, payload):
        self.created.append(payload)
        return {
            "session_id": "session:new",
            "initial_windows": [{"window_id": f"window:{i}"} for i, _ in enumerate(payload["initial_windows"])],
        }

    def focus_window(self, session_id, window_id):
        self.focused.append(window_id)


class RestoreTest(unittest.TestCase):
    def snapshot(self):
        session = build_session_snapshot(load_view(), lambda _: {"cwd": None, "program": None})
        other = dict(session, label="other")
        return {"version": 1, "saved_at": "x", "sessions": [session, other]}

    def test_existing_labels_are_skipped(self):
        client = FakeClient(existing=["rexsess-probe"])
        report = restore(self.snapshot(), client)
        self.assertEqual(report, RestoreReport(created=["other"], skipped_existing=["rexsess-probe"]))
        self.assertEqual(client.focused, ["window:0"])

    def test_dry_run_creates_nothing(self):
        client = FakeClient(existing=[])
        report = restore(self.snapshot(), client, dry_run=True)
        self.assertEqual(report.created, ["rexsess-probe", "other"])
        self.assertEqual(client.created, [])

    def test_only_filter(self):
        client = FakeClient(existing=[])
        restore(self.snapshot(), client, only={"other"})
        self.assertEqual([p["label"] for p in client.created], ["other"])


class StoreTest(unittest.TestCase):
    def test_dedupe_and_rotation(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            base = {"version": 1, "sessions": [{"label": "a"}]}
            self.assertIsNotNone(store.save({**base, "saved_at": "2026-01-01T00:00:00+00:00"}, directory, keep=2))
            self.assertIsNone(store.save({**base, "saved_at": "2026-01-01T00:00:01+00:00"}, directory, keep=2))
            for second, label in enumerate(["b", "c", "d"], start=2):
                snap = {"version": 1, "saved_at": f"2026-01-01T00:00:0{second}+00:00", "sessions": [{"label": label}]}
                store.save(snap, directory, keep=2)
            names = [p.name for p in store.history(directory)]
            self.assertEqual(names, ["snapshot-20260101T000003Z.json", "snapshot-20260101T000004Z.json"])
            self.assertEqual(store.load(directory / store.LATEST_NAME)["sessions"], [{"label": "d"}])


if __name__ == "__main__":
    unittest.main()
