from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from openpyxl import load_workbook

from advanced_tracking.supervisor import AdvancedSupervisor, _read_json_lines


class AdvancedSupervisorTests(unittest.TestCase):
    def test_rejects_a_partial_json_record(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plays.jsonl"
            path.write_text('{"contact_timer":1}\n{"broken"', encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "line 2"):
                _read_json_lines(path)

    def test_exports_a_completed_worker_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            supervisor = AdvancedSupervisor(output, session_id="worker-session")
            supervisor.capture_dir.mkdir(parents=True)
            stem = supervisor.capture_dir / "mario_stadium-test"
            plays = stem.with_suffix(".live.jsonl")
            pitches = stem.with_suffix(".pitches.jsonl")
            plays.write_text(json.dumps({
                "contact_timer": 100,
                "swing_timer": 90,
                "batter": "Mario",
                "batted_ball": {"exit_velocity_mph": 100.0},
                "fielders": {}, "runners": {}, "throws": [],
            }) + "\n", encoding="utf-8")
            pitches.write_text(json.dumps({
                "pitch_timer": 80,
                "swing_timer": 90,
                "pitcher": "Luigi",
            }) + "\n", encoding="utf-8")
            stem.with_suffix(".json").write_text(json.dumps({
                "live_plays_path": str(plays),
                "live_pitches_path": str(pitches),
            }), encoding="utf-8")
            supervisor.manifest_path.write_text(json.dumps({
                "status": "captured",
                "stem": str(stem),
                "frames": 1000,
                "missed_frames": 0,
                "live_derivation": {"calibration_status": "confirmed", "position_offset": 4},
            }), encoding="utf-8")

            advanced = supervisor.export(output / "Original.xlsx", {})
            workbook = load_workbook(advanced, read_only=True)
            self.assertEqual(workbook["Batted Balls"]["H2"].value, "Luigi")
            self.assertEqual(workbook["Batted Balls"]["J2"].value, 100.0)
            self.assertEqual(workbook["Capture Health"]["B2"].value, "captured")
            self.assertEqual(workbook["Capture Health"]["I2"].value, "complete")
            workbook.close()
            self.assertFalse(supervisor.runtime_dir.exists())
            self.assertFalse((output / ".advanced-runtime").exists())

    def test_discard_removes_an_abandoned_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            supervisor = AdvancedSupervisor(output, session_id="abandoned")
            supervisor.capture_dir.mkdir(parents=True)
            (supervisor.capture_dir / "mario_stadium-test.bin").write_bytes(bytes(4096))

            supervisor.discard()

            self.assertFalse(supervisor.runtime_dir.exists())
            self.assertFalse((output / ".advanced-runtime").exists())

    def test_a_worker_failure_reports_what_the_worker_said(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            supervisor = AdvancedSupervisor(output, session_id="failed")
            supervisor.runtime_dir.mkdir(parents=True)
            supervisor.log_path.write_text(
                "fielders  P@0x8131B4B9\n"
                "The actor objects never appeared within 300s.\n",
                encoding="utf-8")
            self.assertEqual(supervisor._last_worker_message(),
                             "The actor objects never appeared within 300s.")

    def test_a_missing_worker_log_still_produces_a_message(self):
        with tempfile.TemporaryDirectory() as directory:
            supervisor = AdvancedSupervisor(Path(directory), session_id="nolog")
            self.assertEqual(supervisor._last_worker_message(), "no worker log was written")


if __name__ == "__main__":
    unittest.main()
