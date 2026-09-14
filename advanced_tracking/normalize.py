"""Normalize derived tracking records into stable workbook rows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


FEET_PER_UNIT = 3.280839895013123

PUBLIC_PLAY_FIELDS = (
    "contact_timer", "swing_timer", "inning", "inning_half", "outs", "balls",
    "strikes", "batter", "batted_ball_class", "batted_ball", "landing",
    "first_touch", "hang_time_s", "caught_in_flight", "primary_fielder",
    "catch_approaches", "home_to_first_s", "ninety_foot_split_s",
)
PUBLIC_FIELDER_FIELDS = (
    "name", "character", "character_id", "start", "run_path_units",
    "displacement_units", "route_efficiency", "sprint_speed_fps", "reaction_s",
    "fielded", "assist_frames", "teleports", "frozen_frames",
    "catch_type_frames",
)
PUBLIC_RUNNER_FIELDS = (
    "name", "character", "character_id", "origin_base", "destination_base",
    "bases_ran", "lead_at_contact_units", "run_path_units", "sprint_speed_fps",
    "stealing", "assist_frames", "teleports",
)
PUBLIC_THROW_FIELDS = (
    "sequence", "thrower_character", "thrower_position", "receiver_character",
    "receiver_position", "intended_target_position", "target_base",
    "peak_speed_mph", "hold_s", "flight_frames", "launch_frame",
    "arrival_frame", "start", "end", "buddy_throw", "outs_recorded",
    # Not published as a column. It is carried through the sanitizer so the
    # rows below can drop the records the deriver has already established are
    # not throws -- a ball knocked out of a glove, or one picked up off the
    # ground -- which otherwise reach the workbook as a slow throw nobody made.
    "is_throw",
)


def _selected(source: Mapping[str, Any], fields: Sequence[str]) -> dict[str, Any]:
    return {field: source.get(field) for field in fields if field in source}


def sanitize_play(play: Mapping[str, Any]) -> dict[str, Any]:
    """Reduce a derived record to the documented Excel-only public schema."""

    public = _selected(play, PUBLIC_PLAY_FIELDS)
    for field, allowed in (
        ("batted_ball", (
            "exit_velocity_mph", "launch_angle_deg", "spray_angle_deg",
            "spray_side", "distance_ft", "measurement_status", "invalid_reason",
        )),
        ("landing", ("t", "frame", "at", "distance_units")),
        ("first_touch", (
            "t", "frame", "by", "character_id", "character", "at",
            "ball_height_units",
        )),
    ):
        value = play.get(field)
        public[field] = _selected(value, allowed) if isinstance(value, Mapping) else None
    public["fielders"] = {
        name: _selected(actor, PUBLIC_FIELDER_FIELDS)
        for name, actor in (play.get("fielders") or {}).items()
        if isinstance(actor, Mapping)
    }
    public["runners"] = {
        name: _selected(actor, PUBLIC_RUNNER_FIELDS)
        for name, actor in (play.get("runners") or {}).items()
        if isinstance(actor, Mapping)
    }
    public["throws"] = [
        _selected(record, PUBLIC_THROW_FIELDS)
        for record in (play.get("throws") or ())
        if isinstance(record, Mapping)
    ]
    public["catch_approaches"] = [
        _selected(record, (
            "by", "catch_type", "approach", "dive", "leap", "start_frame",
            "end_frame", "frames",
        ))
        for record in (play.get("catch_approaches") or ())
        if isinstance(record, Mapping)
    ]
    return public


def _xyz(value: Any) -> tuple[Any, Any, Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and len(value) >= 3:
        return value[0], value[1], value[2]
    return None, None, None


def _feet(value: Any) -> float | None:
    if not isinstance(value, (int, float)):
        return None
    return round(value * FEET_PER_UNIT, 3)


def play_id(session_id: str, contact_timer: Any) -> str:
    if not session_id:
        raise ValueError("session_id is required")
    if not isinstance(contact_timer, int) or contact_timer < 0:
        raise ValueError("a non-negative integer contact_timer is required")
    return f"{session_id}:{contact_timer}"


def _catch_type(play: Mapping[str, Any], position: str, actor: Mapping[str, Any]) -> str | None:
    """Return the measured approach nearest this fielder's touch, if any."""

    approaches = [
        approach for approach in (play.get("catch_approaches") or ())
        if isinstance(approach, Mapping) and approach.get("by") == position
    ]
    touch = play.get("first_touch") or {}
    touch_frame = touch.get("frame") if touch.get("by") == position else None
    if approaches:
        if isinstance(touch_frame, int):
            approaches.sort(key=lambda item: min(
                abs(touch_frame - (item.get("start_frame") or touch_frame)),
                abs(touch_frame - (item.get("end_frame") or touch_frame)),
            ))
        chosen = approaches[0]
        return chosen.get("approach") or (
            str(chosen["catch_type"]) if chosen.get("catch_type") is not None else None
        )

    frames = actor.get("catch_type_frames") or {}
    if isinstance(frames, Mapping) and frames:
        names = {1: "ordinary", 2: "throw", 3: "dive", 5: "clamber", 6: "leap"}
        code = max(frames, key=lambda item: frames[item])
        try:
            numeric_code = int(code)
        except (TypeError, ValueError):
            return str(code)
        return names.get(numeric_code, str(numeric_code))
    return None


def normalize_play(
    play_number: int,
    play: Mapping[str, Any],
    *,
    batted_ball: Mapping[str, Any] | None = None,
    pitcher: str | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Return workbook rows for one derived play.

    Rows are keyed by `play`, which counts from 1 within this game. The session
    is named once on Game Summary rather than repeated down every row of every
    sheet, and the game's own `contact_timer` stays on Batted Balls as the
    durable identity for anyone joining two exports together.

    Stadium-cause fields are intentionally never accepted or copied. Neutral
    motion exclusions remain because publishing a route contaminated by forced
    movement would be worse than leaving the cell blank.
    """

    timer = play.get("contact_timer")
    batted_ball = batted_ball or play.get("batted_ball") or {}
    touch = play.get("first_touch") or {}
    touch_at = _xyz(touch.get("at"))
    landing = play.get("landing") or {}
    endpoint_record = touch if play.get("caught_in_flight") else landing
    endpoint = endpoint_record.get("at") if isinstance(endpoint_record, Mapping) else None
    endpoint_at = _xyz(endpoint)
    ball_row = {
        "play": play_number,
        "inning": play.get("inning"),
        "inning_half": play.get("inning_half"),
        "outs": play.get("outs"),
        "balls": play.get("balls"),
        "strikes": play.get("strikes"),
        "batter": play.get("batter"),
        "pitcher": pitcher,
        "result": play.get("batted_ball_class"),
        "exit_velocity_mph": batted_ball.get("exit_velocity_mph"),
        "launch_angle_deg": batted_ball.get("launch_angle_deg"),
        "spray_angle_deg": batted_ball.get("spray_angle_deg"),
        "spray_side": batted_ball.get("spray_side"),
        "distance_ft": batted_ball.get("distance_ft"),
        "hang_time_s": play.get("hang_time_s"),
        "endpoint_type": "catch" if play.get("caught_in_flight") else "landing",
        "measurement_status": batted_ball.get("measurement_status", "measured"),
        "invalid_reason": batted_ball.get("invalid_reason"),
        "contact_timer": timer,
        "endpoint_x": endpoint_at[0],
        "endpoint_y": endpoint_at[1],
        "endpoint_z": endpoint_at[2],
    }

    fielding_rows = []
    for position, actor in (play.get("fielders") or {}).items():
        if not isinstance(actor, Mapping):
            continue
        assisted = bool(actor.get("assist_frames"))
        discontinuous = bool(actor.get("teleports"))
        immobilized = bool(actor.get("frozen_frames"))
        invalid_reasons = [
            reason for reason, active in (
                ("forced_motion", assisted),
                ("position_discontinuity", discontinuous),
                ("immobilized", immobilized),
            ) if active
        ]
        start = _xyz(actor.get("start"))
        touched = touch.get("by") == position
        actor_touch = touch_at if touched else (None, None, None)
        catch_type = _catch_type(play, position, actor)
        route_efficiency = actor.get("route_efficiency") if not invalid_reasons else None
        path_ft = _feet(actor.get("run_path_units"))
        # A FIELDER WHO DID NOTHING IS NOT A ROW. Nine of them are on the field
        # for every play and most stand still; publishing all nine turns the
        # sheet into 90% of people the ball never went near. Anyone who touched
        # it, was charged with it, went for it, or ran a real route is kept.
        if not (touched or position == play.get("primary_fielder")
                or catch_type or route_efficiency is not None
                or actor.get("fielded")):
            continue
        fielding_rows.append({
            "play": play_number,
            "fielder": actor.get("character") or actor.get("name"),
            "position": position,
            "is_primary": position == play.get("primary_fielder"),
            "first_touch": touched,
            "fielded": actor.get("fielded"),
            "caught_in_flight": play.get("caught_in_flight") if touched else None,
            "catch_type": catch_type,
            "path_ft": path_ft,
            "displacement_ft": _feet(actor.get("displacement_units"))
                if not invalid_reasons else None,
            "route_efficiency": route_efficiency,
            # SPEED SURVIVES A FORCED-MOTION PLAY. The engine's sprint sample
            # already drops the frames the game moved the body on, so what is
            # left is the character running. Route, displacement and reaction
            # cannot be repaired that way and are withheld instead -- which is
            # what `route_valid` is about, and why a False there can sit beside
            # a perfectly good speed.
            "sprint_speed_fps": actor.get("sprint_speed_fps") if not discontinuous else None,
            "reaction_s": actor.get("reaction_s") if not invalid_reasons else None,
            "route_valid": not invalid_reasons,
            "route_excluded_because": ",".join(invalid_reasons) or None,
            "character_id": actor.get("character_id"),
            "start_x": start[0], "start_y": start[1], "start_z": start[2],
            "touch_x": actor_touch[0], "touch_y": actor_touch[1], "touch_z": actor_touch[2],
        })

    runner_rows = []
    for name, actor in (play.get("runners") or {}).items():
        if not isinstance(actor, Mapping):
            continue
        invalid_reasons = [
            reason for reason, active in (
                ("forced_motion", bool(actor.get("assist_frames"))),
                ("position_discontinuity", bool(actor.get("teleports"))),
            ) if active
        ]
        origin_base = {"BAT": "home", "R1": "first", "R2": "second", "R3": "third"}.get(name)
        bases_ran = actor.get("bases_ran")
        destination_base = None
        if origin_base is not None and isinstance(bases_ran, int) and bases_ran >= 0:
            bases = ("home", "first", "second", "third", "home")
            origin_index = {"home": 0, "first": 1, "second": 2, "third": 3}[origin_base]
            destination_base = bases[min(4, origin_index + bases_ran)]
        runner_rows.append({
            "play": play_number,
            "runner": actor.get("character") or name,
            "origin_base": actor.get("origin_base") or origin_base,
            "destination_base": actor.get("destination_base") or destination_base,
            "bases_ran": bases_ran,
            "lead_ft": _feet(actor.get("lead_at_contact_units")),
            "path_ft": _feet(actor.get("run_path_units")),
            "sprint_speed_fps": actor.get("sprint_speed_fps")
                if not actor.get("teleports") else None,
            "home_to_first_s": play.get("home_to_first_s") if name == "BAT" else None,
            "ninety_foot_split_s": play.get("ninety_foot_split_s") if name == "BAT" else None,
            "stealing": actor.get("stealing"),
            "movement_valid": not invalid_reasons,
            "invalid_reason": ",".join(invalid_reasons) or None,
            "character_id": actor.get("character_id"),
        })

    throw_rows = []
    for record in play.get("throws") or ():
        if not isinstance(record, Mapping) or record.get("is_throw") is False:
            continue
        start = _xyz(record.get("start"))
        end = _xyz(record.get("end"))
        launch_frame = record.get("launch_frame")
        arrival_frame = record.get("arrival_frame")
        flight_frames = (
            arrival_frame - launch_frame
            if isinstance(launch_frame, int) and isinstance(arrival_frame, int)
            and arrival_frame >= launch_frame
            else record.get("flight_frames")
        )
        throw_rows.append({
            "play": play_number,
            "sequence": record.get("sequence"),
            "thrower": record.get("thrower_character"),
            "thrower_position": record.get("thrower_position"),
            "receiver": record.get("receiver_character"),
            "receiver_position": record.get("receiver_position"),
            "intended_target_position": record.get("intended_target_position"),
            "target_base": record.get("target_base"),
            "peak_velocity_mph": record.get("peak_speed_mph"),
            "possession_to_release_s": record.get("hold_s"),
            "flight_time_s": round(flight_frames / 59.94, 4)
                if isinstance(flight_frames, (int, float)) else None,
            "buddy_throw": record.get("buddy_throw"),
            "outs_recorded": record.get("outs_recorded"),
            "measurement_status": "measured" if record.get("peak_speed_mph") is not None else "unavailable",
            "invalid_reason": None if record.get("peak_speed_mph") is not None else "no_valid_flight",
            "start_x": start[0], "start_y": start[1], "start_z": start[2],
            "end_x": end[0], "end_y": end[1], "end_z": end[2],
        })

    return {
        "batted_balls": [ball_row],
        "fielding": fielding_rows,
        "baserunning": runner_rows,
        "throws": throw_rows,
    }
