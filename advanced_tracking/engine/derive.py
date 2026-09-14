"""Turn a recorded tracking session into per-play fielding and baserunning metrics.

    python scripts/calibrate_player_tracking.py data/player_tracking/<session>
    python scripts/derive_player_metrics.py     data/player_tracking/<session>

Writes <session>.plays.jsonl -- one JSON object per batted ball -- and prints a
summary. Calibration must run first; this script refuses to guess which bytes
are the live position, because guessing wrong here produces plausible-looking
numbers rather than an error.

WHAT IS MEASURED, AND WHAT EACH ONE NEEDS TO BE HONEST

  reaction        Seconds from contact until a fielder's speed first clears a
                  threshold. Measured against the CONTACT frame, not the pitch,
                  because the fielder cannot react to a ball that has not been
                  hit.
  route efficiency  Straight-line distance from where a fielder started to
                  where they ended, over the distance they actually covered.
                  Only meaningful for the fielder who went after the ball, so
                  it is emitted only for fielders who moved more than a step.
  sprint speed    The fastest one-second window, which is the same definition
                  Statcast uses and is far more stable than an instantaneous
                  frame-to-frame maximum -- at 60 Hz a single-frame difference
                  is mostly quantisation noise.
  opportunity     Hang time available before the ball's first touch, paired
                  with the distance the fielder had to cover. Those two are the
                  inputs a catch-probability model is fitted on; this script
                  records the pair rather than asserting a probability.
  home to first   Contact until the batter-runner reaches the first-base bag.
  lead            Distance off the bag at the moment of contact.

COORDINATE FRAME. Everything is emitted in the BALL's frame -- the one
parkGeometry.js documents, with -Z toward centre field and +X toward first base
-- so fielder positions, runner paths, and batted-ball spray all overlay without
a second conversion. The actor structs use a different sign convention; the
calibration step measures it rather than assuming it.

BASE COORDINATES ARE MEASURED, NOT ASSUMED. An unoccupied runner slot parks
exactly on its bag, so the session itself says where first, second and third are
in this park. That beats deriving them from a nominal base path, and it means a
park with an unusual infield is handled without a special case.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from collections import Counter, deque
from pathlib import Path

from .tracking_io import Session, dumps_play, session_snapshot_builder

# Character ids are the game's own, extracted from the tracker's CHAR_ID_TO_NAME
# and cross-checked against mss_autoteam.py's captain list and its
# LAST_WRITABLE_CHAR_INDEX. Names make every output line readable; an id does
# not.
CHARACTER_NAMES = json.loads(
    (Path(__file__).resolve().parent / "mss_character_ids.json").read_text())


# charList holds 77 named characters at indexes 0..76 and every id from 77 up is
# a Mii, chosen from the Wii's own Mii list rather than the character select.
# The tracker's id table stops at 70, so a Mii used to come out as "char 78" --
# a name no other table in the repo has ever heard of, which is how one DK
# Jungle session recorded 52 plays for a player nothing could resolve. The site
# keys the whole family under "Mii"; scripts/mss_mii_map.json is what says WHICH
# Mii a given player means, and that is a roster question, not a capture one.
FIRST_MII_CHARACTER_ID = 77


def character_name(character_id: int) -> str:
    """The game's name for a character id.

    An EMPTY runner slot keeps whatever id last occupied it, so a name here is
    only meaningful when the slot's batting index is non-negative. Callers gate
    on the index, not on this.
    """
    named = CHARACTER_NAMES.get(str(character_id))
    if named:
        return named
    if character_id is not None and character_id >= FIRST_MII_CHARACTER_ID:
        return "Mii"
    return f"char {character_id}"

# A fielder is "reacting" once they exceed this, held for three frames so a
# single noisy sample cannot trip it.
REACTION_SPEED_UPS = 1.2
REACTION_HOLD_FRAMES = 3

# Above this a fielder is not running, they are being MOVED. The game has two
# distinct modes and they do not overlap much: characters run at a fixed
# per-character speed -- the whole batting order measured between 8.2 and 10.3
# u/s, and no runner in the first real session ever exceeded 10.2 -- while
# fielders converging on a ball are glided toward it at whatever speed the game
# needs, up to 29 u/s. Both are perfectly straight-line, constant-velocity
# motion frame to frame, so smoothness cannot tell them apart; only magnitude
# can. In the fielder step histogram the running peak dies out by 0.15 u/frame,
# there is a trough at 0.16-0.17, and the assist population starts at 0.18.
#
# Sprint Speed measured through the glide is measuring the game's fielding
# assist rather than the character, which is how one 8.4 u/s outfielder came out
# at 20.7. The glide is kept and reported -- it is a real thing that happens on
# the play, and a range model needs to know it happened -- but it is not speed.
#
# RUNNERS AND FIELDERS DO NOT SHARE A CEILING, and one threshold for both let
# the whole fielder glide population through. 11.0 was measured on runners and
# is right for them. Fielders run slower: across the sixteen archived sessions
# the fastest unassisted fielder run belongs to Yoshi at 8.45 u/s, and the value
# is very nearly a per-attribute constant -- Toadette, Baby Luigi, Baby Mario,
# Red Yoshi and Blue Yoshi all carry run_speed 80 and all five read exactly
# 8.15. Everything from 9 to 11 u/s on a fielder is glide, and the game glides
# the SLOW characters hardest to get them to the ball, so the contamination is
# not noise: 17.7% of a slow character's "own running" distance was above the
# fielder ceiling against 1.4% of a fast one's.
#
# Measured against the game's own run_speed attribute, that inverted the metric
# -- per-character fielder sprint speed correlated at -0.66, King K. Rool
# (run_speed 10) reading 10.20 u/s while Yoshi (90) read 8.45.
ASSIST_SPEED_UPS_RUNNER = 11.0
ASSIST_SPEED_UPS_FIELDER = 8.7

# A play is abandoned if it runs longer than this; something has gone wrong with
# the state flags and a 90-second "play" would poison every aggregate.
MAX_PLAY_SECONDS = 30.0

# A single-frame move larger than this is the between-play RESET, not a body.
# See Track.path_length -- these are real and expected, not corruption.
#
# Measured on the first real session, once plays are windowed by the game's own
# live-ball state: across 57 batted balls, every single-frame jump of 2u or more
# happened AFTER the ball was dead (547 of them), and every jump inside the live
# window was between 0.6u and 1.1u (70 of them, almost all pitcher follow-through
# and outfield dives). So 2u separates the reset from real play with a clear gap
# on both sides, and it is the reset this threshold exists to remove.
#
# The earlier value of 0.6 predates the live-ball window. It was doing two jobs
# at once -- excluding the reset AND ending the play -- and to end the play early
# enough it had to be tight enough to also throw away every dive. The window does
# the second job properly now, so this only has to do the first.
TELEPORT_UNITS = 2.0

# The game's own `game_state` while a batted ball is live. It leaves this value
# two frames before the ball snaps back to the mound, which makes it the exact
# dead-ball frame rather than an inference from how bodies moved.
LIVE_BALL_GAME_STATE = 2

# How near the plate the ball has to be when the swing flag rises for this to be
# a real batted ball. `ball_was_hit` also re-raises during the dead-ball
# aftermath -- a home-run trot, an inning change -- with the ball wherever it
# came to rest, and those replays were being recorded as extra batted balls with
# a bogus contact point. On the first real session the split is total: all 57
# genuine contacts had the ball within 0.9u of the origin, and all 17 replays had
# it between 45u and 109u away.
CONTACT_BALL_RADIUS_UNITS = 6.0

# How far the ball must move from where it sat during the swing animation to
# count as having been launched. Small, because it only has to beat float noise
# on a stationary ball.
LAUNCH_MOVE_UNITS = 0.05

# If the ball has not moved this long after a swing, there was no batted ball.
MAX_LAUNCH_DELAY_SECONDS = 6.0

# What the game's own two batted-ball flags mean, read off the first real
# session by cross-checking every value against the ball's measured trajectory.
#
#   fair_or_foul   -1  foul. Every one of the nine was outside the foul lines
#                       (|spray| 50.0 to 70.6 degrees) and none recorded an out.
#                    1  fair, still in flight.
#                    2  fair, and it has touched the ground.
#                    3  fair, and it was caught in the air. All 21 recorded
#                       exactly one out, and all sat inside the lines.
#   home_run_flag   1  home run. All 11 carried 96u to 109u and scored.
#                   2  would have been a home run, but it went foul. All four
#                      peaked above 31u of height outside the lines.
#
# Both are cleared on the frame the play window closes, so they can only be read
# from inside the live window.
FOUL_FLAG = -1
FAIR_CAUGHT_FLAG = 3
HOME_RUN_FLAG_FAIR = 1

# How close the ball has to be to a fielder's own coordinate to count as being
# in their hands. The match is exact when it happens, so this is loose by orders
# of magnitude and still unambiguous. Kept in step with the calibration.
LOCK_UNITS = 0.05

# The game's own frame counter ticks once per rendered frame at the Wii's NTSC
# rate, and all in-game motion is per-frame, so this converts ticks to
# GAME seconds. Override with --frame-rate if a session proves otherwise.
GAME_FRAME_RATE = 59.94

# How close a runner's closest approach must be for them to count as having
# reached the bag at all. This gates whether a split is reported; it does not
# set the time, so it can stay generous without biasing anything.
BAG_RADIUS_UNITS = 1.5

# Throw segmentation is deliberately conservative. The ball can teleport when
# game state resets, and a single bad memory sample must never become a 400 mph
# throw. Real throws must contain several plausible in-flight samples and end
# in a different fielder's possession.
MIN_THROW_SAMPLES = 3
MIN_THROW_SPEED_MPS = 2.0

# What a throw is rejected on is SMOOTHNESS, not a speed ceiling. Traced frame
# by frame, an ordinary throw has three parts: the ball snaps out of the
# thrower's hand to the release point, flies -- decelerating smoothly under drag,
# roughly half a percent per frame -- and snaps into the receiver's glove. A
# Buddy Throw adds a fourth: a second of frozen cutscene between release and
# launch. See BUDDY_THROW_MIN_FREEZE_FRAMES.
#
# The two snaps are the artefacts. On one measured LF->3B throw they read 238.5
# and 168.2 m/s against a real flight of 81.8 m/s decaying to 71.8. A speed cap
# removed them, but at 80 m/s it also clipped the flight itself -- that throw
# came out pinned to the cap, and its true launch speed was above it. Requiring
# each in-flight sample to be within a quarter of the one before it rejects both
# snaps by the property they actually violate, and leaves the flight intact.
FLIGHT_SPEED_RATIO = 0.25

# An absolute corruption guard, not a physical limit: the fastest flight sample
# measured in a real session is 81.8 m/s, so nothing real is anywhere near this.
MAX_THROW_SPEED_MPS = 150.0
METRES_PER_SECOND_TO_MPH = 2.2369362921
MPH_PER_FOOT_PER_SECOND = 3600.0 / 5280.0
FEET_PER_UNIT = 3.280839895013123


# HOW FAST A BATTED BALL CANNOT BE GOING. A rate, not a per-frame distance, so
# it means the same thing whatever the capture ran at. The hardest hit in 3,940
# archived batted balls is 119.0 mph, which is 53 units per second; this bound
# is more than twice that and still a tenth of what the failure it catches
# looks like.
#
# The failure is not a hit at all. When the ball feed goes stale the coordinates
# either freeze or jump to the fallback in a single frame, and both produce a
# launch measurement out of positions the ball never held. Two plays in the
# archive do it -- one reads 1311.8 mph off a 19.6-unit step between consecutive
# frames, the other 0.0 off a ball that never moved, and both have a ball height
# of exactly zero throughout. A stat file that prints either is harder to trust
# than one that says it could not measure them.
BALL_SPEED_LIMIT_UPS = 120.0


def measure_batted_ball_metrics(
    frames: list,
    *,
    fps: float = GAME_FRAME_RATE,
    landing: dict | None = None,
    first_touch: dict | None = None,
) -> dict:
    """Measure launch from the first two exposed post-contact movements.

    The launch transition itself can jump from the plate to the first exposed
    batted position, so that transition is intentionally excluded. Two
    consecutive movement vectors after it are required; a skipped game frame
    makes the measurement unavailable instead of stretching velocity across a
    gap and presenting the result as exact.
    """
    if len(frames) < 3:
        return {"measurement_status": "unavailable",
                "invalid_reason": "insufficient_post_contact_frames"}
    sample = frames[:3]
    timers = [item.get("timer") for item in sample]
    points = [item.get("ball") for item in sample]
    if (not all(isinstance(timer, int) for timer in timers)
            or timers[1] - timers[0] != 1
            or timers[2] - timers[1] != 1):
        return {"measurement_status": "unavailable",
                "invalid_reason": "frame_gap_at_launch"}
    if not all(point is not None and len(point) >= 3
               and all(math.isfinite(value) for value in point[:3])
               for point in points):
        return {"measurement_status": "unavailable",
                "invalid_reason": "invalid_ball_coordinates"}

    steps = [math.dist(points[index][:3], points[index + 1][:3]) for index in range(2)]
    if max(steps) * fps > BALL_SPEED_LIMIT_UPS:
        return {"measurement_status": "unavailable",
                "invalid_reason": "ball_position_discontinuity"}
    if not any(steps):
        return {"measurement_status": "unavailable",
                "invalid_reason": "ball_did_not_move"}

    seconds = 2.0 / fps
    vx = (points[2][0] - points[0][0]) / seconds
    vy = (points[2][1] - points[0][1]) / seconds
    vz = (points[2][2] - points[0][2]) / seconds
    horizontal = math.hypot(vx, vz)
    speed = math.sqrt(vx * vx + vy * vy + vz * vz)
    spray = math.degrees(math.atan2(vx, -vz))
    endpoint = landing or first_touch
    distance_units = endpoint.get("distance_units") if endpoint else None
    if distance_units is None and endpoint and endpoint.get("at"):
        at = endpoint["at"]
        distance_units = math.hypot(at[0], at[2])
    return {
        "exit_velocity_mph": round(speed * METRES_PER_SECOND_TO_MPH, 3),
        "launch_angle_deg": round(math.degrees(math.atan2(vy, horizontal)), 3),
        "spray_angle_deg": round(spray, 3),
        "spray_side": "first_base" if spray > 0 else "third_base" if spray < 0 else "center",
        "distance_ft": round(distance_units * FEET_PER_UNIT, 3)
            if distance_units is not None else None,
        "measurement_status": "measured",
        "invalid_reason": None,
    }
# A pitch is only measured once the ball is actually travelling. The frames
# between the counter rising and the release are the wind-up, and including
# them stretches the flight time and reports a slower pitch than was thrown.
PITCH_RELEASE_MOVE_UNITS = 0.05
# Below this many samples the flight is too short to say anything about break.
MIN_PITCH_SAMPLES = 8


def measure_pitch_metrics(samples: list, *, fps: float = GAME_FRAME_RATE) -> dict:
    """Speed and break from the ball's own flight, release to the plate.

    Both numbers are read off the same 60 Hz positions everything else here is
    read off, so a pitch and the ball it became cannot disagree about where the
    ball was.

    SPEED is the path the ball actually took over the GAME clock, not the wall
    clock. The emulator does not run at exactly 60 Hz, and timing a pitch by
    wall time therefore reports the emulator's speed as the pitcher's.

    BREAK is the largest signed departure from the straight line between
    release and the plate, measured separately in the horizontal and vertical
    axes and parameterised along the forward axis so it does not depend on how
    long the flight took. A pitch that did not deviate reads ~0; a curveball
    reads whichever way it curved. It is a measurement, not a pitch name: what
    to CALL a pitch needs a classifier this build deliberately does not ship.
    """
    usable = [
        (timer, point) for timer, point in samples
        if isinstance(timer, int) and point is not None and len(point) >= 3
        and all(math.isfinite(value) for value in point[:3])
    ]
    # Drop the wind-up: start at the first frame the ball had actually moved.
    start = 0
    for index in range(1, len(usable)):
        if math.dist(usable[index][1][:3], usable[index - 1][1][:3]) >= PITCH_RELEASE_MOVE_UNITS:
            start = index - 1
            break
    else:
        return {"measurement_status": "unavailable",
                "invalid_reason": "no_pitch_flight_observed"}
    flight = usable[start:]
    if len(flight) < MIN_PITCH_SAMPLES:
        return {"measurement_status": "unavailable",
                "invalid_reason": "insufficient_flight_samples"}

    timers = [timer for timer, _ in flight]
    # A dropped frame would be measured across rather than reported, exactly as
    # it would be for a batted ball.
    if timers[-1] - timers[0] != len(timers) - 1:
        return {"measurement_status": "unavailable",
                "invalid_reason": "frame_gap_in_flight"}
    elapsed = (timers[-1] - timers[0]) / fps
    if elapsed <= 0:
        return {"measurement_status": "unavailable",
                "invalid_reason": "no_elapsed_flight_time"}

    points = [point for _, point in flight]
    path = sum(math.dist(a[:3], b[:3]) for a, b in zip(points, points[1:]))
    first, last = points[0], points[-1]
    span = last[2] - first[2]
    if abs(span) < 1e-9:
        return {"measurement_status": "unavailable",
                "invalid_reason": "pitch_did_not_travel_forward"}
    horizontal, vertical = [], []
    for x, y, z in (point[:3] for point in points[1:-1]):
        ratio = (z - first[2]) / span
        horizontal.append(x - (first[0] + ratio * (last[0] - first[0])))
        vertical.append(y - (first[1] + ratio * (last[1] - first[1])))
    if not horizontal:
        return {"measurement_status": "unavailable",
                "invalid_reason": "insufficient_flight_samples"}

    feet_per_second = path * FEET_PER_UNIT / elapsed
    return {
        "velocity_mph": round(feet_per_second * MPH_PER_FOOT_PER_SECOND, 3),
        "horizontal_break_units": round(max(horizontal, key=abs), 4),
        "vertical_break_units": round(max(vertical, key=abs), 4),
        "flight_samples": len(flight),
        "flight_seconds": round(elapsed, 4),
        "measurement_status": "measured",
        "invalid_reason": None,
    }


TARGET_BASE_RADIUS_UNITS = 5.0
# Every routine base-targeted throw in the labelled Playroom game reached its
# receiver within 1.6 units of the bag. Across 361 targeted throws the 97.5th
# percentile is 2.01 units; confirmed bad throws run 2.99 and 4.47-4.58.
#
# 2.0 RATHER THAN 2.5, because this distance is very nearly a CONSTANT per
# (thrower, receiver, base) pair, not a noisy measurement -- Red Pianta threw to
# Blooper at first three times at 1.209 units each. So a pair that sits high
# sits high every time, and the gap between routine and off-target is far
# narrower than a pooled percentile suggests. In the 2026-08-31 Yoshi Park game
# Red Pianta reached second four times at 1.362-1.392 and reached Petey Piranha
# at home twice at 2.007 and 2.041; the operator identified that pairing as
# negative chemistry and the missing "off the bag" line as the bug. Same
# thrower, same session, same arm: only the receiver changed. Birdo->Waluigi
# (2.103, 2.112) is the same repeated-pair signature.
#
# This flags 15 of 361 archived throws instead of 7, and charges NO new errors:
# every one of the eight it adds fails the runner-gap and closing tests below,
# which is the distinction the operator drew -- the throw was off the bag, and
# nobody advanced on it.
RECEIVER_PULLED_OFF_BASE_UNITS = 2.0
# AN INACCURATE THROW, from the game's own numbers rather than from where the
# receiver ended up. THROW_AIM_ADDRESS (player_tracking_io.py) is where the game
# aimed the throw and THROW_DESTINATION_ADDRESS is where it actually sent it, so
# the gap between them is the error the game gave the throw -- known on the
# release frame, before the ball lands. Across all 2,383 archived throws
# (2026-09-11) the ball landed a median 0.20u from the destination. Of the 1,816
# aimed at a receiver already standing on the aim point, the 18 off target have
# a 2.22-7.66u gap and the other 1,798 never more than 0.94u; all 18 are between
# characters with bad chemistry, including all five the operator annotated. Bad
# chemistry is a chance, not a rule: 121 bad-chemistry throws have no gap at all.
#
# It separates what `receiver_pulled_off_base` cannot: Wario to King K. Rool at
# home caught him 2.2u off the plate and landed 0.06u from its aim point -- K. Rool
# was not on the plate, and the throw was fine.
#
# A throw to a receiver still moving is not judged: aim point and destination
# part there between neutral pairs too (218 of the 258 gaps over 3u), so the gap
# is not an error. A capture without a destination falls back to the landing.
THROW_AIM_STANDING_UNITS = 0.5
THROW_OFF_TARGET_UNITS = 1.5
THROWING_ERROR_RUNNER_DISTANCE_UNITS = 3.0
THROWING_ERROR_RUNNER_CLOSING_UNITS = 2.0
# A glove knock-out can briefly look like a very slow throw: possession changes
# from the base fielder to a nearby backup even though no target was selected.
# The labelled Yoshi Park example began 0.03 s after the contested arrival and
# moved at 10.6 mph; real missing-target relays in the archive are much faster.
LOOSE_BALL_MAX_SPEED_MPH = 20.0
LOOSE_BALL_MAX_TRANSITION_SECONDS = 0.15
PITCH_MOVE_UNITS_PER_FRAME = 0.02
PITCH_LOOKBACK_SECONDS = 3.0


class Track:
    """One actor's positions through a single play, in the ball's frame."""

    def __init__(self, name: str, character: int, index: int,
                 kind: str = "fielder"):
        self.name = name
        self.character = character
        self.index = index
        # "fielder" or "offense", straight off the actor. It selects the glide
        # threshold, which is not the same for the two -- see ASSIST_SPEED_UPS_*.
        self.kind = kind
        self.times = []
        self.points = []

    @property
    def assist_speed_ups(self) -> float:
        return (ASSIST_SPEED_UPS_RUNNER if self.kind == "offense"
                else ASSIST_SPEED_UPS_FIELDER)

    def add(self, t: float, point: tuple):
        self.times.append(t)
        self.points.append(point)

    def path_length(self) -> float:
        """Ground distance covered, excluding teleports.

        The live position field genuinely jumps -- on a change of sides the nine
        fielder objects are rewritten with the other team, and players are reset
        between plays. A single such jump is tens of units, which would swamp a
        real route and drive route efficiency toward zero, so steps that are not
        physically possible for a body are dropped rather than summed.

        A glide toward the ball IS covered ground and stays in here. It is
        excluded from speed, not from distance -- see run_path_length.
        """
        return sum(step for step in self.steps() if step <= TELEPORT_UNITS)

    def run_path_length(self) -> float:
        """Ground covered under the character's own running speed."""
        return sum(step for step, assisted in zip(self.steps(), self.assists())
                   if step <= TELEPORT_UNITS and not assisted)

    def assist_units(self) -> float:
        return sum(step for step, assisted in zip(self.steps(), self.assists())
                   if step <= TELEPORT_UNITS and assisted)

    def assists(self) -> list:
        """Per step, whether the game moved this actor faster than it can run."""
        ceiling = self.assist_speed_ups
        out = []
        for step, t0, t1 in zip(self.steps(), self.times, self.times[1:]):
            dt = t1 - t0
            out.append(dt > 0 and step / dt > ceiling)
        return out

    def steps(self) -> list:
        return [math.dist(a[::2], b[::2])
                for a, b in zip(self.points, self.points[1:])]

    def teleports(self) -> int:
        return sum(1 for step in self.steps() if step > TELEPORT_UNITS)

    def first_teleport_time(self):
        """The last honest instant before this actor is yanked somewhere.

        Deliberately the time of the sample BEFORE the jump, not after it. A
        window that ends at the post-jump sample still contains the jump: path
        length skips the impossible step but straight-line displacement does
        not, so route efficiency comes out above 1.0 -- which is arithmetically
        impossible and was the tell that this boundary was off by one frame.
        """
        for step, t in zip(self.steps(), self.times[:-1]):
            if step > TELEPORT_UNITS:
                return t
        return None

    def window(self, end_t: float) -> "Track":
        """A copy covering only up to `end_t`."""
        clipped = Track(self.name, self.character, self.index, self.kind)
        for t, point in zip(self.times, self.points):
            if t > end_t:
                break
            clipped.add(t, point)
        return clipped

    def displacement(self) -> float:
        if len(self.points) < 2:
            return 0.0
        return math.dist(self.points[0][::2], self.points[-1][::2])

    def speeds(self) -> list:
        """Ground speed per frame pair, in units per second."""
        out = []
        for (t0, p0), (t1, p1) in zip(zip(self.times, self.points),
                                      zip(self.times[1:], self.points[1:])):
            dt = t1 - t0
            step = math.dist(p0[::2], p1[::2])
            if dt > 0 and step <= TELEPORT_UNITS:
                out.append((t1, step / dt))
        return out

    def sprint_speed(self, window: float = 1.0) -> float:
        """Fastest average speed over any `window` seconds of the play."""
        if len(self.points) < 2:
            return 0.0
        best = 0.0
        start = 0
        cumulative = [0.0]
        for step, assisted in zip(self.steps(), self.assists()):
            counted = step <= TELEPORT_UNITS and not assisted
            cumulative.append(cumulative[-1] + (step if counted else 0.0))
        for end in range(len(self.times)):
            while self.times[end] - self.times[start] > window:
                start += 1
            span = self.times[end] - self.times[start]
            if span >= window * 0.75 and span > 0:
                best = max(best, (cumulative[end] - cumulative[start]) / span)
        return best

    def reaction(self) -> float | None:
        """Seconds from the start of the track until sustained movement.

        The hold requirement exists so one noisy frame cannot trip the
        threshold, but the answer is when the body STARTED moving, not when it
        had been moving for three frames. Returning the end of the run would
        add a flat REACTION_HOLD_FRAMES/fps to every reaction in the dataset --
        a bias of the same size as the differences between characters, which is
        the whole thing this metric is for.
        """
        run = 0
        run_started = None
        for t, speed in self.speeds():
            if speed >= REACTION_SPEED_UPS:
                if run == 0:
                    run_started = t
                run += 1
                if run >= REACTION_HOLD_FRAMES:
                    return round(run_started - self.times[0], 4)
            else:
                run = 0
        return None


# Route efficiency on someone who took two steps is dominated by idle jitter and
# is trivially 1.0. Measured on the first real session, a 1u threshold made 247
# of 467 routes exactly 1.000; requiring a real route is what makes the number
# mean anything.
ROUTE_MIN_PATH_UNITS = 5.0


# WHY A FREEZE WITHHOLDS RATHER THAN ADJUSTS. A glide can be subtracted: the
# game moved the body, the frames it moved it are identifiable, and the speed
# over the remaining frames is still the character's own. A freeze cannot. It
# does not add path length, so route efficiency is not inflated arithmetically
# -- it changes what the fielder was doing. He is held for two seconds, the
# ball goes somewhere else, and the route he then runs is a route to a
# different problem. Measured over peach_ice_garden-20260902T151327Z the 18
# frozen fielder-plays have a median route efficiency of 0.684 against 0.971
# for the other 552, and PA 72's 0.2552 is not Toadsworth taking a bad line, it
# is Toadsworth standing still.
#
# Reaction is worse: a fielder frozen at contact cannot react at all, so the
# number measures the freeze.
#
# Neither is recoverable from the capture, so neither is emitted, and
# `frozen_frames` says why. The same rule as the moved_threshold below: a
# number that would not be honest is not published.
def summarise_track(track: Track, moved_threshold: float = ROUTE_MIN_PATH_UNITS,
                    frozen_frames: int = 0) -> dict:
    path = track.path_length()
    sprint_mps = track.sprint_speed()
    out = {
        "name": track.name,
        "character_id": track.character,
        "character": character_name(track.character),
        "batting_index": track.index,
        "start": [round(v, 3) for v in track.points[0]] if track.points else None,
        "end": [round(v, 3) for v in track.points[-1]] if track.points else None,
        "path_units": round(path, 3),
        "run_path_units": round(track.run_path_length(), 3),
        "assist_units": round(track.assist_units(), 3),
        "assist_frames": sum(1 for assisted in track.assists() if assisted),
        "displacement_units": round(track.displacement(), 3),
        "sprint_speed_ups": round(sprint_mps, 3),
        "sprint_speed_fps": round(sprint_mps * 3.280839895, 3),
        "bolt": sprint_mps * 3.280839895 >= 30.0,
        "teleports": track.teleports(),
    }
    if path >= moved_threshold and not frozen_frames:
        out["reaction_s"] = track.reaction()
        # Route efficiency is only defined for someone who actually went
        # somewhere. On a fielder who shuffled two steps the ratio is dominated
        # by idle jitter and reads like a terrible route.
        out["route_efficiency"] = round(track.displacement() / path, 4)
    return out


def locked_fielder(snapshot: dict) -> str | None:
    """Return the defensive position whose actor is carrying the ball.

    Coordinates say WHO. The game has to say that anyone is holding it at all,
    because a fly ball passing directly over an outfielder's head matches their
    x and z exactly -- on three home runs in the first real session that was
    recorded as the fielder taking possession of a ball that had already left
    the park.

    It says so two ways, and both are needed. `ball_holder` names a carrier, and
    `fair_or_foul` reaching its caught-in-the-air value is the game declaring the
    catch. A robbery at the wall sets the second and not the first: on a
    first-inning catch at Bowser Castle the ball sat exactly on the centre
    fielder at 4.33u for ninety frames with the call already made, and
    `ball_holder` stayed at -1 throughout. Requiring the byte alone threw that
    catch away entirely.

    Neither admits the fly-over: those balls are still in flight, so the call is
    "fair, in the air" rather than "caught", and nobody is carrying them.
    """
    bx, by, bz = snapshot["ball"]
    if bx == 0.0 and by == 0.0 and bz == 0.0:
        return None
    held = int(snapshot["state"].get("ball_holder", -1)) >= 0
    called_caught = int(snapshot["state"].get("fair_or_foul", 0)) == FAIR_CAUGHT_FLAG
    if not held and not called_caught:
        return None
    # Horizontal only. A fielder's tracked height is pinned to 0.00 in every
    # frame of the first real session -- 19,326 live frames, including the 2,180
    # where the game's own airborne flag was set -- so the actor position field
    # simply does not carry height, and comparing the ball's height against it
    # was comparing it against a constant.
    #
    # That rejected every catch made off the ground. Three catch cutscenes in
    # that session hold the ball at 18u to 24u while the fielder reads 0, and
    # the touch was only recorded once they landed: 4.52s became 6.91s, 5.69s
    # became 8.17s, 4.44s became 6.86s. A robbed home run is the hardest
    # opportunity there is, and it was being recorded as a seven-second can of
    # corn. `ball_holder` is what confirms possession here, and it is exact.
    matches = []
    for name, actor in snapshot["actors"].items():
        if actor["kind"] != "fielder":
            continue
        px, _, pz = actor["pos"]
        horizontal = math.dist((px, pz), (bx, bz))
        if horizontal <= LOCK_UNITS:
            matches.append((horizontal, name))
    return min(matches)[1] if matches else None


# A fielder who holds the ball and MOVES is not necessarily carrying it. A ball
# hit hard enough drives the fielder who caught it backwards, and that is an
# impulse rather than a run: it is fastest on the very first frame, it only ever
# slows down, and it travels in a dead-straight line because nothing is steering
# it. A run has to accelerate from a standstill, and it curves.
#
# Measured over dk_jungle-20260902T171957Z: of the 30 ball-holding moves that
# clear the one-unit narrative floor, 14 have this shape and every one of them
# reads displacement/path = 1.000, against 0.28-0.99 for the other 16. The two
# the operator flagged -- "did not use ball dash to run, they got pushed back by
# the power of the hit" -- are both in the 14. Calling those a carry credited
# Ball Dash to a fielder who was standing still being shoved.
KNOCKBACK_STRAIGHTNESS = 0.97
KNOCKBACK_SPEED_TOLERANCE_UPS = 0.05


def classify_carry_motion(track: "Track") -> str:
    """'knockback' for a decaying impulse, 'carry' for self-directed movement.

    Deliberately biased toward 'carry': every test below has to pass before a
    move is called a knockback, so a fielder who is shoved and then runs comes
    out a carry. Under-calling this costs a sentence; over-calling it would
    delete a real Ball Dash observation.
    """
    speeds = [speed for _, speed in track.speeds()]
    moving = [speed for speed in speeds if speed > KNOCKBACK_SPEED_TOLERANCE_UPS]
    if len(moving) < 3:
        return "carry"
    # Sped up at any point -- only a body under its own power does that.
    if any(later > earlier + KNOCKBACK_SPEED_TOLERANCE_UPS
           for earlier, later in zip(moving, moving[1:])):
        return "carry"
    path = track.path_length()
    if path <= 0 or track.displacement() / path < KNOCKBACK_STRAIGHTNESS:
        return "carry"
    return "knockback"


def detect_possession_carries(frames: list, contact_t: float,
                              live_end: float) -> list:
    """Measure each contiguous interval in which a fielder carries the ball.

    This is the activation evidence for Ball Dash. The ability is passive, so
    the meaningful event is not a separate action byte: it is a mapped Ball
    Dash character actually moving while the game's possession state and the
    ball-to-actor coordinate lock both agree they hold the ball.
    """
    live = [snapshot for snapshot in frames
            if snapshot["t"] - contact_t <= live_end + 1e-9]
    by_timer = {snapshot["timer"]: snapshot for snapshot in live}

    def ball_speed_before(timer: int):
        """The loose ball's horizontal speed on its way into a possession frame.

        None when the two frames before possession are not both a free ball --
        a relay taken straight out of another glove has no approach to measure.
        """
        before, earlier = by_timer.get(timer - 1), by_timer.get(timer - 2)
        if before is None or earlier is None:
            return None
        if any(int(snapshot["state"].get("ball_holder", -1)) >= 0
               for snapshot in (before, earlier)):
            return None
        elapsed = before["t"] - earlier["t"]
        if elapsed <= 0:
            return None
        return round(math.dist(before["ball"][::2], earlier["ball"][::2]) / elapsed, 3)

    carries = []
    track = None
    start_frame = None
    end_frame = None
    airborne_start = False

    def finish():
        nonlocal track, start_frame, end_frame, airborne_start
        if track is not None and len(track.points) >= 2:
            distance = track.path_length()
            if distance >= 0.1:
                speeds = [speed for _, speed in track.speeds()]
                carries.append({
                    "by": track.name,
                    "character_id": track.character,
                    "character": character_name(track.character),
                    "start_frame": start_frame,
                    "end_frame": end_frame,
                    "start_t": round(track.times[0], 4),
                    "end_t": round(track.times[-1], 4),
                    "distance_units": round(distance, 3),
                    "displacement_units": round(track.displacement(), 3),
                    "peak_speed_ups": round(max(speeds), 3) if speeds else 0.0,
                    # Whether the fielder moved the ball or the ball moved the
                    # fielder. Ball Dash may only be read off a "carry".
                    "motion": classify_carry_motion(track),
                    # Still in the air on the frame they took the ball, which
                    # is what separates a leaping catch's follow-through from
                    # an impulse delivered to a fielder on their feet.
                    "airborne_at_start": airborne_start,
                    # How fast the ball was travelling when they took it. A
                    # ball can only shove a fielder if it arrives with
                    # something behind it; see KNOCKBACK_BALL_SPEED_UPS.
                    "ball_speed_at_start_ups": ball_speed_before(start_frame),
                })
        track = None
        start_frame = None
        end_frame = None
        airborne_start = False

    for snapshot in live:
        owner = locked_fielder(snapshot)
        if owner is None:
            finish()
            continue
        actor = snapshot["actors"][owner]
        if track is None or track.name != owner:
            finish()
            track = Track(owner, actor["character"], actor["index"],
                          actor.get("kind", "fielder"))
            start_frame = snapshot["timer"]
            airborne_start = bool(actor.get("airborne"))
        track.add(snapshot["t"] - contact_t, actor["pos"])
        end_frame = snapshot["timer"]
    finish()
    return carries


# WHAT DROVE THE FIELDER BACK. `classify_carry_motion` was built out of the
# operator's own reading of two plays -- "they got pushed back by the power of
# the hit" -- and then the sentence refused to say so, on the grounds that a
# stadium hazard looks the same and the capture holds no field separating them.
# It holds three.
#
# A knockback is an impulse delivered to a fielder who is HOLDING THE BALL, and
# possession begins on an exact frame. Across mario_stadium-20260904T000419Z's
# 17 knockbacks, 16 begin on the frame that fielder first touched the batted
# ball and the 17th begins on the frame a throw arrived in their glove --
# frame-exact, not close. A hazard is under no such obligation: it reaches a
# fielder whenever it gets there, and it sets `knockdown_flag`, which fired zero
# times all game.
#
# So the ball is named when it demonstrably arrived at that instant, the
# knockdown flag outranks it when it fired, and anything else stays unnamed --
# which is the honest answer for the case this was written to protect.
#
# A LEAP OUTRANKS THE BALL. A fielder who catches in mid-air keeps travelling
# after the catch, and that drift passes every knockback test: fastest on the
# first frame, never speeding up, dead straight. It is their own jump. The
# operator flagged one at Peach Ice Garden -- "think it was his momentum of
# running and jumping to the ball" -- and the airborne flag agrees frame-exact:
# of the 25 knockbacks in peach_ice_garden-20260907T234715Z, 3 were airborne on
# the possession frame and 22 were not, and those 3 are the three smallest
# displacements in the set (1.4, 1.7, 1.9 units against a spread that runs to
# 26.8). The jump began before the ball arrived, so where both are true the
# capture cannot separate them and the leap is the one that was already
# happening.
# AND THE GAME MOVES FIELDERS BY ITSELF, which looks exactly like a knockback.
# On 09-11 PA50 Bowser caught a liner, was floored two frames later, the ball
# rolled out of his glove and sat there, and 104 frames later he took possession
# of it again from 4.18u away: the ball snapped into his glove and the game slid
# him 1.65u onto its spot, 5.39 u/s on the frame before and 16.98 u/s on the
# frame after. That passes every knockback test -- fastest first, only slowing,
# dead straight -- with nothing to deliver it, because the ball it names was
# crawling at 0.93 u/s. PA23's ball arrived at 13.0 u/s and drove Toadsworth
# back 4.6u, which is the real thing.
#
# This bound sits between the two measured cases and is PROVISIONAL: the ball
# speed is a new field, so the archive has not been re-derived with it yet.
KNOCKBACK_BALL_SPEED_UPS = 4.0


def name_carry_impulses(carries: list, first_touch: dict | None, throws: list,
                        knocked_frames: dict) -> None:
    """Record what delivered each knockback, in place."""
    for carry in carries:
        start = carry["start_frame"]
        speed_in = carry.get("ball_speed_at_start_ups")
        arrived_fast = None if speed_in is None else speed_in >= KNOCKBACK_BALL_SPEED_UPS
        on_first_touch = bool(first_touch and first_touch.get("by") == carry["by"]
                              and first_touch.get("frame") == start)
        # A SHOVE THE GAME CUT SHORT. The track ends when the ball leaves the
        # glove, so a fielder floored just after the catch keeps only the first
        # frames of the shove the ball gave him -- too few for
        # classify_carry_motion, which needs three moving frames, and far under
        # the narrative's one-unit floor. 09-11 PA50: Bowser went from a
        # standstill to 12.8 u/s on the frame he caught a ball arriving at 19.7
        # u/s, moving in the ball's own direction of travel, and was floored
        # two frames later. Jason watched it -- "it was the hit that
        # drove him back", "yes it was at the moment of the catch" -- and the
        # derivation had thrown it away and described a glide 1.8 s later.
        #
        # ONE play in the whole archive meets this, so it is deliberately
        # narrow and PROVISIONAL at n=1: the catch frame, a ball measured
        # arriving fast, and a knockdown of that same fielder within two frames
        # of the track's end. The other 17 carries a knockdown ends run 17
        # frames or longer and classify on their own shape; the slow ones
        # (0.5-2.9 u/s) fail the ball-speed test.
        cut_short = [frame for frame in knocked_frames.get(carry["by"] + "@frame", [])
                     if carry["end_frame"] < frame <= carry["end_frame"] + 2]
        interrupted = bool(carry.get("motion") != "knockback" and cut_short
                           and on_first_touch and arrived_fast)
        if carry.get("motion") != "knockback" and not interrupted:
            continue
        if interrupted:
            # The distance is an artefact of the interruption; the speed is the
            # measurement, so the sentence leads with it.
            carry["truncated_by_knockdown"] = True
            carry["impulse"] = "batted_ball"
            carry["impulse_frame"] = start
            continue
        floored = [frame for frame in knocked_frames.get(carry["by"] + "@frame", [])
                   if carry["start_frame"] <= frame <= carry["end_frame"]]
        arrivals = [throw for throw in throws
                    if throw.get("receiver_position") == carry["by"]
                    and throw.get("arrival_frame") == start]
        touched = bool(first_touch and first_touch.get("by") == carry["by"]
                       and first_touch.get("frame") == start)
        speed = carry.get("ball_speed_at_start_ups")
        crawling = None if speed is None else speed < KNOCKBACK_BALL_SPEED_UPS
        if floored:
            carry["impulse"] = "knockdown"
        elif carry.get("airborne_at_start"):
            carry["impulse"] = "leap"
        elif arrivals:
            carry["impulse"] = "thrown_ball"
        # An unmeasurable approach keeps the old answer; only a ball MEASURED
        # to be crawling loses the right to be called the thing that shoved them.
        elif touched and crawling is not True:
            carry["impulse"] = "batted_ball"
        elif crawling:
            carry["impulse"] = "possession_glide"
        else:
            carry["impulse"] = None
        carry["impulse_frame"] = start


def closest_target_base(point: tuple, bags: dict, home: tuple | None = None) -> str | None:
    candidates = {
        "first": bags.get("R1"), "second": bags.get("R2"),
        "third": bags.get("R3"), "home": home,
    }
    distances = []
    for name, bag in candidates.items():
        if bag is None:
            continue
        distances.append((math.dist(point[::2], bag[::2]), name))
    if not distances:
        return None
    distance, name = min(distances)
    return name if distance <= TARGET_BASE_RADIUS_UNITS else None


# How long the play window is held open past the game's own dead-ball call.
# Every detector windows at `live_end`, so these frames feed exactly one thing:
# `final_bases_ran`, which has to see a runner's count settle through the actor
# teardown. Measured against the full archive -- 445 plays across five parks --
# as the smallest tail that leaves every derived field unchanged, with the
# widest real advance-after-dead-ball at 62 frames.
DEAD_BALL_TAIL_FRAMES = 90

# The ball rests at y=0 but bounces off the ground to a minimum near 0.5, so the
# landing is the first low turning point rather than a zero crossing. 1.5u sits
# above every measured bounce minimum and below the flight it came out of.
LANDING_HEIGHT_UNITS = 1.5
FIELDING_ACTION_MISPLAYS = (2, 3)
# 4 = A STAR-SWING BALL FORCING THE FIRST CONTACT. Every action-4 window ever
# recorded -- two in this session on Blue Yoshi, one at Peach Ice Garden on
# Bowser Jr. -- was hit by MARIO, and by nobody else across five sessions of
# plays. The operator names the mechanic directly: Mario's Fire Swing turns the
# ball into a fireball the first fielder to reach it cannot hold. That is the
# same shape as Yoshi's egg, which the game gives its own code (5), and it is
# handled the same way: the fielder has no play to make, so the misplay is
# forced and no error may be charged for it.
FIELDING_ACTION_STAR_BALL = 4
FIELDING_ACTION_YOSHI_EGG = 5
FIELDING_ACTION_BUDDY_HANDOFF = 7

# Both codes are a batter's star ball forcing a contact the fielder cannot
# hold. They stay separate codes because they are separate mechanics with
# separate names; they are grouped wherever the question is "did the fielder
# have a play to make".
FIELDING_ACTION_FORCED = (FIELDING_ACTION_STAR_BALL, FIELDING_ACTION_YOSHI_EGG)

# HOW FAR A FIELDER CAN POSSIBLY REACH, in units, measured in three dimensions
# from the tracked body centre to the ball.
#
# The action byte is an animation state, not a claim about the ball: it fires
# for one frame as a fly ball sails overhead, and the tracker .exe turns that
# single frame into "X bobbled the ball!". Two such windows exist in the
# archive -- Donkey Kong with the ball 34.9u above him at DK Jungle, Baby DK
# with it 26.0u above him at Bowser Castle -- and in both the ball was a home
# run that no one came near.
#
# The bound is measured, not assumed: across 233 contacts the game's own
# `last_contact_fielder` confirms, the largest separation at the moment of
# contact is 6.29u, and the largest at a MISS the capture recorded is 8.71u.
# Twelve units is comfortably outside everything a fielder has ever actually
# touched and far inside the two overhead balls, so a window beyond it is an
# animation that coincided with a ball, not a play on one.
FIELDING_REACH_LIMIT_UNITS = 12.0

# The value `buddy_jump_flag` takes while the game runs a Buddy Jump. See
# detect_buddy_jumps for why the other observed value is not one.
BUDDY_JUMP_FLAG_ACTIVE = 2

# How far back to look when asking whether a runner standing at a base ARRIVED
# there with the throw. Half a second: long enough that a runner at a measured
# 9 u/s has covered about 4.5 units, short enough that it stays inside the play.
CLOSE_PLAY_LOOKBACK_FRAMES = 30

# A CLOSE PLAY IS A DIFFERENCE IN TIME, NOT IN DISTANCE, and reading it off the
# gap in units was wrong in both directions.
#
# A runner is at the bag when they are inside this radius -- every runner
# standing on a measured bag in the archive sits 0.78-0.82 units from its
# centre. The frame they first get inside it and the frame the ball arrives are
# both measured, and `margin_s` is the difference: negative when the runner beat
# the throw, positive when the throw beat the runner.
#
# WHY THE OLD READING FAILED. Distance-at-arrival conflates the margin with the
# runner's speed. A runner at 12 u/s is four units from first a third of a
# second before reaching it, so every close play at first -- which is always
# taken at full speed -- failed a three-unit gate; and a runner who beat the
# throw two seconds ago is 0.8 units from the bag standing still, so the
# closing-speed test that was there to exclude them excluded every runner who
# WON a close play as well. In peach_ice_garden-20260907T234715Z the two gates
# selected disjoint sets: five throws inside three units, six with a runner
# still closing, no overlap, and the game narrated zero close plays. Measured in
# time the same session has five, at +0.18, +0.33, +0.37, +0.42 and -0.47 s.
RUNNER_AT_BASE_UNITS = 1.5

# How far past the ball's arrival to keep looking for the runner reaching the
# bag. A runner beaten by the throw still finishes the stride, and that finish
# is what makes the margin measurable on a play the runner LOST -- four of the
# five above are outs by under half a second.
CLOSE_PLAY_REACH_SEARCH_FRAMES = 120


# HOW THE APPROACH TO THE BALL IS CLASSIFIED (`catch_type`, actor +0x2AC).
#
# The fielding AI picks its approach before the ball arrives, writes the choice
# to this byte, and clears it on the frame the glove closes. The values were
# read off three archived sessions (wario_stadium, mario_stadium,
# peach_ice_garden -- 433 windows) and one live one:
#
#   1  ordinary catch. The fielder is standing still for the whole window.
#   2  catching a THROW. The countdown at +0x2A5 equals the frames of throw
#      flight still to come, and every window ends on a receiver's catch.
#   3  DIVE. In all 55 of them the fielder is still running at full step at the
#      moment of the catch and keeps sliding for several frames afterwards,
#      which no type-1 window ever does. Those that end in possession take the
#      ball at a height of ~0; those that do not are the balls that carry
#      through to the outfielder behind.
#   5  CLAMBER. The fielder climbs the wall; this is not a dive.
#   6  LEAP. This is the only value the airborne flag at +0x22E accompanies
#      (30/30), and it is what the 18u-24u catches at the wall are.
#   7  a dash with real displacement (~2.1u) that is not yet resolved to a
#      named mechanic, so it is reported with its raw code and nothing else.
#
# THE AIRBORNE FLAG IS NOT A DIVE DETECTOR and never was: it fires for 6 and
# never for 3. That is why every dive in the archive read as an ordinary play.
#
# 0x2B3 and 0x2B4 both read 2 for exactly the type-3 windows and 0 or 1 for
# every other type across all four sessions, so they remain a free cross-check
# if this classification is ever doubted.
#
# Type 3 says a fielder used the single dive/special-action input. The character
# mapping distinguishes an ordinary dive from Super Dive, Tongue Catch,
# Suction Catch, Magical Catch, Piranha Catch, Keeper Catch, and the three Bro
# throws. The derivation keeps the raw approach and tracker_abilities.mjs names
# the character-specific animation.
CATCH_TYPE_ORDINARY = 1
CATCH_TYPE_THROW = 2
CATCH_TYPE_DIVE = 3
CATCH_TYPE_CLAMBER = 5
CATCH_TYPE_LEAP = 6
CATCH_TYPE_NAMES = {
    CATCH_TYPE_ORDINARY: "ordinary",
    CATCH_TYPE_THROW: "throw",
    CATCH_TYPE_DIVE: "dive",
    CATCH_TYPE_CLAMBER: "clamber",
    CATCH_TYPE_LEAP: "leap",
}

# How many frames after a window closes an event may still be attributed to it.
# The byte clears on the same frame the catch registers, so the run ends one
# frame BEFORE its own possession event; two frames of slack covers that
# without reaching the next window.
CATCH_TYPE_ATTRIBUTION_FRAMES = 2


def detect_catch_approaches(frames: list, contact_t: float) -> list:
    """One record per contiguous non-zero `catch_type` window per fielder."""
    approaches = []
    active = {}

    def finish(name, last):
        window = active.pop(name, None)
        if window is None:
            return
        window["end_frame"] = last["timer"]
        window["end_t"] = round(last["t"] - contact_t, 4)
        approaches.append(window)

    previous = None
    for snapshot in frames:
        for name, actor in snapshot["actors"].items():
            if actor.get("kind") != "fielder":
                continue
            code = actor.get("catch_type") or 0
            window = active.get(name)
            if window is not None and window["catch_type"] != code:
                finish(name, previous or snapshot)
                window = None
            if code and window is None:
                active[name] = {
                    "by": name,
                    "character_id": actor["character"],
                    "character": character_name(actor["character"]),
                    "catch_type": code,
                    "approach": CATCH_TYPE_NAMES.get(code, "unresolved"),
                    "dive": code == CATCH_TYPE_DIVE,
                    "leap": code == CATCH_TYPE_LEAP,
                    "start_frame": snapshot["timer"],
                    "start_t": round(snapshot["t"] - contact_t, 4),
                    "airborne_frames": 0,
                }
                window = active[name]
            if window is not None:
                window["frames"] = window.get("frames", 0) + 1
                if actor.get("airborne"):
                    window["airborne_frames"] += 1
        previous = snapshot
    for name in list(active):
        finish(name, previous)
    approaches.sort(key=lambda window: (window["start_frame"], window["by"]))
    return approaches


def approach_at(approaches: list, name: str, frame: int) -> dict | None:
    """The approach window this fielder was in on (or just before) `frame`."""
    best = None
    for window in approaches:
        if window["by"] != name:
            continue
        if not (window["start_frame"] <= frame
                <= window["end_frame"] + CATCH_TYPE_ATTRIBUTION_FRAMES):
            continue
        if best is None or window["start_frame"] > best["start_frame"]:
            best = window
    return best


def approach_fields(window: dict | None) -> dict:
    """The approach keys carried on a fielding event."""
    if window is None:
        return {
            "catch_type": None,
            "approach": None,
            "dive": False,
            "leap": False,
        }
    return {
        "catch_type": window["catch_type"],
        "approach": window["approach"],
        "dive": window["dive"],
        "leap": window["leap"],
        "approach_frames": window.get("frames", 0),
        "approach_start_frame": window["start_frame"],
    }

def _ball_motion_change(frames: list, index: int) -> dict:
    """Horizontal turn and speed around a fielding contact.

    The action enum is authoritative; this physics is retained as an audit
    trail. A boot can deaden a ball without turning it, so turn angle must not
    be used as the classifier.
    """
    if index < 8 or index + 8 >= len(frames):
        return {}
    before_a, before_b = frames[index - 8], frames[index - 4]
    after_a, after_b = frames[index + 4], frames[index + 8]

    def velocity(first, second):
        dt = second["t"] - first["t"]
        if dt <= 0:
            return None
        return ((second["ball"][0] - first["ball"][0]) / dt,
                (second["ball"][2] - first["ball"][2]) / dt)

    incoming = velocity(before_a, before_b)
    outgoing = velocity(after_a, after_b)
    if incoming is None or outgoing is None:
        return {}
    speed_in = math.hypot(*incoming)
    speed_out = math.hypot(*outgoing)
    if speed_in <= 1e-9 or speed_out <= 1e-9:
        turn = None
    else:
        cosine = max(-1.0, min(1.0,
            (incoming[0] * outgoing[0] + incoming[1] * outgoing[1])
            / (speed_in * speed_out)))
        turn = math.degrees(math.acos(cosine))
    return {
        "incoming_speed_ups": round(speed_in, 3),
        "outgoing_speed_ups": round(speed_out, 3),
        "trajectory_turn_degrees": round(turn, 3) if turn is not None else None,
    }


def name_knocked_loose_throws(throws: list, knockdowns: list) -> None:
    """A "throw" released on the frame its own thrower was floored, renamed in place.

    The game drops the ball out of the glove on the frame the knockdown flag
    rises, and a floored fielder throws nothing. Across 2,408 archived throws a
    release lands exactly on its own thrower's knockdown six times, and each
    one measured as a slow "throw" to whoever picked the ball up. One launched
    three frames BEFORE its thrower went down is a real throw and keeps its
    name.
    """
    for throw in throws:
        knock = next((entry for entry in knockdowns
                      if entry.get("by") == throw["thrower_position"]
                      and entry.get("frame") == throw["release_frame"]), None)
        if knock is None:
            continue
        throw["is_throw"] = False
        throw["event_type"] = "knocked_loose"
        throw["is_relay"] = False
    # A throw that follows nothing but a ball knocked loose was not relayed.
    for index, throw in enumerate(throws):
        if throw.get("is_relay") and throw.get("is_throw") is not False:
            throw["is_relay"] = any(earlier.get("is_throw") is not False
                                    for earlier in throws[:index])


def detect_fielding_action_events(frames: list, contact_t: float,
                                   live_end: float,
                                   first_touch: dict | None,
                                   action_code: int) -> list:
    """Classify one event per contiguous action window.

    `last_contact_fielder` at 0x900D9524 is the authority for physical contact:
    it idles at -1 and names a fielder by pointer-table order. Across the full
    labelled archive it names 16/16 ordinary boots (including the two 4.6u/5.1u
    Brown Kritter dives), 8/8 egg contacts, 50/50 intentional teammate tosses,
    and 286/286 clean possessions, while never naming Shy Guy's unrelated
    action-3 attempt.

    Action 3 without that actor transition is therefore a confirmed miss.
    Action 2 has no general labelled miss and does not consistently set the
    scalar, so absence remains explicit `unknown` here. After the independent
    approach byte is attached below, an action-2 DIVE with no contact actor can
    be resolved as a miss: the operator-confirmed DK example establishes that
    exact combination. Centre-to-ball distance remains audit context only.
    """
    cutoff = first_touch["t"] if first_touch else live_end
    active = {}
    events = []

    first_snapshot = frames[0] if frames else {"actors": {}}
    order = [name for name, actor in first_snapshot["actors"].items()
             if actor["kind"] == "fielder"]
    actor_indices = {name: index for index, name in enumerate(order)}
    mechanic = {
        2: "ordinary", 3: "ordinary", 4: "star_ball",
        5: "egg", 7: "buddy",
    }.get(action_code, "unknown")

    def finish(name):
        window = active.pop(name, None)
        if window is None:
            return
        contact = window["contact"]
        closest = window["closest"]
        if closest is None:
            return
        distance, t, index, snapshot, actor = contact or closest
        reach = window["closest_reach"]
        if contact is not None:
            ball_contact = "confirmed"
            confidence = "high"
            source = "last_contact_fielder"
        elif reach > FIELDING_REACH_LIMIT_UNITS:
            # The ball was never close enough for contact to be physically
            # possible, so this window is an animation that happened while a
            # ball was in the air somewhere else.
            ball_contact = "missed"
            confidence = "high"
            source = "ball_never_within_reach"
        elif action_code == 3:
            ball_contact = "missed"
            confidence = "high"
            source = "action_without_contact_actor"
        else:
            ball_contact = "unknown"
            confidence = "low"
            source = "no_contact_actor"
        events.append({
            "event_type": "fielding_action",
            "fielding_attempt": True,
            "ball_contact": ball_contact,
            # Code 7 deliberately takes control of the ball long enough to
            # redirect it at a teammate. It is a quick secure-and-pass even
            # when no chemistry Buddy Throw state is present.
            "secured": action_code == FIELDING_ACTION_BUDDY_HANDOFF
                       and ball_contact == "confirmed",
            "mechanic": mechanic,
            # A physical boot is not automatically an official scoring error.
            "official_error": None,
            "confidence": confidence,
            "contact_source": source,
            "t": round(t, 4),
            "frame": snapshot["timer"],
            "action_start_frame": window["start_frame"],
            "action_end_frame": window["end_frame"],
            "by": name,
            "character_id": actor["character"],
            "character": character_name(actor["character"]),
            "at": [round(value, 3) for value in actor["pos"]],
            "ball_at": [round(value, 3) for value in snapshot["ball"]],
            "distance_units": round(distance, 3),
            "closest_distance_units": round(closest[0], 3),
            # The horizontal distances above ignore the ball's height, which is
            # the only axis that separates a dive off the turf from a home run
            # sailing 35 units overhead. This one does not.
            "closest_reach_units": round(reach, 3),
            "within_reach": reach <= FIELDING_REACH_LIMIT_UNITS,
            "action_code": action_code,
            "contact_fielder": snapshot["state"].get("contact_fielder", -1),
            "last_contact_fielder": snapshot["state"].get(
                "last_contact_fielder", -1),
            "fielding_contact_counter": actor.get("contact_counter", 0),
            # The fielder position has no usable height, but this actor flag is
            # still useful attempt context. It is deliberately not called an
            # error decision: a hard play can be missed without being an error.
            "airborne_near_contact": any(
                nearby["actors"].get(name, {}).get("airborne")
                for nearby in frames
                if abs(nearby["t"] - snapshot["t"]) <= 1.0
            ),
            **_ball_motion_change(frames, index),
        })

    for index, snapshot in enumerate(frames):
        t = snapshot["t"] - contact_t
        inside_window = 0 < t <= min(cutoff, live_end)
        bx, by, bz = snapshot["ball"]
        has_ball = not (bx == 0.0 and by == 0.0 and bz == 0.0)
        for name, actor in snapshot["actors"].items():
            if actor["kind"] != "fielder":
                continue
            matches = (inside_window and has_ball
                       and actor["fielding_action"] == action_code)
            if not matches:
                finish(name)
                continue
            px, _, pz = actor["pos"]
            distance = math.dist((px, pz), (bx, bz))
            # The fielder's own height reads 0 in every frame ever captured,
            # so this is the ball's distance from a point on the ground under
            # the fielder. That is the right comparison for "could this have
            # been touched at all": the error it can make is to call a ball
            # closer than it was, never further.
            reach = math.dist((px, 0.0, pz), (bx, by, bz))
            candidate = (distance, t, index, snapshot, actor)
            window = active.get(name)
            if window is None:
                window = active[name] = {
                    "start_frame": snapshot["timer"],
                    "end_frame": snapshot["timer"],
                    "closest": candidate,
                    "closest_reach": reach,
                    "contact": None,
                }
            else:
                window["end_frame"] = snapshot["timer"]
                if distance < window["closest"][0]:
                    window["closest"] = candidate
                window["closest_reach"] = min(window["closest_reach"], reach)
            actor_index = actor_indices.get(name, -1)
            contact_named = (
                int(snapshot["state"].get("last_contact_fielder", -1)) == actor_index
                or int(snapshot["state"].get("contact_fielder", -1)) == actor_index
            )
            if contact_named and window["contact"] is None:
                window["contact"] = candidate
    for name in list(active):
        finish(name)
    return sorted(events, key=lambda event: event["t"])


def detect_buddy_jumps(frames: list, contact_t: float, live_end: float) -> list:
    """Fielders the game put into a Buddy Jump, one record per attempt.

    THE ONE FIELDING ATTEMPT THE CAPTURE USED TO MISS ENTIRELY. A Buddy Jump is
    two outfielders boosting one of them over the wall, and it happens on
    exactly the balls where knowing whether anybody had a play matters most:
    home runs. It leaves no action-byte window and no possession, so a play
    with a Buddy Jump and nothing else came out of the derivation with zero
    fielding events, and the console said "no fielding attempt and no landing
    for this ball" about a ball two fielders had just gone up for.

    `buddy_jump_flag` (+0x223, fielder class) is what the game sets while it
    happens, and the VALUE MATTERS. Across the DK Jungle session the byte opens
    eight windows, four reading 2 and four reading 1, and the tracker log
    announces exactly four buddy jumps -- Peach once and Mario three times. The
    four flag-2 windows are those four plays, fielder for fielder; none of the
    flag-1 windows is announced at all, and one of them belongs to a shortstop,
    who cannot be boosted over an outfield wall. So 2 is the Buddy Jump and 1
    is some other state of the same byte, left unnamed rather than guessed at.
    The value is carried on the record so the next session can widen this if a
    flag-1 window ever turns out to be one.
    """
    jumps = []
    active = {}

    def finish(name, last):
        window = active.pop(name, None)
        if window is None or last is None:
            return
        window["end_frame"] = last["timer"]
        window["end_t"] = round(last["t"] - contact_t, 4)
        window["frames"] = window["end_frame"] - window["start_frame"] + 1
        jumps.append(window)

    previous = None
    for snapshot in frames:
        if snapshot["t"] - contact_t > live_end + 1e-9:
            break
        for name, actor in snapshot["actors"].items():
            if actor.get("kind") != "fielder":
                continue
            flag = actor.get("buddy_jump") or 0
            if flag != BUDDY_JUMP_FLAG_ACTIVE:
                flag = 0
            window = active.get(name)
            if window is not None and window["flag"] != flag:
                finish(name, previous or snapshot)
                window = None
            if flag and window is None:
                active[name] = {
                    "by": name,
                    "character_id": actor["character"],
                    "character": character_name(actor["character"]),
                    "flag": flag,
                    "start_frame": snapshot["timer"],
                    "start_t": round(snapshot["t"] - contact_t, 4),
                    "end_frame": snapshot["timer"],
                    "end_t": round(snapshot["t"] - contact_t, 4),
                    "frames": 1,
                }
        previous = snapshot
    for name in list(active):
        finish(name, previous)
    jumps.sort(key=lambda window: (window["start_frame"], window["by"]))
    return jumps


# THE BALL'S OWN PATH, as measured, for anything that has to DRAW the play.
#
# The field view used to be given three points -- contact, landing, first touch
# -- and drew straight lines between them. That is the truth only when nothing
# happened in between, and at Wario City something usually did. Worse, the two
# cases where the ball is most interesting have none of those points: a home run
# and a ball that leaves play have no landing and no first touch at all, so the
# diagram drew nothing and said "no measured endpoint" about a ball whose whole
# flight is sitting in these frames. The operator, 2026-09-10 PA93: "we should
# be tracking the full path of the ball, regardless of what happened. this also
# includes homeruns as those are not being tracked either."
#
# WHERE IT STOPS. A ball in a glove is not a ball in flight -- its coordinates
# become the glove's, and past the play they become the dead-ball reset, which
# is how eleven Daisy Cruiser catches once came to sit three feet from home
# plate. So the path ends at the FIRST possession, or at the end of the live
# window, whichever comes first, and never runs past either.
#
# Sampled rather than dumped: at 60 Hz a seven-second flight is 420 points and
# nothing drawn at screen scale can tell them from 70. The endpoint is always
# kept exactly, whatever the stride does.
BALL_PATH_STRIDE_FRAMES = 6
BALL_PATH_MAX_POINTS = 120


def measure_ball_path(frames: list, contact_t: float, live_end: float,
                      first_touch: dict | None, possession_carries: list) -> list:
    """Every measured ball position from contact to the first glove, sampled."""
    stop_timer = None
    for carry in possession_carries or ():
        timer = carry.get("start_frame")
        if timer is not None:
            stop_timer = timer if stop_timer is None else min(stop_timer, timer)
    if first_touch is not None and first_touch.get("frame") is not None:
        stop_timer = (first_touch["frame"] if stop_timer is None
                      else min(stop_timer, first_touch["frame"]))

    live = []
    for snapshot in frames:
        t = snapshot["t"] - contact_t
        if t < 0 or t > live_end:
            continue
        if stop_timer is not None and snapshot["timer"] > stop_timer:
            break
        ball = snapshot.get("ball")
        if ball is None or not all(math.isfinite(value) for value in ball):
            continue
        live.append((snapshot["timer"], round(t, 4), ball))
    if not live:
        return []

    stride = max(BALL_PATH_STRIDE_FRAMES,
                 math.ceil(len(live) / BALL_PATH_MAX_POINTS))
    kept = live[::stride]
    if kept[-1][0] != live[-1][0]:
        kept.append(live[-1])
    return [{"frame": timer, "t": t, "at": [round(value, 3) for value in ball]}
            for timer, t, ball in kept]


def detect_landing(frames: list, contact_t: float, live_end: float,
                   first_touch: dict | None):
    """When and where the ball first reached the ground.

    This is the measurement Catch Probability is built on, and it is NOT first
    touch. On a ball that falls in, first touch is where a fielder picked it up
    after it bounced, which is a different place at a later time than where it
    landed -- so using first touch as the target compares a fly ball's catch
    point against a base hit's pickup point and calls them the same feature.

    The search stops at first touch, because a ball in a glove is not landing --
    it is being carried, and a fielder who lowers it is not a bounce.

    Returns None for a ball that never came down inside the live window, which
    is exactly the set that was caught on the fly or left the park.
    """
    for previous, before, after in zip(frames, frames[1:], frames[2:]):
        t = before["t"] - contact_t
        # EXCLUSIVE at first touch, because the possession frame passes the
        # descent test below on its own: the ball reads y=0 in the glove with
        # the last free-flight frame above it. Including it turned a ball taken
        # off the wall 2.8 units up into a landing at the fielder's feet, and
        # its hang time into time-to-pickup -- 6.96 s on one Mario Stadium ball
        # whose real flight ended when it struck the wall. 19 plays across the
        # archive, all of them fair balls, 18 with the landing point byte-equal
        # to first touch.
        # Compared on the FRAME, not on the seconds: first_touch["t"] is stored
        # rounded to four decimals and can land just above the raw t of the very
        # frame it names, which let the possession frame back into the scan.
        if t > live_end or (first_touch is not None
                            and before["timer"] >= first_touch["frame"]):
            break
        y = before["ball"][1]
        if t <= 0 or y > LANDING_HEIGHT_UNITS:
            continue
        # A turning point the ball DESCENDED into. The descent is what makes it
        # a landing: the ball leaves the bat below this height and climbing, and
        # without that check every batted ball "lands" on the frame after
        # contact.
        if previous["ball"][1] > y and after["ball"][1] >= y:
            return {
                "t": round(t, 4),
                "frame": before["timer"],
                "at": [round(value, 3) for value in before["ball"]],
                "distance_units": round(
                    math.hypot(before["ball"][0], before["ball"][2]), 3),
            }
    return None


def flight_window(samples: list):
    """The longest run of consecutive frames the ball was actually in flight.

    Returns (index of the launch sample, the smoothed speeds over that run), or
    None if nothing in the window looks like a flight. "Consecutive" is the
    point: the release snap is a single fast sample separated from the flight by
    the whole wind-up hold, so it can never join the run however fast it is, and
    the arrival snap is rejected by its own step change rather than by a cap.
    """
    speeds = []
    for index, (before, after) in enumerate(zip(samples, samples[1:])):
        dt = after["t"] - before["t"]
        if dt <= 0:
            continue
        speed = math.dist(before["ball"], after["ball"]) / dt
        if MIN_THROW_SPEED_MPS <= speed <= MAX_THROW_SPEED_MPS:
            speeds.append((index, speed))

    best = current = []
    for entry in speeds:
        if current:
            last_index, last_speed = current[-1]
            adjacent = entry[0] == last_index + 1
            smooth = abs(entry[1] - last_speed) <= FLIGHT_SPEED_RATIO * last_speed
            if not (adjacent and smooth):
                current = []
        current = current + [entry]
        if len(current) > len(best):
            best = current
    if len(best) < MIN_THROW_SAMPLES:
        return None

    values = [speed for _, speed in best]
    stable = [statistics.median(values[max(0, i - 1):min(len(values), i + 2)])
              for i in range(len(values))]
    return best[0][0], stable


def named_fielder(snapshots: list, field: str, order: list) -> str | None:
    """The fielder a state scalar names, over the opening frames of a throw.

    All three of these scalars idle at -1 and are set on the release frame, but
    a frame of slack costs nothing and covers a scalar that lands one tick late.
    """
    for snapshot in snapshots[:8]:
        index = int(snapshot["state"].get(field, -1))
        if 0 <= index < len(order):
            return order[index]
    return None


def detect_throws(frames: list, contact_t: float, live_end: float,
                  bags: dict, order: list, home: tuple | None = None) -> list:
    """Segment fielder possession -> flight -> receiver possession."""
    live = [snapshot for snapshot in frames
            if snapshot["t"] - contact_t <= live_end + 1e-9]
    by_timer = {snapshot["timer"]: snapshot for snapshot in live}
    throws = []
    held_by = None
    held_since = None
    held_since_t = None
    flight = None

    def finish(receiver_name: str, arrival_snapshot: dict) -> None:
        nonlocal flight
        if not flight or receiver_name == flight["thrower"]:
            flight = None
            return
        samples = flight["samples"] + [arrival_snapshot]
        window = flight_window(samples)
        if window is None:
            flight = None
            return
        first_index, stable = window
        launch = samples[first_index]
        release = flight["release"]
        # The game names a Buddy Throw explicitly. A frozen cutscene remains a
        # useful duration measurement, but cannot classify it: close-play
        # contests freeze the world too.
        buddy_named = named_fielder(samples, "buddy_thrower", order)
        buddy_throw = buddy_named is not None
        # The game's own flag for a Laser Beam, up for exactly this throw's
        # flight. Read over the whole flight rather than at one frame, because
        # the release frame the segmentation picks and the frame the game raises
        # the flag on need not be the same one. See STATE_FIELDS in
        # collect_player_tracking.py for how it was identified.
        laser_throw = any(
            int(snapshot["state"].get("laser_throw_flag", 0) or 0) != 0
            for snapshot in samples)
        thrower = release["actors"][flight["thrower"]]
        receiver = arrival_snapshot["actors"][receiver_name]
        start_ball = launch["ball"]
        end_ball = arrival_snapshot["ball"]
        outs_recorded = max(
            0, int(arrival_snapshot["state"].get("outs", 0))
            - int(release["state"].get("outs", 0)))
        target_base = closest_target_base(receiver["pos"], bags, home)
        target_positions = {
            "first": bags.get("R1"), "second": bags.get("R2"),
            "third": bags.get("R3"), "home": home,
        }
        target_position = target_positions.get(target_base)
        receiver_distance_from_target = (
            math.dist(receiver["pos"][::2], target_position[::2])
            if target_position is not None else None)
        receiver_pulled_off_base = (
            receiver_distance_from_target is not None
            and receiver_distance_from_target > RECEIVER_PULLED_OFF_BASE_UNITS)
        # WHERE THE GAME AIMED IT AND WHERE IT SENT IT, one frame after
        # release. See THROW_OFF_TARGET_UNITS for why only a throw aimed at a
        # standing receiver is judged.
        aimed = by_timer.get(release["timer"] + 1)
        aim_at = aimed.get("throw_aim") if aimed else None
        destination_at = aimed.get("throw_destination") if aimed else None
        aim_miss = aim_to_receiver = destination_error = None
        if aim_at is not None and receiver_name in aimed["actors"]:
            aim_miss = math.dist(aim_at[::2], end_ball[::2])
            aim_to_receiver = math.dist(aim_at[::2], aimed["actors"][receiver_name]["pos"][::2])
            if destination_at is not None:
                destination_error = math.dist(aim_at[::2], destination_at[::2])
        off_target = (aim_miss is not None
                      and aim_to_receiver <= THROW_AIM_STANDING_UNITS
                      and (destination_error if destination_error is not None
                           else aim_miss) > THROW_OFF_TARGET_UNITS)

        # HOW CLOSE THE PLAY WAS. The ball's arrival is measured and so is where
        # every runner is standing on that frame, and the gap between them is
        # what an operator means by "a close play at the plate". Who WON it is
        # not inferred from this -- that is the game's call and it is already in
        # `outs_recorded`; this is the margin, which nothing else records.
        #
        # An empty base slot keeps whatever runner last occupied it, so the
        # batting index gates this rather than the coordinates: a stale Luigi
        # parked on first would otherwise be the closest runner to every base.
        runner_at_arrival = None
        if target_position is not None:
            candidates = [
                (math.dist(actor["pos"][::2], target_position[::2]), name, actor)
                for name, actor in arrival_snapshot["actors"].items()
                if actor["kind"] == "offense" and actor["index"] >= 0
            ]
            if candidates:
                distance, name, actor = min(candidates, key=lambda entry: entry[0])
                # STANDING ON THE BASE LOOKS EXACTLY LIKE ARRIVING AT IT, in a
                # single frame: a batter who reached first two seconds ago is
                # 0.8 units from the bag, and so is one sliding in ahead of the
                # throw. Half a second of history separates them -- the runner
                # who is already there has not moved, and one arriving at a
                # measured 9 u/s has covered about 4.5 units.
                earlier = by_timer.get(
                    arrival_snapshot["timer"] - CLOSE_PLAY_LOOKBACK_FRAMES)
                before = earlier["actors"].get(name) if earlier else None
                distance_before = (
                    math.dist(before["pos"][::2], target_position[::2])
                    if before is not None else None)
                # WHEN THE RUNNER GOT THERE. Matched on the batting index
                # rather than the base slot, because a runner advancing moves
                # between slots and the slot would follow whoever is standing in
                # it. Searched from the start of the play so a runner who was
                # already on the bag reads as the long negative margin they are.
                reach_frame = None
                reach_t = None
                for snapshot in frames:
                    if (snapshot["timer"]
                            > arrival_snapshot["timer"] + CLOSE_PLAY_REACH_SEARCH_FRAMES):
                        break
                    running = next(
                        (candidate for candidate in snapshot["actors"].values()
                         if candidate["kind"] == "offense"
                         and candidate["index"] == actor["index"]),
                        None)
                    if running is None:
                        continue
                    if (math.dist(running["pos"][::2], target_position[::2])
                            <= RUNNER_AT_BASE_UNITS):
                        reach_frame = snapshot["timer"]
                        reach_t = snapshot["t"]
                        break
                runner_at_arrival = {
                    "runner": name,
                    "character_id": actor["character"],
                    "character": character_name(actor["character"]),
                    "distance_from_target_units": round(distance, 3),
                    "distance_before_units": (round(distance_before, 3)
                                              if distance_before is not None else None),
                    "closing_units": (round(distance_before - distance, 3)
                                      if distance_before is not None else None),
                    # The margin itself. `reach_frame` is None when the runner
                    # never reached that base at all, which is most throws --
                    # the nearest runner to second on a routine grounder to
                    # first is twenty units away and was never in the play.
                    "reach_frame": reach_frame,
                    "margin_s": (round(reach_t - arrival_snapshot["t"], 4)
                                 if reach_t is not None else None),
                }
        throws.append({
            "sequence": len(throws) + 1,
            "thrower_position": flight["thrower"],
            "thrower_character_id": thrower["character"],
            "thrower_character": character_name(thrower["character"]),
            "receiver_position": receiver_name,
            "receiver_character_id": receiver["character"],
            "receiver_character": character_name(receiver["character"]),
            "possession_frame": flight["possession_timer"],
            "release_frame": release["timer"],
            # Separation and launch are up to a second apart, because the ball
            # leaves the hand at the start of the wind-up and does not move
            # until the end of it. Arm Strength is a property of the flight, so
            # it is measured from the launch; the separation frame is kept
            # because possession-to-arrival is what an arm-value model needs.
            "launch_frame": launch["timer"],
            "arrival_frame": arrival_snapshot["timer"],
            "release_t": round(release["t"] - contact_t, 4),
            # How long the ball sat in the glove. Ordinary throws release
            # inside a second -- across 558 measured throws the median is 0.70s
            # and the 95th percentile is 1.43s -- so a long hold is a real
            # event, whatever caused it. DK Jungle's flower sprays a fielder and
            # leaves them dazed and unable to throw, and the longest hold in
            # that session (2.94s, four times the median) is exactly the play
            # the operator flagged for it. The measurement is reported; the
            # cause is not guessed at.
            "hold_s": (round(release["t"] - flight["possession_t"], 4)
                       if flight.get("possession_t") is not None else None),
            "launch_t": round(launch["t"] - contact_t, 4),
            "arrival_t": round(arrival_snapshot["t"] - contact_t, 4),
            "flight_frames": len(stable),
            "start": [round(value, 4) for value in start_ball],
            "end": [round(value, 4) for value in end_ball],
            "target_base": target_base,
            "receiver_distance_from_target_units": (
                round(receiver_distance_from_target, 3)
                if receiver_distance_from_target is not None else None),
            # The nearest real runner to the base this throw went to, on the
            # frame the ball got there.
            "runner_at_arrival": runner_at_arrival,
            "receiver_pulled_off_base": receiver_pulled_off_base,
            # The game's aim point one frame after release, how far the ball
            # landed from it, and whether that makes this an inaccurate throw.
            # See THROW_OFF_TARGET_UNITS.
            "aim_at": [round(value, 3) for value in aim_at] if aim_at is not None else None,
            "aim_miss_units": round(aim_miss, 3) if aim_miss is not None else None,
            "aim_to_receiver_units": (round(aim_to_receiver, 3)
                                      if aim_to_receiver is not None else None),
            # Where the game actually sent the ball, and how far that is from
            # the aim point: the error the throw was given, which off_target
            # keys on. See THROW_OFF_TARGET_UNITS.
            "destination_at": ([round(value, 3) for value in destination_at]
                               if destination_at is not None else None),
            "destination_error_units": (round(destination_error, 3)
                                        if destination_error is not None else None),
            "off_target": off_target,
            "peak_speed_mps": round(max(stable), 4),
            "peak_speed_mph": round(max(stable) * METRES_PER_SECOND_TO_MPH, 3),
            "median_speed_mps": round(statistics.median(stable), 4),
            "sample_count": len(stable),
            # A Buddy Throw, and the frozen cutscene that identifies one. The
            # thrower recorded above is the fielder who STARTED it; the
            # chemistry partner who actually threw the ball cannot be recovered
            # from the capture, so this velocity is not an arm measurement.
            "buddy_throw": buddy_throw,
            # Measured, not inferred from speed. False on a session recorded
            # before the flag was named only if that session predates the state
            # block -- every MSSTRK02 capture already holds the byte.
            "laser_throw": laser_throw,
            "buddy_freeze_s": round(launch["t"] - release["t"], 4) if buddy_throw else 0.0,
            "buddy_thrower_position": buddy_named,
            # The chemistry partner who made the throw possible.
            "buddy_partner_position": (
                named_fielder(samples, "buddy_partner", order)
                if buddy_throw else None),
            # Who the throw was AIMED at, from the game rather than from who
            # caught it. On a throw to second base these differ whenever the
            # other middle infielder covers, and the intent is the useful half.
            "intended_target_position": named_fielder(samples, "throw_target", order),
            "outs_recorded": outs_recorded,
            "is_relay": len(throws) > 0,
            "quality": {
                "raw_speed_samples": len(stable),
                "discarded_speed_samples": max(0, len(samples) - 1 - len(stable)),
            },
        })
        flight = None

    for snapshot in live:
        owner = locked_fielder(snapshot)
        if owner is not None:
            if flight is not None:
                finish(owner, snapshot)
            if owner != held_by:
                held_by = owner
                held_since = snapshot["timer"]
                held_since_t = snapshot["t"]
            continue

        if held_by is not None and flight is None:
            flight = {
                "thrower": held_by,
                "possession_timer": held_since,
                "possession_t": held_since_t,
                "release": snapshot,
                "samples": [snapshot],
            }
            held_by = None
            held_since = None
            held_since_t = None
        elif flight is not None:
            flight["samples"].append(snapshot)

    # An out at a base is often posted after the receiver gets the ball (the
    # runner still has to be tagged), so release-to-arrival alone reports zero
    # for successful throws. Attribute every out posted between this arrival
    # and the next throw's release to this throw. A fly-out already exists in
    # the release snapshot and therefore is not borrowed by a later throw.
    by_timer = {snapshot["timer"]: snapshot for snapshot in live}
    for index, throw in enumerate(throws):
        release = by_timer.get(throw["release_frame"])
        end_timer = (throws[index + 1]["release_frame"] - 1
                     if index + 1 < len(throws)
                     else live[-1]["timer"] if live else throw["arrival_frame"])
        after_arrival = [
            snapshot for snapshot in live
            if throw["arrival_frame"] <= snapshot["timer"] <= end_timer
        ]
        if release is not None and after_arrival:
            release_outs = int(release["state"].get("outs", 0))
            final_outs = max(int(snapshot["state"].get("outs", 0))
                             for snapshot in after_arrival)
            throw["outs_recorded"] = max(0, final_outs - release_outs)

    # A runner collision immediately after a close arrival can pop the ball out
    # of the receiver's glove. Possession-to-possession segmentation sees the
    # loose ball reach a backup and would otherwise call it a relay throw. The
    # combination below is intentionally strict: no selected target, very low
    # speed, an immediate transition, and a runner measurably arriving on the
    # preceding throw.
    for index in range(1, len(throws)):
        previous = throws[index - 1]
        current = throws[index]
        contested = previous.get("runner_at_arrival")
        runner_gap = (float(contested.get("distance_from_target_units"))
                      if contested and contested.get("distance_from_target_units") is not None
                      else None)
        closing = (float(contested.get("closing_units"))
                   if contested and contested.get("closing_units") is not None
                   else None)
        transition = float(current["release_t"]) - float(previous["arrival_t"])
        if (current.get("intended_target_position") is None
                and float(current.get("peak_speed_mph") or 0) <= LOOSE_BALL_MAX_SPEED_MPH
                and 0 <= transition <= LOOSE_BALL_MAX_TRANSITION_SECONDS
                and runner_gap is not None
                and runner_gap <= THROWING_ERROR_RUNNER_DISTANCE_UNITS
                and closing is not None
                and closing >= THROWING_ERROR_RUNNER_CLOSING_UNITS):
            current["is_throw"] = False
            current["event_type"] = "loose_ball_recovery"
            current["caused_by_runner_contact"] = True
            current["runner_contact"] = contested
            current["is_relay"] = False
            previous["receiver_knocked_loose"] = True

    # This is a review flag, not an automatic official error. It identifies the
    # narrow factual pattern the scorer needs: an inaccurate throw pulled the
    # receiver off a base while a runner was still arriving, and no out followed.
    for throw in throws:
        contested = throw.get("runner_at_arrival")
        runner_gap = (float(contested.get("distance_from_target_units"))
                      if contested and contested.get("distance_from_target_units") is not None
                      else None)
        closing = (float(contested.get("closing_units"))
                   if contested and contested.get("closing_units") is not None
                   else None)
        throwing_error_candidate = bool(
            throw.get("is_throw") is not False
            and throw.get("intended_target_position") is not None
            and throw.get("receiver_pulled_off_base") is True
            and int(throw.get("outs_recorded") or 0) == 0
            and runner_gap is not None
            and runner_gap <= THROWING_ERROR_RUNNER_DISTANCE_UNITS
            and closing is not None
            and closing >= THROWING_ERROR_RUNNER_CLOSING_UNITS)
        if throwing_error_candidate:
            throw["throwing_error_candidate"] = True

    return throws


def detect_pitch_release_window(recent: list, fps: float) -> list:
    """Return the latest sustained pre-swing ball-flight window."""
    if len(recent) < 4:
        return []
    cutoff = recent[-1]["t"] - PITCH_LOOKBACK_SECONDS
    window = [snapshot for snapshot in recent if snapshot["t"] >= cutoff]
    moving = []
    for before, after in zip(window, window[1:]):
        delta = math.dist(before["ball"], after["ball"])
        moving.append(delta >= PITCH_MOVE_UNITS_PER_FRAME)
    moving_indices = [index for index, value in enumerate(moving) if value]
    if not moving_indices:
        return []
    end = moving_indices[-1]
    # A valid pitch must still have been moving close to the swing; otherwise
    # the latest motion was a reset or the previous play.
    if window[-1]["t"] - window[end + 1]["t"] > 0.5:
        return []
    start = end
    gaps = 0
    while start > 0:
        if moving[start - 1]:
            gaps = 0
            start -= 1
            continue
        gaps += 1
        if gaps > 2:
            break
        start -= 1
    return window[start:end + 2]


def cumulative_distance_splits(track: Track, step_feet: int = 5,
                               max_feet: int = 90) -> dict:
    """Interpolate elapsed time at each five-foot path-distance crossing."""
    if len(track.points) < 2:
        return {}
    thresholds = [(feet, feet / 3.280839895)
                  for feet in range(step_feet, max_feet + step_feet, step_feet)]
    output = {}
    cumulative = 0.0
    target_index = 0
    for index, (before, after) in enumerate(zip(track.points, track.points[1:])):
        step = math.dist(before[::2], after[::2])
        if step > TELEPORT_UNITS or step <= 0:
            continue
        previous = cumulative
        cumulative += step
        while target_index < len(thresholds) and cumulative >= thresholds[target_index][1]:
            feet, distance = thresholds[target_index]
            fraction = (distance - previous) / step
            t = track.times[index] + fraction * (track.times[index + 1] - track.times[index])
            output[str(feet)] = round(t, 4)
            target_index += 1
        if target_index >= len(thresholds):
            break
    return output


def jump_measurement(frames: list, position: str, first_touch: dict,
                     release_timer: int, fps: float) -> dict | None:
    if not frames or not first_touch or release_timer is None:
        return None
    relevant = [snapshot for snapshot in frames if snapshot["timer"] >= release_timer]
    if not relevant or position not in relevant[0]["actors"]:
        return None
    start = relevant[0]["actors"][position]["pos"]
    target = tuple(first_touch["at"])
    vx, vz = target[0] - start[0], target[2] - start[2]
    length = math.hypot(vx, vz)
    if length <= 1e-9:
        return None
    ux, uz = vx / length, vz / length

    def sample_at(seconds):
        target_frame = release_timer + seconds * fps
        return min(relevant, key=lambda snapshot: abs(snapshot["timer"] - target_frame))

    at_15 = sample_at(1.5)["actors"][position]["pos"]
    at_30 = sample_at(3.0)["actors"][position]["pos"]
    reaction = max(0.0, (at_15[0] - start[0]) * ux + (at_15[2] - start[2]) * uz)
    jump = max(0.0, (at_30[0] - start[0]) * ux + (at_30[2] - start[2]) * uz)
    burst = max(0.0, jump - reaction)
    three_second_frames = [snapshot for snapshot in relevant
                           if snapshot["timer"] <= release_timer + 3.0 * fps]
    points = [snapshot["actors"][position]["pos"] for snapshot in three_second_frames]
    path = sum(math.dist(before[::2], after[::2])
               for before, after in zip(points, points[1:])
               if math.dist(before[::2], after[::2]) <= TELEPORT_UNITS)
    return {
        "pitch_release_start": [round(value, 3) for value in start],
        "jump_distance_units": round(jump, 4),
        "jump_distance_feet": round(jump * 3.280839895, 3),
        "reaction_distance_units": round(reaction, 4),
        "reaction_distance_feet": round(reaction * 3.280839895, 3),
        "burst_distance_units": round(burst, 4),
        "burst_distance_feet": round(burst * 3.280839895, 3),
        "jump_route_efficiency": round(jump / path, 4) if path > 0 else None,
    }


# --- the incremental state machine ------------------------------------------
#
# WHY THIS IS A CLASS. The derivation was always an incremental forward state
# machine -- it opens a play when the ball leaves the plate and closes one at
# the dead ball, and nothing in it ever looks back past the play it is in. It
# just happened to be spelled as a loop inside main(), which meant the only
# thing that could run it was a finished recording.
#
# The live collector needs the same answers during the game, and the one thing
# that must not happen is a second implementation: two derivations that agree
# on most plays and quietly disagree on the interesting ones would be worse
# than having no live numbers at all, because the live page would look right.
# So the loop moved here, and both callers feed it the same snapshots built by
# the same SnapshotBuilder.
#
# The postgame pass over the raw .bin remains authoritative. Live output is the
# same code reaching the same conclusion earlier, and it is labelled as live.


class PlayDeriver:
    """Frames in, completed plays out.

    Feed every frame in order with `feed(snapshot)`. It returns the plays that
    finished on that frame -- normally an empty list, and exactly one on the
    frame the ball goes dead. Call `flush()` at the end of a stream to close a
    play that was still open.

    Replay suppression is incremental and equivalent to the batch pass it
    replaces: the batch version only ever compared a play against previously
    KEPT plays inside a 20-second window, which is already a streaming rule.
    """

    def __init__(self, fps: float = GAME_FRAME_RATE, suppress_replays: bool = True,
                 build_executor=None):
        self.fps = fps
        self.suppress_replays = suppress_replays
        # WHERE build_play RUNS. Closing a play costs 42-87 ms on the archived
        # sessions -- route summaries, throw segmentation and split
        # interpolation over up to 1,800 frames -- and a 60 Hz capture has a
        # 16.7 ms frame budget of which the recording itself already uses 1.8.
        # Doing that work inline would drop about five frames at every dead
        # ball, which is exactly the kind of quiet damage live derivation is
        # not allowed to do to the recording.
        #
        # So a live caller passes a SINGLE-WORKER executor. The build moves off
        # the capture loop, the GIL hands it out in millisecond slices while
        # the loop keeps reading frames, and one worker preserves the order
        # plays closed in. The postgame pass passes nothing and runs inline,
        # which is why both produce byte-identical output: same function, same
        # arguments, same sequence -- only the thread differs.
        self.build_executor = build_executor
        self._pending = deque()
        # Measured bag coordinates, ball frame. An unoccupied runner slot parks
        # exactly on its bag, so the session itself says where the bases are.
        self.bags = {}
        self.recent = deque(maxlen=int(fps * 12))
        self.active = None
        self.pending = None
        self.previous_state = None
        self.pointer_tables = set()
        self.frames_seen = 0
        self.plays_emitted = 0
        self.replay_duplicates = 0
        # Per-play wall cost, so a live caller can prove the derivation is not
        # what is eating its frame budget.
        self.last_build_seconds = None
        self.max_build_seconds = 0.0
        self._seen = {}

    # -- replay suppression, incrementally ---------------------------------

    _FAIR_CLASSES = frozenset(
        {"fair_in_play", "fair_caught", "home_run", "home_run_robbed"})
    _COMPARABLE_CLASSES = _FAIR_CLASSES | {"unknown"}

    def _accept(self, play: dict):
        """Keep this play, or drop it as an immediate replay copy."""
        trajectory = tuple(tuple(point)
                           for point in play.pop("_trajectory_signature", ()))
        if not self.suppress_replays:
            return play
        play_class = play.get("batted_ball_class")
        if play_class not in self._COMPARABLE_CLASSES or not trajectory:
            return play
        key = (
            play.get("inning"), play.get("inning_half"), play.get("outs"),
            play.get("balls"), play.get("strikes"), play.get("batter_id"),
            play.get("batter_index"),
        )
        timer = play.get("contact_timer")
        window = round(self.fps * REPLAY_WINDOW_SECONDS)
        if timer is not None:
            fingerprint = replay_fingerprint(play)
            for (previous_timer, previous_class, previous_trajectory,
                 previous_fingerprint) in self._seen.get(key, ()):
                # An unknown record is useful evidence when it stands alone, but
                # it is not strong enough to suppress a later called fair ball.
                if previous_class not in self._FAIR_CLASSES:
                    continue
                if not 0 <= timer - previous_timer <= window:
                    continue
                if replay_trajectories_match(
                        previous_trajectory, trajectory,
                        previous_fingerprint, fingerprint, play.get("live_s")):
                    self.replay_duplicates += 1
                    return None
            if play_class in self._FAIR_CLASSES:
                self._seen.setdefault(key, []).append(
                    (timer, play_class, trajectory, fingerprint))
        return play

    def _finish(self, play: dict) -> list:
        kept = self._accept(play)
        if kept is None:
            return []
        self.plays_emitted += 1
        return [kept]

    def _timed_build(self, active, bags, truncated: bool) -> dict:
        started = time.perf_counter()
        play = build_play(active, bags, self.fps, truncated=truncated)
        self.last_build_seconds = time.perf_counter() - started
        self.max_build_seconds = max(self.max_build_seconds,
                                     self.last_build_seconds)
        return play

    def _close(self, truncated: bool) -> list:
        active = self.active
        self.active = None
        # `bags` keeps being measured by later frames, so the build gets the
        # snapshot that was true when this play ended rather than a dict that
        # mutates underneath it.
        bags = dict(self.bags)
        if self.build_executor is None:
            return self._finish(self._timed_build(active, bags, truncated))
        self._pending.append(
            self.build_executor.submit(self._timed_build, active, bags, truncated))
        return self._drain()

    def _drain(self, block: bool = False) -> list:
        """Release the builds that have finished, oldest first.

        Order is preserved by refusing to skip an unfinished build: a play that
        closed earlier is always emitted earlier, which is what lets the
        replay-suppression window and the postgame comparison stay valid.
        """
        released = []
        while self._pending:
            future = self._pending[0]
            if not block and not future.done():
                break
            self._pending.popleft()
            released.extend(self._finish(future.result()))
        return released

    # -- the frame loop ----------------------------------------------------

    def note_pointers(self, pointers) -> None:
        """Whether the nine fielder objects are ever reallocated.

        The fixed capture region rests on this one assumption, and the failure
        if it breaks is silent: half a session of stale bytes that still look
        like coordinates.
        """
        self.pointer_tables.add(tuple(pointers))

    def feed(self, snapshot: dict) -> list:
        """One frame. Returns the plays released by it."""
        self.frames_seen += 1
        released = self._drain() if self._pending else []
        if released:
            return released + self._feed(snapshot)
        return self._feed(snapshot)

    def _feed(self, snapshot: dict) -> list:
        state = snapshot["state"]
        t = snapshot["t"]
        ball = snapshot["ball"]

        for name, actor in snapshot["actors"].items():
            # An empty runner slot sits on its bag, so it measures the bag.
            if (actor["kind"] == "offense" and name != "BAT"
                    and actor["index"] < 0):
                self.bags[name] = actor["pos"]
        self.recent.append(snapshot)

        hit = state["ball_was_hit"]
        was_hit = self.previous_state["ball_was_hit"] if self.previous_state else 0
        self.previous_state = state

        if self.active is None and self.pending is None and was_hit == 0 and hit == 1:
            # `ball_was_hit` rises at the SWING, not at the launch. The ball
            # then sits frozen at the plate for the duration of the contact
            # animation -- measured at 1.6 s on a star swing -- before it moves
            # at all. Starting the clock here would fold that animation into
            # every reaction time, and because the freeze is longer for star
            # swings than for ordinary ones it would do so unevenly. So mark it
            # pending and wait for the ball to actually leave.
            #
            # It also rises again AFTER the ball is dead, while the game replays
            # a home run or changes innings. Those raises are not swings: the
            # ball is out where it came to rest rather than on the plate, and
            # `game_state` has already left its live-ball value.
            if (math.hypot(ball[0], ball[2]) > CONTACT_BALL_RADIUS_UNITS
                    or state["game_state"] != LIVE_BALL_GAME_STATE):
                return []
            pitch_window = detect_pitch_release_window(list(self.recent), self.fps)
            self.pending = {
                "t": t, "timer": snapshot["timer"], "ball": ball,
                "situation": dict(state),
                "pitch_frames": pitch_window,
                "pitch_release_timer": pitch_window[0]["timer"] if pitch_window else None,
            }
            return []

        if self.pending is not None:
            pending = self.pending
            if not pending["pitch_frames"] or pending["pitch_frames"][-1]["timer"] != snapshot["timer"]:
                pending["pitch_frames"].append(snapshot)
            moved = math.dist(ball, pending["ball"])
            expired = t - pending["t"] > MAX_LAUNCH_DELAY_SECONDS
            if moved > LAUNCH_MOVE_UNITS:
                self.active = {
                    "contact_t": t,
                    "contact_timer": snapshot["timer"],
                    # The frame the swing itself started, which is not the frame
                    # the ball left: the contact animation holds the ball at the
                    # plate for 2 frames on an ordinary swing and about 91 on a
                    # star swing. PitchDeriver closes a pitch at the swing, so
                    # this is the frame the two streams join on -- carrying it
                    # means the join is an equality rather than a tolerance.
                    "swing_timer": pending["timer"],
                    "swing_to_launch_s": round(t - pending["t"], 4),
                    "frames": [snapshot],
                    "pre_contact_frames": pending["pitch_frames"],
                    "pitch_release_timer": pending.get("pitch_release_timer"),
                    "situation": pending["situation"],
                    "live_timer": snapshot["timer"],
                    "live_flags": [],
                }
                self.pending = None
                return []
            if expired or hit == 0:
                # The ball never left: a foul tip taken by the catcher, or a
                # flag that flickered. Nothing to measure.
                self.pending = None
            return []

        if self.active is not None:
            active = self.active
            active["frames"].append(snapshot)
            if state["game_state"] == LIVE_BALL_GAME_STATE:
                # The live window, and the fair/foul and home-run calls made
                # inside it. Both flags are cleared on the same frame the play
                # window closes, so the last frame of the window always reads 0
                # for both.
                active["live_timer"] = snapshot["timer"]
                active["live_flags"].append(
                    (state["fair_or_foul"], state["home_run_flag"]))
            over = (hit == 0 and was_hit == 1)
            too_long = t - active["contact_t"] > MAX_PLAY_SECONDS
            # `ball_was_hit` is not a reliable end-of-play signal on its own: it
            # stays latched through inning changes and between-play animation,
            # for up to 9.8s past the dead ball in the archived sessions. That
            # is dead time for the derivation -- every detector is already
            # windowed at `live_end` -- but it is not dead time for an operator
            # waiting to see what the tracker made of the play. So the window
            # also closes a fixed tail past the game's own dead-ball transition.
            #
            # The tail is not arbitrary. The one measurement that legitimately
            # reads PAST the dead ball is `final_bases_ran`, which follows a
            # runner's count through the actor teardown, and the widest gap in
            # the whole archive between the dead ball and a runner's last
            # advance is inside this window. Verified by re-deriving all five
            # sessions: identical output on every field of every play.
            if state["game_state"] == LIVE_BALL_GAME_STATE:
                active["dead_since"] = None
            elif active.get("dead_since") is None:
                active["dead_since"] = snapshot["timer"]
            settled = (active.get("dead_since") is not None
                       and snapshot["timer"] - active["dead_since"]
                       >= DEAD_BALL_TAIL_FRAMES)
            if over or too_long or settled:
                # After the final out of a game, `ball_was_hit` can stay set
                # forever while the caught ball remains locked in the glove. A
                # caught third out is terminal on its own; there can be no
                # subsequent runner/throw outcome the latched state is hiding.
                return self._close(
                    truncated=too_long and not is_complete_third_out_catch(active))
        return []

    def flush(self) -> list:
        """Close a play still open at the end of the stream, and wait for the
        deferred builds so nothing is lost when the capture stops."""
        released = []
        if self.active is not None:
            released.extend(self._close(
                truncated=not is_complete_third_out_catch(self.active)))
        released.extend(self._drain(block=True))
        return released


# The game's own `game_state` while a pitch is being delivered. Every one of the
# 154 genuine pitches in bowser_castle-20260828T182145Z incremented the pitch
# counter at this value, with the ball 15.8u to 17.7u from the plate -- in the
# pitcher's hand. The two increments that were NOT at this value were both
# inside a home-run replay, with the ball 47u and 89u away, and are the same
# replay that re-raises `ball_was_hit`. Gating on it is what makes the memory
# pitch count trustworthy enough to reconcile the tracker log against.
PITCH_GAME_STATE = 1

# A pitch nothing ever resolved is reported as unresolved rather than dropped.
# Real ones settle in about a second; this only has to be longer than an
# animation, and it exists so a missed transition costs one labelled record
# instead of silently swallowing every pitch after it.
MAX_PITCH_RESOLUTION_SECONDS = 20.0


class PitchDeriver:
    """Frames in, one record per pitch out.

    WHY THIS IS SEPARATE FROM PlayDeriver. A play is a batted ball, and most
    pitches are not one: the tracker's own log has no way to tell a swinging
    strike from a called strike, so it reports both as `strike_unknown`, and a
    pitch the log misses entirely leaves no trace at all. Both facts are in the
    state block on every frame -- the pitch counter, and the two swing animation
    counters that say how the batter offered -- and neither of them needs a
    position offset, so this runs and is trustworthy even in a session whose
    calibration never confirms.

    What it deliberately does NOT do is restate the batted ball. A pitch that
    made contact says so and names the frame, and the play record joined to
    that frame owns fair/foul, the landing and everything downstream.
    """

    def __init__(self, fps: float = GAME_FRAME_RATE):
        self.fps = fps
        self.previous_state = None
        self.open = None
        self.pitches_emitted = 0
        # Increments rejected as a replay of a pitch already counted. Reported
        # rather than hidden: if this ever climbs into the same order as the
        # pitch count, the rule is wrong and the count cannot be trusted.
        self.replay_increments = 0
        # THE REPLAY RE-RUNS THE PITCH COUNTER. When the game replays a home run
        # or a nice play it does not merely re-raise `ball_was_hit` -- it reloads
        # the state from before the pitch and runs the whole thing again, pitch
        # counter included, with the ball back in the pitcher's hand. So a
        # replayed pitch is at PITCH_GAME_STATE with the ball exactly where a
        # real one starts, and neither position nor game state can separate them.
        #
        # What separates them is that a replay covers ground already covered.
        # Within one plate appearance the counter only ever goes up, so a pitch
        # is real only when it takes the counter to a value this plate
        # appearance has not already reached. On
        # bowser_castle-20260828T182145Z that rejects 11 phantoms -- one per home
        # run, one per replayed nice play -- and keeps 145, which is exactly the
        # number of pitches in the tracker's own log for the same game.
        self._pa_key = None
        self._pitch_high = 0

    # -- the frame loop ----------------------------------------------------

    def feed(self, snapshot: dict) -> list:
        """One frame. Returns the pitches that resolved on it."""
        state = snapshot["state"]
        previous, self.previous_state = self.previous_state, state
        if previous is None:
            return []

        released = []
        if self.open is not None:
            self._track(snapshot, previous, state)
            resolved = self._resolution(snapshot, previous, state)
            if resolved:
                released.append(self._close(snapshot, *resolved))

        # `pitches` is the game's own per-plate-appearance pitch counter, and it
        # resets between plate appearances, so a rise in it is a pitch -- unless
        # the rise is a replay re-running one already counted. See _replay_high.
        # Which plate appearance this is. The lineup INDEX alone is not enough:
        # the two teams number their slots the same way, so a half-inning that
        # ends on slot 8 and the next one that opens on slot 8 look like one
        # continuing plate appearance, and the leadoff pitch of the new half
        # gets thrown away as a replay of the last one of the old.
        pa_key = (state.get("inning"), state.get("inning_half"),
                  state.get("batter_index"), state.get("batter_id"))
        if pa_key != self._pa_key:
            self._pa_key = pa_key
            self._pitch_high = 0

        if state.get("pitches", 0) > previous.get("pitches", 0):
            replayed = state.get("pitches", 0) <= self._pitch_high
            if state.get("game_state") != PITCH_GAME_STATE or replayed:
                self.replay_increments += 1
            else:
                self._pitch_high = state.get("pitches", 0)
                # A pitch still open when the next one is thrown never had its
                # outcome observed. It is emitted saying exactly that.
                if self.open is not None:
                    released.append(self._close(snapshot, "unknown", "superseded"))
                self._begin(snapshot, state)
        return released

    def flush(self) -> list:
        """Close a pitch still open at the end of the stream."""
        if self.open is None:
            return []
        snapshot = {"timer": self.open["last_timer"], "t": self.open["last_t"],
                    "state": self.previous_state or {}}
        return [self._close(snapshot, "unknown", "capture_ended")]

    # -- one pitch ---------------------------------------------------------

    def _begin(self, snapshot: dict, state: dict) -> None:
        pitcher = snapshot["actors"].get("P") or {}
        self.open = {
            "pitch_timer": snapshot["timer"],
            "t": snapshot["t"],
            "last_timer": snapshot["timer"],
            "last_t": snapshot["t"],
            "situation": dict(state),
            "pitcher_id": pitcher.get("character"),
            # Both counters can still be running down from the PREVIOUS pitch
            # when this one is thrown -- a swing's follow-through outlasts the
            # next release by dozens of frames -- so a value on its own says
            # nothing. What is recorded is whether each one ROSE while this
            # pitch was in the air, and separately what both read at the moment
            # the ball arrived. See _offer for why the second is the answer.
            "swing_shown": False,
            "bunt_shown": False,
            "swing_frames": 0,
            "bunt_frames": 0,
            "closest_units": None,
            "swing_at_plate": 0,
            "bunt_at_plate": 0,
            "swing_timer": None,
            # The ball's own flight, for speed and break. Trimmed at the plate
            # in _close: past that frame the samples belong to the batted ball
            # or the catcher, not to the pitch.
            "samples": [],
            "plate_index": None,
        }

    def _track(self, snapshot: dict, previous: dict, state: dict) -> None:
        pitch = self.open
        pitch["last_timer"] = snapshot["timer"]
        pitch["last_t"] = snapshot["t"]
        for counter, shown in (("swing_frames", "swing_shown"),
                               ("bunt_frames", "bunt_shown")):
            was = previous.get(counter) or 0
            now = state.get(counter) or 0
            if was == 0 and now > 0:
                pitch[shown] = True
            if pitch[shown]:
                pitch[counter] = max(pitch[counter], now)
        # WHERE THE BALL GOT CLOSEST TO THE PLATE, and what the bat was doing
        # there. A pitch resolves after the ball has gone by -- the count does
        # not change until it reaches the catcher -- so the resolving frame is
        # too late to read, and "the counter rose at some point" is too early.
        radius = math.hypot(snapshot["ball"][0], snapshot["ball"][2])
        pitch["samples"].append((snapshot["timer"], tuple(snapshot["ball"])))
        if pitch["closest_units"] is None or radius < pitch["closest_units"]:
            pitch["closest_units"] = radius
            pitch["swing_at_plate"] = state.get("swing_frames") or 0
            pitch["bunt_at_plate"] = state.get("bunt_frames") or 0
            pitch["plate_index"] = len(pitch["samples"]) - 1

    @staticmethod
    def _offer(pitch: dict) -> str:
        """How the batter offered: what the bat was doing when the ball arrived.

        NOT merely whether a counter rose. A batter can square to bunt and pull
        the bat back, and that is a taken pitch, not a bunt: the one in
        bowser_castle-20260828T182145Z held the stance for 4 frames and was out
        of it with the ball still 11 units away, then took the pitch for a
        strike. Read as a rise it was indistinguishable from the bunt Baby DK
        actually laid down, which held for 26 frames and was still running when
        the bat met the ball.

        The square that was abandoned is kept as `bunt_shown` rather than thrown
        away -- it is a real decision by the hitter, it is just not a bunt.
        """
        if pitch["bunt_at_plate"]:
            return "bunt"
        if pitch["swing_at_plate"]:
            return "swing"
        return "take"

    def _resolution(self, snapshot: dict, previous: dict, state: dict):
        """(outcome, how) once this pitch's outcome is observable, else None."""
        pitch = self.open
        # Contact is the same event PlayDeriver starts a play on, gated the same
        # way, so the two cannot disagree about whether a pitch was hit.
        if (previous.get("ball_was_hit") == 0 and state.get("ball_was_hit") == 1
                and state.get("game_state") == LIVE_BALL_GAME_STATE
                and math.hypot(snapshot["ball"][0], snapshot["ball"][2])
                <= CONTACT_BALL_RADIUS_UNITS):
            pitch["swing_timer"] = snapshot["timer"]
            return ("contact", "ball_was_hit")
        if state.get("balls", 0) > previous.get("balls", 0):
            return ("ball", "balls")
        if state.get("strikes", 0) > previous.get("strikes", 0):
            return ("strike", "strikes")
        # Strike three does not increment the count -- it resets it and retires
        # the batter -- so the end of the plate appearance is the only signal a
        # third strike leaves.
        ended = (state.get("outs", 0) > previous.get("outs", 0)
                 or state.get("batter_index") != previous.get("batter_index")
                 or state.get("inning") != previous.get("inning")
                 or state.get("inning_half") != previous.get("inning_half"))
        if ended:
            struck_out = (previous.get("strikes", 0) >= 2
                          and state.get("outs", 0) > previous.get("outs", 0))
            return ("strike" if struck_out else "unknown", "plate_appearance_ended")
        if snapshot["t"] - pitch["t"] > MAX_PITCH_RESOLUTION_SECONDS:
            return ("unknown", "timed_out")
        return None

    def _close(self, snapshot: dict, outcome: str, how: str) -> dict:
        pitch, self.open = self.open, None
        self.pitches_emitted += 1
        before = pitch["situation"]
        after = snapshot.get("state") or {}
        offer = self._offer(pitch)
        return {
            "pitch_timer": pitch["pitch_timer"],
            "resolved_timer": snapshot["timer"],
            "inning": before.get("inning"),
            "inning_half": before.get("inning_half"),
            "outs": before.get("outs"),
            "batter_id": before.get("batter_id"),
            "batter": character_name(before.get("batter_id")),
            "batter_index": before.get("batter_index"),
            "pitcher_id": pitch["pitcher_id"],
            "pitcher": character_name(pitch["pitcher_id"]),
            "pitch_in_pa": before.get("pitches"),
            "balls_before": before.get("balls"),
            "strikes_before": before.get("strikes"),
            "balls_after": after.get("balls"),
            "strikes_after": after.get("strikes"),
            # THE FACT THIS EXISTS FOR. The tracker log cannot see it, and it is
            # what separates a swinging strike from a called one.
            "offer": offer,
            "swing_frames": pitch["swing_frames"],
            "bunt_frames": pitch["bunt_frames"],
            # A stance the batter got into and came out of before the ball
            # arrived. `bunt_shown` without `offer == "bunt"` is a square that
            # was pulled back.
            "swing_shown": pitch["swing_shown"],
            "bunt_shown": pitch["bunt_shown"],
            "closest_units": (None if pitch["closest_units"] is None
                              else round(pitch["closest_units"], 3)),
            "outcome": outcome,
            "outcome_source": how,
            # The batted ball, if there was one. The play joined on this frame
            # owns everything about it -- fair or foul, the landing, the
            # fielding. `swing_timer` is the frame the swing started, which is
            # what the play carries under the same name; joining on the frame
            # the ball LEFT would need a tolerance, because the contact
            # animation holds it at the plate for 2 frames on an ordinary swing
            # and about 91 on a star swing.
            "contact": pitch["swing_timer"] is not None,
            "swing_timer": pitch["swing_timer"],
            **measure_pitch_metrics(
                pitch["samples"][:(pitch["plate_index"] or 0) + 1], fps=self.fps),
        }


def classify_batted_ball(live_flags: list) -> tuple:
    """The fair/foul call, the home-run call, and a single readable class."""
    fair_values = [fair for fair, _ in live_flags if fair]
    home_values = [home for _, home in live_flags if home]
    fair_or_foul = fair_values[-1] if fair_values else None
    home_run = HOME_RUN_FLAG_FAIR in home_values
    if FOUL_FLAG in fair_values:
        label = "foul_home_run" if home_values else "foul"
    elif home_run and FAIR_CAUGHT_FLAG in fair_values:
        # The game called it a home run AND called it caught in the air. That
        # contradiction is the event: a ball robbed at the wall. It is named
        # rather than resolved, because picking one of the two flags silently
        # would either credit the batter with a home run he did not get or hide
        # the hardest catch in the game inside the ordinary fly-out bucket.
        #
        # UNCONFIRMED that the game raises the home-run flag on a rob at all --
        # the three off-the-ground catches in the first session never did. If it
        # does not, this class simply never fires and nothing is mislabelled.
        label = "home_run_robbed"
    elif home_run:
        label = "home_run"
    elif FAIR_CAUGHT_FLAG in fair_values:
        label = "fair_caught"
    elif fair_values:
        label = "fair_in_play"
    else:
        # No call was ever made inside the live window. Nothing downstream
        # should treat this as a fair ball just because it is not marked foul.
        label = "unknown"
    return fair_or_foul, home_run, label


def is_complete_third_out_catch(active: dict) -> bool:
    """Whether a stuck/end-of-capture play already recorded its final out."""
    situation = active.get("situation", {})
    live_flags = active.get("live_flags", [])
    return (
        int(situation.get("outs", -1)) == 2
        and any(fair == FAIR_CAUGHT_FLAG for fair, _ in live_flags)
    )


def final_bases_ran(frames: list, name: str,
                    over_fence_home_run: bool = False) -> int:
    """How far this slot got before the game recycled it.

    The offense actors are reused for the next batter, and the count is torn
    down a few frames either side of the dead ball -- so the last frame of the
    play is the wrong place to read it. Ice Garden play 20 has R3 reach 4 on
    frame 32035 and drop back to 3 on 32155, two frames past the dead ball and
    still inside the play window; the batter on the same session's inside-the-
    park homer climbs 0..4 and is then zeroed. Reading the last frame threw both
    away, and every other advance in the archive with them.

    Scoring on an over-the-fence homer clears the slot outright rather than
    counting it to 4 -- and does so BEFORE the ball goes dead. For that one
    outcome the game's HR call is stronger than the recycled actor byte: every
    occupied runner scored, so return home explicitly. Otherwise take the last
    value before the first teardown drop, ignoring frames where the slot holds
    no runner.
    """
    if over_fence_home_run:
        return 4
    best = 0
    previous = None
    for snapshot in frames:
        actor = snapshot["actors"][name]
        if actor["index"] < 0:
            continue
        value = actor["bases_ran"]
        if previous is not None and value < previous:
            break
        best = previous = value
    return best


def build_play(active: dict, bags: dict, fps: float, truncated: bool) -> dict:
    frames = active["frames"]
    motion_frames = []
    seen_timers = set()
    for snapshot in active.get("pre_contact_frames", []) + frames:
        if snapshot["timer"] in seen_timers:
            continue
        seen_timers.add(snapshot["timer"])
        motion_frames.append(snapshot)
    contact_t = active["contact_t"]
    tracks = {}
    airborne = {}
    bobbles = {}
    forced_misplay_frames = {}
    buddy_handoff_frames = {}
    contact_counter_frames = {}
    fielding_action_frames = {}
    frozen_frames = {}
    frozen_onsets = {}
    freezing = set()
    knocked_frames = {}
    knocked_onsets = {}
    knocking = set()
    for snapshot in frames:
        for name, actor in snapshot["actors"].items():
            track = tracks.get(name)
            if track is None:
                track = tracks[name] = Track(
                    name, actor["character"], actor["index"],
                    actor.get("kind", "fielder"))
            track.add(snapshot["t"] - contact_t, actor["pos"])
            if actor["airborne"]:
                airborne[name] = airborne.get(name, 0) + 1
            # A freeze is one contiguous 120-frame run, but a fielder can be
            # caught twice in a play, so the onsets are a list rather than a
            # first-seen time. An onset is the transition into the flag, not
            # every frame it is up.
            #
            # EACH ONSET CARRIES ITS OWN LENGTH. It used to carry only its time,
            # and the record built below then gave every onset the fielder's
            # TOTAL frozen frames for the play, so a fielder caught twice was
            # published as two freezes of the summed duration. The operator read
            # a 4.0 s freeze at Peach Ice Garden and said it looked like two 2 s
            # freezes; it was, and 0x240 is up for exactly 120 frames, so a
            # single freeze cannot be 4 s in the first place.
            if actor.get("frozen"):
                if name not in freezing:
                    freezing.add(name)
                    frozen_onsets.setdefault(name, []).append(
                        [round(snapshot["t"] - contact_t, 4), 0])
                frozen_onsets[name][-1][1] += 1
                frozen_frames[name] = frozen_frames.get(name, 0) + 1
            else:
                freezing.discard(name)
            # Park-neutral on purpose: the flag says a fielder was floored and
            # never says by what.
            if actor.get("knocked_down"):
                if name not in knocking:
                    knocking.add(name)
                    knocked_onsets.setdefault(name, []).append(
                        [round(snapshot["t"] - contact_t, 4), 0])
                    knocked_onsets.setdefault(name + "@frame", []).append(
                        snapshot["timer"])
                knocked_onsets[name][-1][1] += 1
                knocked_frames[name] = knocked_frames.get(name, 0) + 1
            else:
                knocking.discard(name)
            action = actor["fielding_action"]
            if actor.get("contact_counter"):
                contact_counter_frames[name] = (
                    contact_counter_frames.get(name, 0) + 1)
            if action:
                counts = fielding_action_frames.setdefault(name, {})
                counts[action] = counts.get(action, 0) + 1
            if action in FIELDING_ACTION_MISPLAYS:
                bobbles[name] = bobbles.get(name, 0) + 1
            elif action == FIELDING_ACTION_YOSHI_EGG:
                forced_misplay_frames[name] = (
                    forced_misplay_frames.get(name, 0) + 1)
            elif action == FIELDING_ACTION_BUDDY_HANDOFF:
                buddy_handoff_frames[name] = buddy_handoff_frames.get(name, 0) + 1


    # First touch: the earliest frame in which the ball's own coordinates sit on
    # top of a fielder's. That coincidence is exact to the float when someone is
    # holding it, which makes it a far better signal than the game's
    # `ball_holder` byte -- that byte goes stale between plays and will name a
    # fielder while the ball is sitting on the mound.
    first_touch = None
    for snapshot in frames:
        name = locked_fielder(snapshot)
        if name is None:
            continue
        actor = snapshot["actors"][name]
        first_touch = {
            "t": round(snapshot["t"] - contact_t, 4),
            "frame": snapshot["timer"],
            "by": name,
            "character_id": actor["character"],
            "character": character_name(actor["character"]),
            "at": [round(v, 3) for v in actor["pos"]],
            # The BALL's height at the catch, which is the only height in the
            # capture that is real. The fielder's own reads 0 even mid-air, so
            # a wall climb or a leaping rob is visible here and nowhere else.
            "ball_height_units": round(snapshot["ball"][1], 3),
        }
        break

    # WHERE THE PLAY ACTUALLY ENDS. `ball_was_hit` stays set through the dead
    # ball, and the game then SNAPS every fielder back to their alignment spot.
    # Measuring through that reset is what made route efficiency read 0.000 --
    # start and end are literally the same coordinate, so displacement is zero
    # however far the fielder ran in between -- and let the reset glide count as
    # a 19.8 u/s sprint.
    #
    # The boundary is the game's own `game_state`, which leaves its live-ball
    # value two frames before the ball is teleported back to the mound. The
    # earlier version inferred it instead, from the first physically impossible
    # jump by any fielder, and that inference was wrong on 36 of 57 plays in the
    # first real session -- it ranged from 0.48s to 11.21s against a true live
    # window of 3.0s to 10.7s, because a pitcher's follow-through and an
    # outfielder's dive both snap far enough in one frame to look like a reset.
    # Cutting the play at 0.48s is what discarded most of the session's first
    # touches, and with them the throws and catch opportunities behind them.
    end_of_play = frames[-1]["t"] - contact_t
    live_end = (active["live_timer"] - active["contact_timer"]) / fps
    # Each actor is still windowed at ITS OWN reset if one somehow lands inside
    # the live window, so a single corrupt actor cannot contaminate its own
    # route without ending everyone else's play.
    tracks = {
        name: track.window(min(
            live_end,
            track.first_teleport_time()
            if track.first_teleport_time() is not None else end_of_play))
        for name, track in tracks.items()
    }
    # A fielder may be handed the ball by the game after the play is already
    # dead. That was the source of the real-session false touches found during
    # the audit. Possession later than the first defensive reset is not a
    # fielding event and cannot seed catch probability or a throw chain.
    if first_touch and first_touch["t"] > live_end + (1.0 / fps):
        first_touch = None

    fair_or_foul, home_run, batted_ball_class = classify_batted_ball(
        active["live_flags"])

    # Whether the ball was caught on the fly is the GAME's call, not an
    # inference from the height trace. On a low line drive the ball descends
    # below the landing threshold and stops there because it is in a glove, and
    # the detector cannot tell that from a short hop -- it read 22 of the
    # session's caught balls as having landed first. `fair_or_foul` reaching its
    # caught value says so directly, on the exact frame of the catch.
    caught_in_flight = batted_ball_class == "fair_caught"
    landing = (None if caught_in_flight
               else detect_landing(frames, contact_t, live_end, first_touch))
    # How each fielder went to the ball. This is a separate signal from the
    # action enum above: the enum says what happened to the ball once it was
    # reached, `catch_type` says whether the fielder had to dive or leap to
    # reach it, and a dive that never touches the ball sets only the latter.
    catch_approaches = detect_catch_approaches(frames, contact_t)
    buddy_jumps = detect_buddy_jumps(frames, contact_t, live_end)
    fielding_action_events = []
    for action_code in (*FIELDING_ACTION_MISPLAYS, FIELDING_ACTION_STAR_BALL,
                        FIELDING_ACTION_YOSHI_EGG,
                        FIELDING_ACTION_BUDDY_HANDOFF):
        fielding_action_events.extend(detect_fielding_action_events(
            frames, contact_t, live_end, first_touch, action_code))
    fielding_action_events.sort(key=lambda event: event["t"])
    for event in fielding_action_events:
        approach = approach_at(catch_approaches, event["by"], event["frame"])
        if approach is None:
            approach = approach_at(
                catch_approaches, event["by"], event["action_start_frame"])
        event.update(approach_fields(approach))
        if (event["action_code"] == 2
                and event["ball_contact"] == "unknown"
                and event["dive"]):
            event["ball_contact"] = "missed"
            event["confidence"] = "high"
            event["contact_source"] = "dive_action_without_contact_actor"

    # A BUDDY TOSS OPENS WITH THE MISPLAY ANIMATION. The enum sits at 2 while
    # the fielder reaches and flips to 7 on the frame he takes control of the
    # ball, and the contact actor is not written until that second frame -- so
    # the reach surfaced as its own attempt whose contact "could not be
    # determined", one frame before the toss confirmed it. It is ONE touch
    # described twice: all seven in the archive report the same trajectory turn
    # to three decimals as the handoff that follows, and in every one the two
    # animation windows are exactly contiguous. That is the guard -- same
    # fielder, action 2 ending on the frame a confirmed action 7 begins -- and
    # it leaves the eighth action-2 unknown, a 121-frame window with no toss
    # after it, quarantined where it belongs.
    #
    # None of the seven dove, so the dive rule above cannot reach them.
    lead_ins = set()
    for handoff in fielding_action_events:
        if (handoff["action_code"] != FIELDING_ACTION_BUDDY_HANDOFF
                or handoff["ball_contact"] != "confirmed"):
            continue
        for event in fielding_action_events:
            if (event["action_code"] in FIELDING_ACTION_MISPLAYS
                    and event["ball_contact"] == "unknown"
                    and event["by"] == handoff["by"]
                    and event["action_end_frame"] + 1
                        == handoff["action_start_frame"]):
                # Kept so the frame the fielder began reaching is not lost with
                # the phantom event; the toss's own window stays as measured.
                handoff["action_lead_in_frame"] = event["action_start_frame"]
                lead_ins.add(id(event))
    if lead_ins:
        fielding_action_events = [event for event in fielding_action_events
                                  if id(event) not in lead_ins]

    deflections = [event for event in fielding_action_events
                   if event["action_code"] in FIELDING_ACTION_MISPLAYS
                   and event["ball_contact"] == "confirmed"]
    forced_misplays = [event for event in fielding_action_events
                       if event["action_code"] in FIELDING_ACTION_FORCED
                       and event["ball_contact"] == "confirmed"]
    buddy_handoffs = [event for event in fielding_action_events
                      if event["action_code"] == FIELDING_ACTION_BUDDY_HANDOFF
                      and event["ball_contact"] == "confirmed"]

    # A BUDDY TOSS IS A SECURE AND A PASS. Action 7 takes control of the ball
    # and sends it to a teammate at a fixed speed on a new heading, so the
    # fielder who tossed it is the one who fielded the batted ball, and the
    # teammate who ends up holding it caught a toss. The possession lock only
    # sees the second half of that, which is why the console reported two
    # fielders "securing the ball after it landed" on every buddy toss and then
    # charged the ball to the wrong one.
    buddy_handoff_before_touch = next(
        (event for event in buddy_handoffs
         if first_touch is None or event["t"] < first_touch["t"]), None)
    if (buddy_handoff_before_touch and first_touch
            and buddy_handoff_before_touch["by"] == first_touch["by"]):
        buddy_handoff_before_touch = None

    # Possession is independently authoritative: the ball is both declared held
    # by the game and coordinate-locked to this actor. Add it as a secured event
    # even after an earlier boot, egg contact, or handoff so contact and outcome
    # remain separate facts rather than one overloaded label.
    fielding_events = list(fielding_action_events)
    if first_touch:
        touch_snapshot = next(
            (snapshot for snapshot in frames
             if snapshot["timer"] == first_touch["frame"]), None)
        touch_actor = (touch_snapshot["actors"].get(first_touch["by"])
                       if touch_snapshot else None)
        fielding_events.append({
            "event_type": "possession",
            "fielding_attempt": True,
            "ball_contact": "confirmed",
            "secured": True,
            "mechanic": ("buddy_receive" if buddy_handoff_before_touch
                         else "ordinary"),
            "received_from": (buddy_handoff_before_touch["by"]
                              if buddy_handoff_before_touch else None),
            "official_error": None,
            "confidence": "high",
            "contact_source": "possession_lock",
            **first_touch,
            "action_code": (touch_actor.get("fielding_action", 0)
                            if touch_actor else 0),
            # The byte clears on the frame the glove closes, so the window that
            # produced this catch is the one that ended just before it.
            **approach_fields(approach_at(
                catch_approaches, first_touch["by"], first_touch["frame"])),
        })
    fielding_events.sort(key=lambda event: event["t"])
    failed_contacts = sorted(
        [*deflections, *forced_misplays], key=lambda event: event["t"])
    for event in failed_contacts:
        # A low-ball turning point and a glove deflection can be stamped on the
        # same 60 Hz frame. That frame is the contact, not proof the ball had
        # already landed: Bowser Castle PA 12 (2026-09-05) was an airborne RF
        # boot whose apparent landing and confirmed contact were both frame
        # 12567. Calling equality "before" preserved a double that the fielder
        # never allowed to land instead of scoring the error.
        event["ball_landed_before_contact"] = bool(
            landing and landing["frame"] < event["frame"])
    deflected_by = {entry["by"] for entry in failed_contacts}
    forced_misplay_by = {entry["by"] for entry in forced_misplays}

    # Charge the original batted-ball opportunity to the fielder who actually
    # had to make it. First touch is only correct on a clean catch: after a boot
    # it names the eventual pickup (or the teammate who saved the error), which
    # gives the miss to the wrong player and can turn a baserunner out into a
    # successful catch. That case is already gone by the time we get here --
    # `failed_contacts` takes it -- so anyone left holding the first touch got
    # to the ball cleanly and is the fielder who made the play.
    #
    # WHERE A GROUND BALL LANDS IS NOT WHERE IT WAS FIELDED. A grounder touches
    # down a few units off the plate and then rolls out through the infield, so
    # "the fielder closest when it came down" names whoever happened to be
    # standing near the bounce -- the pitcher on a ball hit past him, the
    # catcher on a chopper or a bunt -- while the ball is already going by them.
    # On bowser_castle-20260828T182145Z this rule was used on 31 plays and
    # disagreed with the fielder who actually picked the ball up on 22 of them,
    # including the bunt Baby DK laid down: charged to the catcher, fielded by
    # the pitcher four units later. The tracker's own scoring notation for those
    # plays (G5-3 on the ball this charged to the pitcher) agrees with the touch,
    # not with the landing.
    #
    # So the landing only decides it when nobody ever touched the ball, which is
    # the case that rule was written for: a ball that falls in untouched, where
    # the fielder who had the chance is the one who was closest to where it came
    # down.
    primary_fielder = None
    primary_reason = None
    if failed_contacts:
        primary_fielder = failed_contacts[0]["by"]
        primary_reason = ("forced_misplay" if failed_contacts[0] in forced_misplays
                          else "failed_contact")
    elif buddy_handoff_before_touch:
        primary_fielder = buddy_handoff_before_touch["by"]
        primary_reason = "buddy_handoff"
    elif caught_in_flight and first_touch:
        primary_fielder = first_touch["by"]
        primary_reason = "catch"
    elif first_touch:
        primary_fielder = first_touch["by"]
        primary_reason = "fielded"
    elif landing and batted_ball_class == "fair_in_play":
        landing_snapshot = next(
            (snapshot for snapshot in frames
             if snapshot["timer"] == landing["frame"]), None)
        if landing_snapshot:
            bx, _, bz = landing_snapshot["ball"]
            candidates = []
            for name, actor in landing_snapshot["actors"].items():
                if actor["kind"] != "fielder":
                    continue
                px, _, pz = actor["pos"]
                candidates.append((math.dist((px, pz), (bx, bz)), name))
            if candidates:
                primary_fielder = min(candidates)[1]
                primary_reason = "closest_at_landing"
    # Hang time is time to the ball's own first contact with anything: the glove
    # if it was caught, the ground if it was not. A fielder who was never going
    # to reach it still had exactly this long to try.
    hang_time = first_touch["t"] if caught_in_flight and first_touch else (
        landing["t"] if landing else None)

    # Where the ball had to be reached: the glove if it was caught on the fly,
    # otherwise the spot it came down on.
    opportunity_target = None
    if caught_in_flight and first_touch:
        opportunity_target = tuple(first_touch["at"])
    elif landing:
        opportunity_target = tuple(landing["at"])

    home = tuple(frames[0]["ball"]) if frames else None
    # The state scalars name fielders by their index in the capture's actor
    # table, which is the order they were added to each snapshot.
    order = [name for name, actor in frames[0]["actors"].items()
             if actor["kind"] == "fielder"]
    throws = detect_throws(frames, contact_t, live_end, bags, order, home)
    possession_carries = detect_possession_carries(frames, contact_t, live_end)
    name_carry_impulses(possession_carries, first_touch, throws, knocked_onsets)
    knockdowns = []
    fielders = {}
    for name, track in tracks.items():
        if frames[0]["actors"][name]["kind"] != "fielder":
            continue
        entry = summarise_track(track, frozen_frames=frozen_frames.get(name, 0))
        entry["airborne_frames"] = airborne.get(name, 0)
        entry["frozen_frames"] = frozen_frames.get(name, 0)
        entry["frozen_seconds"] = round(frozen_frames.get(name, 0) / fps, 4)
        # SOMEBODY WENT DOWN. The flag is read straight off the fielder and
        # says only that: a floored fielder covers no ground under his own
        # power and drops whatever he was holding, which is why the record
        # exists. What floored him is not measured here.
        entry["knocked_down_frames"] = knocked_frames.get(name, 0)
        entry["knocked_down_seconds"] = round(knocked_frames.get(name, 0) / fps, 4)
        for (onset, run_frames), at_frame in zip(
                knocked_onsets.get(name, []),
                knocked_onsets.get(name + "@frame", [])):
            knockdowns.append({
                "by": name,
                "character_id": track.character,
                "character": character_name(track.character),
                "t": onset,
                "frame": at_frame,
                "frames": run_frames,
                "seconds": round(run_frames / fps, 4),
            })
        approaches = [w for w in catch_approaches if w["by"] == name]
        entry["dove"] = any(w["dive"] for w in approaches)
        entry["leaped"] = any(w["leap"] for w in approaches)
        entry["dive_frames"] = sum(w.get("frames", 0)
                                   for w in approaches if w["dive"])
        entry["catch_type_frames"] = {
            str(code): sum(w.get("frames", 0) for w in approaches
                           if w["catch_type"] == code)
            for code in sorted({w["catch_type"] for w in approaches})
        }
        entry["bobble_frames"] = bobbles.get(name, 0)
        entry["forced_misplay_frames"] = forced_misplay_frames.get(name, 0)
        entry["buddy_handoff_frames"] = buddy_handoff_frames.get(name, 0)
        entry["fielding_contact_counter_frames"] = contact_counter_frames.get(name, 0)
        entry["fielding_action_frames"] = {
            str(code): count
            for code, count in sorted(fielding_action_frames.get(name, {}).items())
        }
        release_timer = active.get("pitch_release_timer")
        release_snapshot = next(
            (snapshot for snapshot in motion_frames
             if release_timer is not None
             and snapshot["timer"] >= release_timer
             and name in snapshot["actors"]),
            None,
        )
        if release_snapshot:
            entry["pitch_release_start"] = [
                round(value, 3)
                for value in release_snapshot["actors"][name]["pos"]
            ]
        if first_touch and track.points:
            entry["distance_to_touch_units"] = round(
                math.dist(track.points[0][::2], tuple(first_touch["at"])[::2]), 3)
            entry["opportunity_s"] = first_touch["t"]
            entry["fielded"] = name == first_touch["by"]
        if track.points and opportunity_target:
            # The catch-probability pair, and the reason it is measured against
            # the landing point rather than against first touch: on a ball that
            # falls in, first touch is where somebody picked it up after the
            # bounce. Scoring a fly ball's catch point against a base hit's
            # pickup point makes the two look like the same chance, which is
            # exactly what the first session's numbers did -- the required
            # closing speed came out at 4.41 u/s for balls caught and 4.37 for
            # balls that dropped, and every difficulty band converted at 52%.
            entry["distance_to_landing_units"] = round(
                math.dist(track.points[0][::2], opportunity_target[::2]), 3)
            entry["hang_time_s"] = hang_time
            # Reached the ball and did not hold it. Distinct from never having
            # got there, which is what an unqualified miss looks like.
            entry["deflected"] = name in deflected_by
            entry["forced_misplay"] = name in forced_misplay_by
            jump = jump_measurement(
                motion_frames, name, first_touch,
                release_timer, fps)
            if jump:
                entry.update(jump)
        fielders[name] = entry

    # A floored fielder's dropped ball is not a throw. The knockdown flag is
    # read straight off the fielder, so this holds at every park and names no
    # cause for the fall.
    name_knocked_loose_throws(throws, knockdowns)

    ball_path = measure_ball_path(frames, contact_t, live_end, first_touch,
                                  possession_carries)
    batted_ball_metrics = measure_batted_ball_metrics(
        frames, fps=fps, landing=landing, first_touch=first_touch)

    rebound_catch = None
    if (caught_in_flight and failed_contacts and first_touch
            and first_touch["by"] not in deflected_by):
        deflection = failed_contacts[0]
        deflection_snapshot = next(
            (snapshot for snapshot in frames
             if snapshot["timer"] == deflection["frame"]), None)
        saver = first_touch["by"]
        if deflection_snapshot and saver in deflection_snapshot["actors"]:
            start = deflection_snapshot["actors"][saver]["pos"]
            end = tuple(first_touch["at"])
            rebound_catch = {
                "deflected_by": deflection["by"],
                "saved_by": saver,
                "start_frame": deflection["frame"],
                "catch_frame": first_touch["frame"],
                "opportunity_s": round(first_touch["t"] - deflection["t"], 4),
                "distance_needed_units": round(
                    math.dist(start[::2], end[::2]), 3),
                "start_at": [round(value, 3) for value in start],
                "catch_at": list(first_touch["at"]),
            }

    runners = {}
    for name, track in tracks.items():
        if frames[0]["actors"][name]["kind"] != "offense":
            continue
        occupied = [s["actors"][name]["index"] >= 0 for s in frames]
        if not any(occupied):
            continue
        entry = summarise_track(track)
        entry["bases_ran"] = final_bases_ran(
            frames, name,
            over_fence_home_run=batted_ball_class == "home_run")
        entry["stealing"] = max(s["actors"][name]["stealing"] for s in frames)
        # Lead off the bag at contact, measured against the bag this slot owns.
        bag = bags.get(name)
        if bag and track.points:
            entry["lead_at_contact_units"] = round(
                math.dist(track.points[0][::2], bag[::2]), 3)
        entry["five_foot_splits_s"] = cumulative_distance_splits(track)
        runners[name] = entry

    # Home to first, for the batter-runner. The first-base bag is whatever the
    # empty R1 slot measured, so this is right in any park.
    home_to_first = None
    ninety_foot_split = None
    bag = bags.get("R1")
    if bag and "BAT" in tracks:
        track = tracks["BAT"]
        # Time of CLOSEST APPROACH, not of first entering a radius around the
        # bag. A fixed radius is a fixed head start: at 8 u/s a 1.5u trigger
        # reports every runner 0.19s faster than they were, and the whole range
        # between the fastest and slowest character is smaller than that.
        best = None
        for t, point in zip(track.times, track.points):
            distance = math.dist(point[::2], bag[::2])
            if best is None or distance < best[0]:
                best = (distance, t)
        # If the closest approach is the LAST sample, the runner was still
        # closing when the window ended and the "time to first" is really the
        # time the recording stopped watching. Three plays reported an
        # identical 5.639s that way.
        still_running = bool(track.times) and best is not None and             best[1] >= track.times[-1] - 1e-9
        if best and best[0] <= BAG_RADIUS_UNITS and not still_running:
            home_to_first = round(best[1], 4)
            actual_distance_feet = (math.dist(track.points[0][::2], bag[::2]) * 3.280839895
                                    if track.points else None)
            if actual_distance_feet and actual_distance_feet > 0:
                ninety_foot_split = round(home_to_first * 90.0 / actual_distance_feet, 4)

    situation = active["situation"]
    # HOW THE BATTER OFFERED AT IT, measured rather than inferred from how hard
    # the ball left. `situation` is the state at the frame the swing flag rose,
    # and the game freezes the swing animation on contact, so the counter that
    # is running there is the animation that produced this batted ball. Exactly
    # one of the two was non-zero at all 96 contacts of the session this was
    # read from. A bunt therefore no longer has to be guessed from exit
    # velocity, which cannot tell a bunt from a swing that was simply mishit.
    swing_frames = situation.get("swing_frames")
    bunt_frames = situation.get("bunt_frames")
    contact_type = None
    if bunt_frames:
        contact_type = "bunt"
    elif swing_frames:
        contact_type = "swing"

    # The game can replay the entire batted-ball sequence from home plate while
    # keeping `game_state == live` and raising `ball_was_hit` again. That defeats
    # both launch gates. The replay is byte-for-byte deterministic in the ball
    # trace for as long as it runs -- it may be cut early, after which the ball
    # parks at its reset position -- so retain a short internal signature for the
    # post-pass below, which compares only the leading samples. It is removed
    # before JSON is written.
    signature_step = max(1, round(fps * REPLAY_SIGNATURE_STEP_SECONDS))
    signature_limit = min(len(frames),
                          round(fps * REPLAY_SIGNATURE_SECONDS) + 1)
    trajectory_signature = tuple(
        tuple(round(value, 3) for value in frames[index]["ball"])
        for index in range(0, signature_limit, signature_step)
    )
    return {
        "contact_timer": active["contact_timer"],
        "swing_timer": active.get("swing_timer"),
        "pitch_release_timer": active.get("pitch_release_timer"),
        "inning": situation["inning"],
        "inning_half": situation["inning_half"],
        "outs": situation["outs"],
        "balls": situation["balls"],
        "strikes": situation["strikes"],
        "batter_id": situation["batter_id"],
        "batter": character_name(situation["batter_id"]),
        "batter_index": situation["batter_index"],
        "duration_s": round(frames[-1]["t"] - contact_t, 4),
        "live_s": round(live_end, 4),
        "dead_ball_timer": round(active["contact_timer"] + live_end * fps),
        "swing_to_launch_s": active.get("swing_to_launch_s"),
        "contact_type": contact_type,
        "bunt": contact_type == "bunt" if contact_type else None,
        "swing_frames": swing_frames,
        "bunt_frames": bunt_frames,
        "truncated": truncated,
        "fair_or_foul": fair_or_foul,
        "home_run": home_run,
        "batted_ball_class": batted_ball_class,
        "contact_at": [round(v, 3) for v in frames[0]["ball"]],
        "batted_ball": batted_ball_metrics,
        "landing": landing,
        "deflections": deflections,
        "forced_misplays": forced_misplays,
        "buddy_handoffs": buddy_handoffs,
        # Two fielders boosting one over the wall. This leaves no action window
        # and no possession, so without it a ball they went up for reads as a
        # ball nobody played.
        "buddy_jumps": buddy_jumps,
        # THE MEASURED PATH, for anything that draws the play. Sampled ball
        # positions from contact to the first glove -- see measure_ball_path.
        # This is the only field that describes a home run's flight or a ball
        # that left play: both have a null `landing` and a null `first_touch`.
        "ball_path": ball_path,
        # Somebody went down. The flag is read off the fielder and says only
        # that, never why: naming the cause is stadium research and is not part
        # of this build.
        "knockdowns": knockdowns,
        # Every dive and leap on the play, including the ones that never
        # reached the ball -- those produce no fielding event at all, and they
        # are exactly the range plays worth seeing.
        "catch_approaches": catch_approaches,
        "dives": [w for w in catch_approaches if w["dive"]],
        "fielding_events": fielding_events,
        "primary_fielder": primary_fielder,
        "primary_fielder_reason": primary_reason,
        # The catch that follows a deflection is a rebound, and the chance it
        # represents was created by the boot rather than by the batted ball.
        "after_deflection": rebound_catch is not None,
        "rebound_catch": rebound_catch,
        "hang_time_s": hang_time,
        "caught_in_flight": caught_in_flight,
        "first_touch": first_touch,
        "throws": throws,
        "possession_carries": possession_carries,
        "home_to_first_s": home_to_first,
        "ninety_foot_split_s": ninety_foot_split,
        "fielders": fielders,
        "runners": runners,
        "_trajectory_signature": trajectory_signature,
    }


# How much of a replay has to agree with the original before it is treated as a
# copy. The game does not always run a replay to completion: the Ice Garden
# ninth-inning copy was identical for 1.42s and was then cut, parking the ball
# at its reset position. Comparing the whole signature window therefore misses
# short replays, so only the leading samples are required to agree. Five
# samples is 1.0s of flight matching to the millimetre, which no two distinct
# batted balls in any captured session have ever done. A Wario City close-play
# replay ended its live phase at 0.734s, however, leaving only three comparable
# samples before the ball reset. That shorter form is accepted only when the
# contact, landing, swing lead and pitch lead also repeat exactly.
REPLAY_SIGNATURE_SECONDS = 2.5
REPLAY_SIGNATURE_STEP_SECONDS = 0.25
REPLAY_MIN_MATCHING_SAMPLES = 5
REPLAY_SHORT_MIN_MATCHING_SAMPLES = 3
REPLAY_SHORT_LIVE_SECONDS = 1.0
REPLAY_WINDOW_SECONDS = 20.0


def matching_prefix(left: tuple, right: tuple) -> int:
    """How many leading trajectory samples two plays agree on exactly."""
    count = 0
    for a, b in zip(left, right):
        if a != b:
            break
        count += 1
    return count


def replay_fingerprint(play: dict):
    """Exact event landmarks that distinguish a cut-short replay from a hit."""
    contact = play.get("contact_at")
    timer = play.get("contact_timer")
    swing = play.get("swing_timer")
    pitch = play.get("pitch_release_timer")
    if (not isinstance(contact, (list, tuple))
            or timer is None or swing is None or pitch is None):
        return None
    # NO LANDING IN HERE. It used to be, and that made the fingerprint None for
    # exactly the records this branch exists to catch: a copy cut off before the
    # ball comes down never has one, so the short branch could never fire.
    #
    # AND THE LEADS ARE MEASURED SWING-TO-PITCH, NOT FROM CONTACT. Contact-
    # relative leads agree on 19 of the archive's 22 original/copy pairs and
    # disagree on all three whose original is a HOME RUN: the home-run replay
    # re-raises the hit flag exactly 90 frames off its own swing, so the copy
    # reads a 1-frame swing lead where the original read 91. The interval
    # BETWEEN the swing and the pitch is untouched by that shift and matches on
    # all 22, as does the contact point to the millimetre. Two real swings share
    # neither.
    return (tuple(contact), swing - pitch)


def replay_trajectories_match(previous_trajectory: tuple, trajectory: tuple,
                              previous_fingerprint, fingerprint,
                              live_seconds) -> bool:
    """Whether this is a full replay trace or an exactly identified short one."""
    prefix = matching_prefix(previous_trajectory, trajectory)
    if prefix >= REPLAY_MIN_MATCHING_SAMPLES:
        return True
    try:
        live = float(live_seconds)
    except (TypeError, ValueError):
        return False
    if not 0 <= live < REPLAY_SHORT_LIVE_SECONDS:
        return False
    # ASK THE COPY FOR WHAT IT COULD HAVE, NOT FOR THREE. The signature samples
    # every 0.25 s, so a replay the game cut off after five frames -- a third of
    # one interval -- can only ever supply the contact sample; everything after
    # it is the ball parked at its reset position, which agrees with nothing.
    # Demanding three samples of that record demands evidence that cannot exist,
    # and one such copy reached the Mario Stadium feed as a 112th play. A copy
    # long enough for three still has to produce three.
    available = 1 + int(live / REPLAY_SIGNATURE_STEP_SECONDS)
    return (
        prefix >= min(REPLAY_SHORT_MIN_MATCHING_SAMPLES, available)
        and fingerprint is not None
        and fingerprint == previous_fingerprint
    )


if __name__ == "__main__":
    sys.exit(main())
