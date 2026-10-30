import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rex_sessions import cli


class UninstallTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.bin_dir = Path(self.directory.name)
        self.target = self.bin_dir / "rex-sessions"
        self.launcher = Path(cli.__file__).resolve().parent.parent / "bin" / "rex-sessions"

    def run_uninstall(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return cli.main(["uninstall", "--bin-dir", str(self.bin_dir)])

    @patch("rex_sessions.cli.agent.uninstall", return_value=True)
    def test_removes_own_link_and_agent_but_keeps_snapshots(self, uninstall):
        self.target.symlink_to(self.launcher)
        snapshots = self.bin_dir / "snapshots"
        snapshots.mkdir()
        snapshot = snapshots / "latest.json"
        snapshot.write_text('{"sessions": []}')
        with patch("rex_sessions.cli.store.store_dir", return_value=snapshots):
            self.assertEqual(self.run_uninstall(), 0)
        uninstall.assert_called_once_with()
        self.assertFalse(self.target.is_symlink())
        self.assertTrue(self.launcher.is_file())
        self.assertEqual(snapshot.read_text(), '{"sessions": []}')

    @patch("rex_sessions.cli.agent.uninstall", return_value=False)
    def test_repeated_uninstall_succeeds(self, uninstall):
        self.target.symlink_to(self.launcher)
        self.assertEqual(self.run_uninstall(), 0)
        self.assertEqual(self.run_uninstall(), 0)
        self.assertEqual(uninstall.call_count, 2)

    @patch("rex_sessions.cli.agent.uninstall")
    def test_preserves_regular_file(self, uninstall):
        self.target.write_text("other installation")
        self.assertEqual(self.run_uninstall(), 1)
        self.assertEqual(self.target.read_text(), "other installation")
        uninstall.assert_not_called()

    @patch("rex_sessions.cli.agent.uninstall")
    def test_preserves_unrelated_and_broken_links(self, uninstall):
        other = self.bin_dir / "other"
        other.write_text("other installation")
        self.target.symlink_to(other)
        self.assertEqual(self.run_uninstall(), 1)
        other.unlink()
        self.assertEqual(self.run_uninstall(), 1)
        self.assertTrue(self.target.is_symlink())
        uninstall.assert_not_called()

    @patch("rex_sessions.cli.agent.uninstall", side_effect=PermissionError("access denied"))
    def test_agent_failure_keeps_cli_available(self, uninstall):
        self.target.symlink_to(self.launcher)
        self.assertEqual(self.run_uninstall(), 1)
        self.assertTrue(self.target.is_symlink())


if __name__ == "__main__":
    unittest.main()
