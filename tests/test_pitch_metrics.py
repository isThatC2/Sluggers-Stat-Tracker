from __future__ import annotations

import unittest

from advanced_tracking.engine.derive import measure_pitch_metrics

# One real pitch, lifted out of a tracker log that printed both the ball
# samples it measured from and the numbers it computed from them. The logged
# answers are the ground truth this measurement has to land on:
#
#   speed_mph  79.8   at feet_per_unit 3.2808 over 0.482173 s
#   horizontal chord deviation  +0.0385895483 units
#   vertical   chord deviation  +0.019103492  units
#
# Speed here is timed off the GAME clock rather than the log's wall clock, so
# the test drives it at the frame rate that reproduces the logged flight time.
FIRE_BRO_FASTBALL = [
    (-0.992703, 3.670702, -18.079243), (-0.956458, 3.582865, -17.445421),
    (-0.920285, 3.495204, -16.812868), (-0.884184, 3.407718, -16.18158),
    (-0.848227, 3.320581, -15.554205), (-0.812415, 3.233793, -14.93072),
    (-0.776745, 3.147352, -14.311101), (-0.741218, 3.061255, -13.695323),
    (-0.705832, 2.975503, -13.083363), (-0.670589, 2.890094, -12.475196),
    (-0.635486, 2.805026, -11.8708), (-0.600523, 2.720298, -11.270151),
    (-0.5657, 2.635909, -10.673225), (-0.531905, 2.551857, -10.080001),
    (-0.499137, 2.46814, -9.490454), (-0.467397, 2.384758, -8.904561),
    (-0.436682, 2.30171, -8.322301), (-0.406993, 2.218993, -7.74365),
    (-0.37833, 2.136606, -7.168588), (-0.350691, 2.054549, -6.59709),
    (-0.324076, 1.97282, -6.029135), (-0.297596, 1.891418, -5.464701),
    (-0.27125, 1.81034, -4.903767), (-0.245038, 1.729587, -4.34631),
    (-0.218959, 1.649156, -3.792309), (-0.193012, 1.569047, -3.241742),
    (-0.167198, 1.489258, -2.694589), (-0.141515, 1.409788, -2.150828),
    (-0.115964, 1.330635, -1.610438), (-0.090543, 1.251799, -1.073399),
]
LOGGED_ELAPSED_SECONDS = 0.482173
LOGGED_SPEED_MPH = 79.8
LOGGED_FEET_PER_UNIT = 3.2808


def frames(points, start=1000):
    return [(start + index, point) for index, point in enumerate(points)]


class PitchMetricTests(unittest.TestCase):
    def test_reproduces_the_logged_speed_and_break(self):
        fps = (len(FIRE_BRO_FASTBALL) - 1) / LOGGED_ELAPSED_SECONDS
        result = measure_pitch_metrics(frames(FIRE_BRO_FASTBALL), fps=fps)

        self.assertEqual(result["measurement_status"], "measured")
        # The log's world scale was 3.2808; this build uses the measured
        # 3.280839895, so the two speeds differ only by that ratio.
        from advanced_tracking.engine.derive import FEET_PER_UNIT
        expected = LOGGED_SPEED_MPH * FEET_PER_UNIT / LOGGED_FEET_PER_UNIT
        self.assertAlmostEqual(result["velocity_mph"], expected, delta=0.05)
        self.assertAlmostEqual(result["horizontal_break_units"], 0.0385895483, places=3)
        self.assertAlmostEqual(result["vertical_break_units"], 0.019103492, places=3)
        self.assertEqual(result["flight_samples"], len(FIRE_BRO_FASTBALL))

    def test_a_dropped_frame_is_reported_rather_than_measured_across(self):
        samples = frames(FIRE_BRO_FASTBALL)
        del samples[10]
        result = measure_pitch_metrics(samples, fps=59.94)
        self.assertEqual(result["measurement_status"], "unavailable")
        self.assertEqual(result["invalid_reason"], "frame_gap_in_flight")
        self.assertIsNone(result.get("velocity_mph"))

    def test_a_ball_that_never_left_the_hand_is_not_a_pitch(self):
        still = [(0.0, 2.0, -18.0)] * 20
        result = measure_pitch_metrics(frames(still), fps=59.94)
        self.assertEqual(result["measurement_status"], "unavailable")
        self.assertEqual(result["invalid_reason"], "no_pitch_flight_observed")

    def test_too_few_samples_are_not_measured(self):
        result = measure_pitch_metrics(frames(FIRE_BRO_FASTBALL[:4]), fps=59.94)
        self.assertEqual(result["measurement_status"], "unavailable")
        self.assertEqual(result["invalid_reason"], "insufficient_flight_samples")

    def test_break_is_signed_so_the_direction_survives(self):
        mirrored = [(-x, y, z) for x, y, z in FIRE_BRO_FASTBALL]
        fps = (len(FIRE_BRO_FASTBALL) - 1) / LOGGED_ELAPSED_SECONDS
        straight = measure_pitch_metrics(frames(FIRE_BRO_FASTBALL), fps=fps)
        flipped = measure_pitch_metrics(frames(mirrored), fps=fps)
        self.assertAlmostEqual(flipped["horizontal_break_units"],
                               -straight["horizontal_break_units"], places=6)
        self.assertAlmostEqual(flipped["velocity_mph"], straight["velocity_mph"], places=6)


if __name__ == "__main__":
    unittest.main()
