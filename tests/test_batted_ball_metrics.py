from __future__ import annotations

import unittest

from advanced_tracking.engine.derive import measure_batted_ball_metrics


class BattedBallMetricTests(unittest.TestCase):
    def test_measures_two_post_contact_vectors_and_ignores_the_launch_jump(self):
        # The first point is already after the unobservable plate-to-launch
        # transition. At 10 Hz these two steps are (1, 1, -2) every 0.1 s.
        frames = [
            {"timer": 10, "ball": (100.0, 20.0, -50.0)},
            {"timer": 11, "ball": (101.0, 21.0, -52.0)},
            {"timer": 12, "ball": (102.0, 22.0, -54.0)},
        ]
        result = measure_batted_ball_metrics(
            frames, fps=10.0,
            landing={"at": [30.0, 0.0, -40.0], "distance_units": 50.0},
        )
        self.assertEqual(result["measurement_status"], "measured")
        self.assertAlmostEqual(result["exit_velocity_mph"], 54.794, places=3)
        self.assertAlmostEqual(result["launch_angle_deg"], 24.095, places=3)
        self.assertAlmostEqual(result["spray_angle_deg"], 26.565, places=3)
        self.assertEqual(result["spray_side"], "first_base")
        self.assertAlmostEqual(result["distance_ft"], 164.042, places=3)

    def test_refuses_to_measure_across_a_missed_frame(self):
        result = measure_batted_ball_metrics([
            {"timer": 10, "ball": (0.0, 0.0, 0.0)},
            {"timer": 12, "ball": (1.0, 1.0, -2.0)},
            {"timer": 13, "ball": (2.0, 2.0, -4.0)},
        ])
        self.assertEqual(result, {
            "measurement_status": "unavailable",
            "invalid_reason": "frame_gap_at_launch",
        })

    def test_a_stale_ball_feed_is_not_a_batted_ball(self):
        # Both shapes the dead feed takes, straight out of the archive: the
        # coordinates jump to the fallback in one frame, or they never move.
        # Ball height is exactly zero throughout in both.
        jumped = measure_batted_ball_metrics([
            {"timer": 94811, "ball": (-19.5176, 0.0, -19.99)},
            {"timer": 94812, "ball": (0.0, 0.0, -18.6)},
            {"timer": 94813, "ball": (0.0, 0.0, -18.6)},
        ], fps=59.94)
        self.assertEqual(jumped["measurement_status"], "unavailable")
        self.assertEqual(jumped["invalid_reason"], "ball_position_discontinuity")
        self.assertIsNone(jumped.get("exit_velocity_mph"))

        frozen = measure_batted_ball_metrics([
            {"timer": 96717, "ball": (41.3502, 0.0, -47.1605)},
            {"timer": 96718, "ball": (41.3502, 0.0, -47.1605)},
            {"timer": 96719, "ball": (41.3502, 0.0, -47.1605)},
        ], fps=59.94)
        self.assertEqual(frozen["measurement_status"], "unavailable")
        self.assertEqual(frozen["invalid_reason"], "ball_did_not_move")

    def test_the_hardest_hit_in_the_archive_still_measures(self):
        # 119.0 mph is the fastest of 3,940 archived batted balls. The bound
        # that rejects a stale feed must not be anywhere near it.
        # 1 unit is 1 metre, so mph -> m/s -> units per frame.
        per_frame = 119.0 / 2.2369362921 / 59.94
        frames = [{"timer": 100 + i, "ball": (0.0, 0.0, -per_frame * i)}
                  for i in range(3)]
        result = measure_batted_ball_metrics(frames, fps=59.94)
        self.assertEqual(result["measurement_status"], "measured")
        self.assertAlmostEqual(result["exit_velocity_mph"], 119.0, delta=0.1)


if __name__ == "__main__":
    unittest.main()
