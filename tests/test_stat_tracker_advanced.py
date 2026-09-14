from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import stat_tracker


def _game():
    return SimpleNamespace(
        away_team=SimpleNamespace(name="Away"),
        home_team=SimpleNamespace(name="Home"),
        stadium=SimpleNamespace(display="Mario Stadium"),
        time_of_day=SimpleNamespace(display="Day"),
        regulation_innings=SimpleNamespace(value=9),
        match_quit_early=False,
    )


class AdvancedIsolationTests(unittest.TestCase):
    def test_original_workbook_is_written_before_advanced_export(self):
        calls = []

        class FakeOutputter:
            def __init__(self, template, game):
                calls.append(("open-original", template))

            def output_game(self, game):
                calls.append(("write-original", game.away_team.name))
                return "original.xlsx"

        class FakeSupervisor:
            def stop_and_export(self, original, summary):
                calls.append(("advanced", original.name))
                raise stat_tracker.AdvancedTrackingError("capture unavailable")

        context = stat_tracker.MatchContext()
        context.advanced_supervisor = FakeSupervisor()
        with patch.object(stat_tracker.out, "Outputter", FakeOutputter):
            result = stat_tracker.output_stats_to_excel(_game(), context)

        self.assertEqual(result, "original.xlsx")
        self.assertEqual([item[0] for item in calls], [
            "open-original", "write-original", "advanced",
        ])
        self.assertIsNone(context.advanced_supervisor)

    def test_original_output_works_when_advanced_worker_never_started(self):
        class FakeOutputter:
            def __init__(self, template, game):
                pass

            def output_game(self, game):
                return "original.xlsx"

        with patch.object(stat_tracker.out, "Outputter", FakeOutputter):
            result = stat_tracker.output_stats_to_excel(_game(), stat_tracker.MatchContext())
        self.assertEqual(result, "original.xlsx")

    def test_an_abandoned_match_discards_its_capture(self):
        discarded = []

        class FakeSupervisor:
            def discard(self):
                discarded.append("discarded")

        context = stat_tracker.MatchContext()
        context.advanced_supervisor = FakeSupervisor()
        stat_tracker._discard_advanced_tracking(context)

        self.assertEqual(discarded, ["discarded"])
        self.assertIsNone(context.advanced_supervisor)

    def test_a_failed_discard_never_reaches_the_caller(self):
        class FakeSupervisor:
            def discard(self):
                raise OSError("worker is wedged")

        context = stat_tracker.MatchContext()
        context.advanced_supervisor = FakeSupervisor()
        stat_tracker._discard_advanced_tracking(context)
        self.assertIsNone(context.advanced_supervisor)

    def test_a_live_match_reads_as_plausible(self):
        live = {address: value for _, address, value in (
            ("outs", 0x900D5AA9, 2), ("balls", 0x900D5AA8, 3),
            ("strikes", 0x900D5AA7, 2), ("inning", 0x900D5D97, 9),
            ("inning half", 0x900D5E25, 1), ("stadium", 0x811F769D, 8),
        )}
        with patch.object(stat_tracker, "read_data",
                          lambda data: bytes([live[data.address]])):
            self.assertEqual(stat_tracker.implausible_match_values(), [])

    def test_a_misread_is_not_mistaken_for_a_match_in_progress(self):
        # Every sentinel out of range at once, which is what the fault looks
        # like and what a real match can never produce.
        with patch.object(stat_tracker, "read_data", lambda data: bytes([200])):
            wrong = stat_tracker.implausible_match_values()
        self.assertEqual(len(wrong), len(stat_tracker.MATCH_SENTINELS))
        self.assertIn("outs=200", wrong)

    def test_an_unreadable_address_counts_as_a_misread(self):
        def explode(data):
            raise RuntimeError("not hooked")

        with patch.object(stat_tracker, "read_data", explode):
            self.assertEqual(stat_tracker.implausible_match_values(), ["outs=unreadable"])


if __name__ == "__main__":
    unittest.main()
