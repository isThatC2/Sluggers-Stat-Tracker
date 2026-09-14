"""Per-player views of the detail sheets.

The detail sheets are one row per play, which is what analysis wants and what
nobody reads. These are the three questions people actually open a stat file
to answer -- who hits it hardest, who throws hardest, who covers ground -- and
every number in them is an aggregate of rows that are still there to check.

NOTHING IS INVENTED HERE. There are no rates against a league baseline, no
"hard hit" threshold, no grades. A column is a count, a mean or a best of
measurements that survived their own validity check, and a player whose every
sample was withheld gets a blank rather than a zero.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


# A FOUL IS NOT A BALL IN PLAY. Every one of them is a real batted ball with a
# real exit velocity, and 37 of the 167 batted balls in one archived game are
# fouls -- so counting them as balls in play roughly doubles a batter's total
# and drags his averages toward whatever he happened to slice into the seats.
# Both numbers are published, and the rate columns are computed over the fair
# ones only.
FAIR_CLASSES = frozenset({"fair_in_play", "fair_caught", "home_run",
                          "home_run_robbed"})
FOUL_CLASSES = frozenset({"foul", "foul_home_run"})

BATTING_COLUMNS = (
    "batter", "balls_in_play", "fouls", "home_runs", "avg_exit_velocity_mph",
    "max_exit_velocity_mph", "avg_launch_angle_deg", "avg_distance_ft",
    "max_distance_ft", "avg_hang_time_s", "batted_balls", "measured_of_in_play",
)
PITCHING_COLUMNS = (
    "pitcher", "pitches", "avg_velocity_mph", "max_velocity_mph",
    "avg_horizontal_break_units", "avg_vertical_break_units", "swings",
    "takes", "bunts", "contact", "measured_of_total",
)
DEFENSE_COLUMNS = (
    "player", "positions", "plays", "touches", "catches", "dives", "leaps",
    "top_fielding_speed_fps", "avg_route_efficiency", "avg_reaction_s",
    "throws", "top_throw_mph", "top_running_speed_fps", "best_home_to_first_s",
)


def _numbers(rows: Sequence[Mapping[str, Any]], key: str) -> list[float]:
    return [row[key] for row in rows
            if isinstance(row.get(key), (int, float)) and not isinstance(row.get(key), bool)]


def _mean(values: Sequence[float], digits: int = 2) -> float | None:
    return round(sum(values) / len(values), digits) if values else None


def _best(values: Sequence[float], digits: int = 2, lowest: bool = False) -> float | None:
    if not values:
        return None
    return round(min(values) if lowest else max(values), digits)


def _grouped(rows: Sequence[Mapping[str, Any]], key: str) -> dict[Any, list]:
    out: dict[Any, list] = {}
    for row in rows:
        name = row.get(key)
        if name is None:
            continue
        out.setdefault(name, []).append(row)
    return out


def batting_summary(batted_balls: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for batter, hits in sorted(_grouped(batted_balls, "batter").items(),
                               key=lambda item: -len(item[1])):
        in_play = [hit for hit in hits if hit.get("result") in FAIR_CLASSES]
        fouls = [hit for hit in hits if hit.get("result") in FOUL_CLASSES]
        measured = [hit for hit in in_play
                    if hit.get("measurement_status") == "measured"]
        velocities = _numbers(measured, "exit_velocity_mph")
        distances = _numbers(measured, "distance_ft")
        rows.append({
            "batter": batter,
            "balls_in_play": len(in_play),
            "fouls": len(fouls),
            "home_runs": sum(1 for hit in hits if hit.get("result") == "home_run"),
            "avg_exit_velocity_mph": _mean(velocities, 1),
            "max_exit_velocity_mph": _best(velocities, 1),
            "avg_launch_angle_deg": _mean(_numbers(measured, "launch_angle_deg"), 1),
            "avg_distance_ft": _mean(distances, 1),
            "max_distance_ft": _best(distances, 1),
            "avg_hang_time_s": _mean(_numbers(in_play, "hang_time_s"), 2),
            # Everything the bat touched, fouls and unreadable calls included,
            # so the two counts above can be reconciled against the detail sheet.
            "batted_balls": len(hits),
            "measured_of_in_play": f"{len(measured)}/{len(in_play)}",
        })
    return rows


def pitching_summary(pitches: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for pitcher, thrown in sorted(_grouped(pitches, "pitcher").items(),
                                  key=lambda item: -len(item[1])):
        measured = [pitch for pitch in thrown
                    if pitch.get("measurement_status") == "measured"]
        velocities = _numbers(measured, "velocity_mph")
        offers = [pitch.get("offer") for pitch in thrown]
        rows.append({
            "pitcher": pitcher,
            "pitches": len(thrown),
            "avg_velocity_mph": _mean(velocities, 1),
            "max_velocity_mph": _best(velocities, 1),
            "avg_horizontal_break_units": _mean(
                [abs(value) for value in _numbers(measured, "horizontal_break_units")], 3),
            "avg_vertical_break_units": _mean(
                [abs(value) for value in _numbers(measured, "vertical_break_units")], 3),
            "swings": offers.count("swing"),
            "takes": offers.count("take"),
            "bunts": offers.count("bunt"),
            "contact": sum(1 for pitch in thrown if pitch.get("outcome") == "contact"),
            "measured_of_total": f"{len(measured)}/{len(thrown)}",
        })
    return rows


def defense_summary(
    fielding: Sequence[Mapping[str, Any]],
    throws: Sequence[Mapping[str, Any]],
    baserunning: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """One row per player, fielding and running together.

    They are the same person, and a file that splits them makes you look up the
    same name twice to ask one question about them.
    """
    players: dict[str, dict[str, Any]] = {}

    def entry(name: str) -> dict[str, Any]:
        return players.setdefault(name, {
            "player": name, "positions": set(), "plays": 0, "touches": 0,
            "catches": 0, "dives": 0, "leaps": 0, "speeds": [], "routes": [],
            "reactions": [], "throws": 0, "throw_speeds": [],
            "run_speeds": [], "home_to_first": [],
        })

    for row in fielding:
        name = row.get("fielder")
        if name is None:
            continue
        item = entry(name)
        item["plays"] += 1
        if row.get("position"):
            item["positions"].add(row["position"])
        if row.get("first_touch"):
            item["touches"] += 1
        if row.get("caught_in_flight"):
            item["catches"] += 1
        if row.get("catch_type") == "dive":
            item["dives"] += 1
        elif row.get("catch_type") == "leap":
            item["leaps"] += 1
        if isinstance(row.get("sprint_speed_fps"), (int, float)):
            item["speeds"].append(row["sprint_speed_fps"])
        if isinstance(row.get("route_efficiency"), (int, float)):
            item["routes"].append(row["route_efficiency"])
        if isinstance(row.get("reaction_s"), (int, float)):
            item["reactions"].append(row["reaction_s"])

    for row in throws:
        name = row.get("thrower")
        if name is None:
            continue
        item = entry(name)
        item["throws"] += 1
        if isinstance(row.get("peak_velocity_mph"), (int, float)):
            item["throw_speeds"].append(row["peak_velocity_mph"])

    for row in baserunning:
        name = row.get("runner")
        if name is None:
            continue
        item = entry(name)
        if row.get("movement_valid") and isinstance(row.get("sprint_speed_fps"), (int, float)):
            item["run_speeds"].append(row["sprint_speed_fps"])
        if isinstance(row.get("home_to_first_s"), (int, float)):
            item["home_to_first"].append(row["home_to_first_s"])

    rows = []
    for item in sorted(players.values(), key=lambda entry: (-entry["plays"], entry["player"])):
        rows.append({
            "player": item["player"],
            "positions": "/".join(sorted(item["positions"])) or None,
            "plays": item["plays"],
            "touches": item["touches"],
            "catches": item["catches"],
            "dives": item["dives"],
            "leaps": item["leaps"],
            "top_fielding_speed_fps": _best(item["speeds"], 2),
            "avg_route_efficiency": _mean(item["routes"], 3),
            "avg_reaction_s": _mean(item["reactions"], 3),
            "throws": item["throws"],
            "top_throw_mph": _best(item["throw_speeds"], 1),
            "top_running_speed_fps": _best(item["run_speeds"], 2),
            "best_home_to_first_s": _best(item["home_to_first"], 3, lowest=True),
        })
    return rows
