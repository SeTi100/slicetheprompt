"""Tests für slicetheprompt/sync.py (rclone + Google Drive Build-Hook)."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from slicetheprompt import sync as sync_mod


class SyncTests(unittest.TestCase):
    def setUp(self) -> None:
        sync_mod._REMOTE_CACHE.clear()

    def test_disabled_under_pytest_env(self) -> None:
        with patch.dict(os.environ, {"PYTEST_CURRENT_TEST": "tests/test_sync.py::x"}):
            res = sync_mod.sync_project_async("dummy")
        self.assertFalse(res["triggered"])
        self.assertEqual(res["reason"], "disabled_by_env")

    def test_load_sync_config_defaults_and_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg_file = Path(tmp) / "sync.yaml"
            cfg_file.write_text(
                "enabled: true\nremote: 'mydrive:'\nremote_path: '/CAD/STP/'\nexclude:\n  - '*.gcode'\n",
                encoding="utf-8",
            )
            with patch.object(sync_mod, "SYNC_YAML", cfg_file):
                cfg = sync_mod.load_sync_config()
        self.assertTrue(cfg["enabled"])
        self.assertEqual(cfg["remote"], "mydrive")
        self.assertEqual(cfg["remote_path"], "CAD/STP")
        self.assertEqual(cfg["exclude"], ["*.gcode"])

    def test_async_triggers_rclone_copy_non_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "2026-11-15-testteil"
            proj.mkdir()
            state_dir = Path(tmp) / ".slicetheprompt"
            cfg_file = Path(tmp) / "sync.yaml"
            cfg_file.write_text("exclude:\n  - 'history/**'\n", encoding="utf-8")

            fake_proc = MagicMock(pid=4242)
            env_clean = {k: v for k, v in os.environ.items() if k not in ("PYTEST_CURRENT_TEST", "SLICETHEPROMPT_NO_SYNC")}

            with patch.dict(os.environ, env_clean, clear=True), \
                 patch.object(sync_mod, "SYNC_YAML", cfg_file), \
                 patch.object(sync_mod, "STATE_DIR", state_dir), \
                 patch.object(sync_mod, "SYNC_LOG", state_dir / "sync.log"), \
                 patch("shutil.which", return_value="/usr/bin/rclone"), \
                 patch.object(sync_mod, "_remote_exists", return_value=True), \
                 patch("subprocess.Popen", return_value=fake_proc) as popen_mock:
                res = sync_mod.sync_project_async(proj)

            self.assertTrue(res["triggered"])
            self.assertEqual(res["pid"], 4242)
            self.assertEqual(res["target"], "gdrive:SliceThePrompt/projects/2026-11-15-testteil")
            cmd = popen_mock.call_args[0][0]
            self.assertEqual(
                cmd,
                [
                    "/usr/bin/rclone",
                    "copy",
                    str(proj),
                    "gdrive:SliceThePrompt/projects/2026-11-15-testteil",
                    "--exclude",
                    "history/**",
                ],
            )
            self.assertTrue((state_dir / "sync.log").exists())

    def test_async_skips_when_remote_missing(self) -> None:
        env_clean = {k: v for k, v in os.environ.items() if k not in ("PYTEST_CURRENT_TEST", "SLICETHEPROMPT_NO_SYNC")}
        with patch.dict(os.environ, env_clean, clear=True), \
             patch("shutil.which", return_value="/usr/bin/rclone"), \
             patch.object(sync_mod, "_remote_exists", return_value=False):
            res = sync_mod.sync_project_async("egal")
        self.assertFalse(res["triggered"])
        self.assertEqual(res["reason"], "remote 'gdrive:' not configured")

    def test_bisync_builds_expected_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            projects_dir = Path(tmp) / "projects"
            projects_dir.mkdir()
            fake_run = MagicMock(returncode=0, stdout="ok", stderr="")
            with patch.object(sync_mod, "PROJECTS_DIR", projects_dir), \
                 patch("shutil.which", return_value="/usr/bin/rclone"), \
                 patch.object(sync_mod, "_remote_exists", return_value=True), \
                 patch("subprocess.run", return_value=fake_run) as run_mock:
                res = sync_mod.bisync(None, resync=True, dry_run=True)
            self.assertTrue(res["ok"])
            cmd = run_mock.call_args[0][0]
            self.assertEqual(
                cmd,
                [
                    "/usr/bin/rclone",
                    "bisync",
                    str(projects_dir),
                    "gdrive:SliceThePrompt/projects",
                    "--create-empty-src-dirs",
                    "--conflict-resolve",
                    "newer",
                    "--resync",
                    "--dry-run",
                ],
            )

    def test_watch_bisync_auto_resyncs_on_missing_prior(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp) / ".slicetheprompt"
            calls = [
                {"ok": False, "returncode": 1, "target": "gdrive:SliceThePrompt/projects",
                 "stderr": "ERROR : cannot find prior Path1 or Path2 listings"},
                {"ok": True, "returncode": 0, "target": "gdrive:SliceThePrompt/projects", "stderr": ""},
            ]
            with patch.object(sync_mod, "STATE_DIR", state_dir), \
                 patch.object(sync_mod, "SYNC_LOG", state_dir / "sync.log"), \
                 patch.object(sync_mod, "bisync", side_effect=calls) as bisync_mock:
                sync_mod.watch_bisync(interval_s=60, auto_resync=True, max_runs=1)
            self.assertEqual(bisync_mock.call_count, 2)
            self.assertEqual(bisync_mock.call_args_list[0].kwargs, {"resync": False})
            self.assertEqual(bisync_mock.call_args_list[1].kwargs, {"resync": True})
            self.assertIn("bisync OK", (state_dir / "sync.log").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
