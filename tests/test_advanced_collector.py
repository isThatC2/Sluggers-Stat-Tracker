from __future__ import annotations

import unittest

import tempfile
from pathlib import Path

from advanced_tracking.engine import collector
from advanced_tracking.engine.collector import (
    ActorsNotReady, capture_ready_payload, lock_ball_offset, wait_for_actors,
)


class CollectorSafetyTests(unittest.TestCase):
    def test_unverified_ball_offset_fails_closed(self):
        with self.assertRaisesRegex(SystemExit, "instead of exporting unverified"):
            lock_ball_offset(object(), timeout=0)

    def test_ready_requires_frames_and_durable_bytes(self):
        missing_bytes = capture_ready_payload(
            frames=30,
            missed_frames=0,
            bytes_on_disk=0,
            elapsed_s=0.5,
            stem="capture",
            park="mario_stadium",
            timer=100,
        )
        ready = capture_ready_payload(
            frames=30,
            missed_frames=0,
            bytes_on_disk=100,
            elapsed_s=0.5,
            stem="capture",
            park="mario_stadium",
            timer=100,
        )
        self.assertFalse(missing_bytes["ready"])
        self.assertTrue(ready["ready"])

    def test_the_worker_waits_for_the_field_instead_of_dying_at_match_start(self):
        # The advanced worker starts when the match does, which is several
        # seconds before the actor objects exist.
        attempts = []

        def actors(dme):
            attempts.append(dme)
            if len(attempts) < 4:
                raise ActorsNotReady("The P fielder pointer is null")
            return {"fielders": [], "offense": []}

        original = collector.resolve_actors
        collector.resolve_actors = actors
        try:
            self.assertEqual(wait_for_actors(object(), timeout=30),
                             {"fielders": [], "offense": []})
        finally:
            collector.resolve_actors = original
        self.assertEqual(len(attempts), 4)

    def test_a_field_that_never_loads_reports_the_last_failure(self):
        original = collector.resolve_actors
        collector.resolve_actors = lambda dme: (_ for _ in ()).throw(
            ActorsNotReady("Offense slot BAT has vtable 0x00000000"))
        try:
            with self.assertRaisesRegex(SystemExit, "Offense slot BAT"):
                wait_for_actors(object(), timeout=0)
        finally:
            collector.resolve_actors = original

    def test_a_stop_signal_ends_the_wait_promptly(self):
        original = collector.resolve_actors
        collector.resolve_actors = lambda dme: (_ for _ in ()).throw(
            ActorsNotReady("not yet"))
        try:
            with tempfile.TemporaryDirectory() as directory:
                stop = Path(directory) / "stop"
                stop.touch()
                with self.assertRaisesRegex(SystemExit, "Stopped before the field"):
                    wait_for_actors(object(), timeout=600, stop_path=stop)
        finally:
            collector.resolve_actors = original


if __name__ == "__main__":
    unittest.main()
