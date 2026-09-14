from __future__ import annotations

import unittest

from advanced_tracking.normalize import normalize_play, play_id, sanitize_play


class NormalizeTests(unittest.TestCase):
    def test_sanitizes_intermediate_records_to_excel_only_fields(self):
        public = sanitize_play({
            "contact_timer": 10,
            "stadium_research": [{"private": True}],
            "park_attribution": True,
            "fielders": {"CF": {"character": "Mario", "private_state_frames": 20}},
            "batted_ball": {"exit_velocity_mph": 90, "private_detail": True},
            "runners": {},
            "throws": [],
        })
        self.assertEqual(public["contact_timer"], 10)
        self.assertNotIn("stadium_research", public)
        self.assertNotIn("park_attribution", public)
        self.assertNotIn("private_state_frames", public["fielders"]["CF"])
        self.assertNotIn("private_detail", public["batted_ball"])

    def test_play_id_uses_the_game_timer(self):
        self.assertEqual(play_id("session-a", 9025), "session-a:9025")
        with self.assertRaises(ValueError):
            play_id("session-a", None)

    def test_hazard_fields_never_reach_rows_and_forced_motion_withholds_speed(self):
        play = {
            "contact_timer": 9025,
            "inning": 1,
            "inning_half": 1,
            "batter": "Donkey Kong",
            "primary_fielder": "CF",
            "caught_in_flight": True,
            "first_touch": {"by": "CF", "at": [3.0, 4.0, 5.0]},
            "stadium_research": [{"should": "never escape"}],
            "fielders": {
                "CF": {
                    "character": "Mario",
                    "character_id": 0,
                    "start": [1.0, 0.0, 2.0],
                    "run_path_units": 10.0,
                    "displacement_units": 8.0,
                    "sprint_speed_fps": 31.0,
                    "route_efficiency": 0.8,
                    "assist_frames": 2,
                    "fielded": True,
                }
            },
            "runners": {},
            "throws": [],
        }
        rows = normalize_play(1, play)
        fielder = rows["fielding"][0]
        self.assertEqual(fielder["sprint_speed_fps"], 31.0)
        self.assertIsNone(fielder["route_efficiency"])
        self.assertEqual(fielder["route_excluded_because"], "forced_motion")
        # Speed survives: the sprint sample never counted the assisted frames.
        self.assertFalse(fielder["route_valid"])
        self.assertFalse(any("stadium" in key or "park" in key
                             for group in rows.values() for row in group for key in row))

    def test_throw_and_runner_units_are_normalized(self):
        play = {
            "contact_timer": 100,
            "home_to_first_s": 3.2,
            "runners": {
                "BAT": {
                    "character": "Mario",
                    "character_id": 0,
                    "run_path_units": 10,
                    "sprint_speed_fps": 30,
                    "bases_ran": 1,
                    "lead_at_contact_units": 2,
                    "assist_frames": 0,
                    "teleports": 0,
                }
            },
            "fielders": {},
            "throws": [{
                "sequence": 1,
                "thrower_character": "Luigi",
                "peak_speed_mph": 75.5,
                "flight_frames": 60,
                "launch_frame": 120,
                "arrival_frame": 150,
                "start": [1, 2, 3],
                "end": [4, 5, 6],
            }],
        }
        rows = normalize_play(2, play)
        self.assertAlmostEqual(rows["baserunning"][0]["path_ft"], 32.808, places=3)
        self.assertAlmostEqual(rows["baserunning"][0]["lead_ft"], 6.562, places=3)
        self.assertEqual(rows["baserunning"][0]["origin_base"], "home")
        self.assertEqual(rows["baserunning"][0]["destination_base"], "first")
        self.assertEqual(rows["baserunning"][0]["home_to_first_s"], 3.2)
        self.assertEqual(rows["throws"][0]["flight_time_s"], 0.5005)

    def test_catch_type_uses_the_verified_approach_window(self):
        play = {
            "contact_timer": 200,
            "caught_in_flight": True,
            "first_touch": {"by": "RF", "frame": 260, "at": [1, 2, 3]},
            "catch_approaches": [
                {"by": "RF", "start_frame": 250, "end_frame": 259,
                 "catch_type": 6, "approach": "leap"},
            ],
            "fielders": {"RF": {"character": "Yoshi"}},
            "runners": {},
            "throws": [],
        }
        rows = normalize_play(3, play)
        self.assertEqual(rows["fielding"][0]["catch_type"], "leap")

    def test_a_ball_knocked_loose_is_not_published_as_a_throw(self):
        rows = normalize_play(4, sanitize_play({
            "contact_timer": 10,
            "fielders": {}, "runners": {},
            "throws": [
                {"sequence": 1, "thrower_position": "CF", "receiver_position": "2B",
                 "peak_speed_mph": 74.0},
                {"sequence": 2, "thrower_position": "LF", "receiver_position": "SS",
                 "peak_speed_mph": 19.0, "is_throw": False},
            ],
        }))
        self.assertEqual([row["sequence"] for row in rows["throws"]], [1])


if __name__ == "__main__":
    unittest.main()
