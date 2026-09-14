"""Run the play derivation while the game is still being played.

The collector already reads every frame; this turns those frames into finished
    plays at the dead ball instead of hours later, without changing what it writes
to disk. Three rules shape the whole module:

  1. THE RAW CAPTURE IS UNTOUCHED. The .bin and its header are written exactly
     as before, and the postgame pass over them stays authoritative. Live
     derivation is an extra consumer of frames the collector already had in
     hand, never a substitute for the recording.

  2. ONE IMPLEMENTATION. Snapshots come from player_tracking_io.SnapshotBuilder
     and plays from derive_player_metrics.PlayDeriver -- the same two objects
     the postgame pass uses. There is no live-specific derivation to drift.

  3. A CALIBRATION THAT WAS NOT CONFIRMED EMITS NO PLAYS. Postgame, the position
     offset is scored over the whole session before anything is derived. Live
     there is no such luxury: the offset has to be chosen at frame zero and the
     wrong one produces confident, plausible, wrong numbers rather than an
     error. So the offset is checked against the game itself -- a fielder
     holding the ball stands exactly on it, to the float -- and until enough of
     those coincidences have been seen, plays are derived but withheld.

     PITCHES ARE NOT WITHHELD, because nothing in one can be wrong for that
     reason: a pitch record is read entirely from the state scalars -- the
     count, the batter, the two swing animation counters -- and none of them is
     a coordinate. `take_pitches` returns them whatever the calibration says.

The check is cheap because it is the same coincidence `locked_fielder` already
looks for, and it is decisive because it cannot happen by accident: two
unrelated float triples do not agree to within 0.05 units on three axes.
"""
from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..normalize import sanitize_play
from .derive import (
    GAME_FRAME_RATE,
    PitchDeriver,
    PlayDeriver,
    locked_fielder,
)
from .tracking_io import SnapshotBuilder, dumps_play

# Every real capture so far has resolved to +0x004, and by a decisive margin
# (9,234 lock frames against 3,993 for the runner-up in the widest one). It is
# a seed, not an assumption: the self-check below has to confirm it against the
# session actually being recorded before a single play is released.
DEFAULT_POSITION_OFFSET = 0x004

# How many frames of a fielder standing exactly on the ball are needed before
# the offset is believed.
#
# THE PITCHER SUPPLIES NONE. This used to say the pitcher holding the ball
# between pitches cleared it before the first batted ball; measured across 45
# sessions, 43 have zero lock frames before first contact. The first play a
# fielder touches has to confirm it alone, and it supplies 36-241 frames. At 60,
# 11 of those 45 first plays fell short (36-59) -- yoshi_park-20260911T164801Z
# PA1 had 59 -- and sat withheld until the NEXT fielded play, which is exactly
# what the operator kept seeing. 30 clears every one of them inside its own play.
#
# It costs the check nothing. A wrong seed reading random floats never matches
# the ball to 0.05u at all, so 30 is as decisive against it as 60; the one real
# alternative position field (+0x38) locks often enough to pass either.
CONFIRM_LOCK_FRAMES = 30

# If that many plays have gone by with no lock at all, the offset is wrong (or
# the ball pointer is stale) and live derivation gives up for the session. The
# recording carries on regardless, and the postgame pass will do it properly.
GIVE_UP_AFTER_PLAYS = 4

# How long the interpreter may hold the GIL before offering it to another
# thread. The default 5 ms is a third of a frame, which is enough for the
# deferred play build to make the capture loop late; 1 ms keeps the loop's
# worst case comfortably inside its 14.9 ms of headroom. Set once, by the
# process that actually runs a capture loop.
GIL_SWITCH_SECONDS = 0.001


class LiveDerivation:
    """The collector's live consumer of frames.

    `feed` is called once per captured frame and returns the plays that
    completed on it -- almost always empty, and one on the frame the ball goes
    dead. Each returned play is also appended to `<stem>.live.jsonl` so a
    supervising tracker process can read
    completed plays without parsing the collector's stdout.
    """

    def __init__(self, *, state_base: int, actors: list, fields: dict,
                 state_fields: list, out_path: Path | str | None,
                 pitches_path: Path | str | None = None,
                 position_offset: int = DEFAULT_POSITION_OFFSET,
                 ball_frame: dict | None = None,
                 fps: float = GAME_FRAME_RATE,
                 state_size: int | None = None, extra_regions=()):
        self.builder = SnapshotBuilder(
            state_base=state_base, actors=actors, fields=fields,
            state_fields=state_fields, position_offset=position_offset,
            state_size=state_size, extra_regions=extra_regions,
            # The ball-frame convention has been identical in every capture and
            # is not what live derivation is at risk from; the position offset
            # is.
            ball_frame=ball_frame or {"sign_x": 1.0, "sign_z": -1.0,
                                      "swap_xz": False},
            fps=fps,
        )
        # One worker, so plays are built in the order they closed while the
        # capture loop keeps reading frames. See PlayDeriver.build_executor for
        # why this is not optional at 60 Hz.
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix='play-build')
        sys.setswitchinterval(GIL_SWITCH_SECONDS)
        self.deriver = PlayDeriver(fps=fps, build_executor=self._executor)
        # PITCHES ARE NOT GATED ON THE CALIBRATION. Everything a pitch record
        # holds -- the count, the batter, how they offered -- is read from the
        # state scalars, which do not depend on the position offset at all. A
        # session whose offset never confirms still has a correct pitch stream,
        # and withholding it would be withholding data that cannot be wrong for
        # the reason plays are withheld.
        self.pitcher = PitchDeriver(fps=fps)
        self._pitches = []
        self.position_offset = position_offset
        self.out_path = Path(out_path) if out_path else None
        self._sink = None
        if self.out_path:
            self.out_path.parent.mkdir(parents=True, exist_ok=True)
            self._sink = self.out_path.open("w", buffering=1)
        self.pitches_path = Path(pitches_path) if pitches_path else None
        self._pitch_sink = None
        if self.pitches_path:
            self.pitches_path.parent.mkdir(parents=True, exist_ok=True)
            self._pitch_sink = self.pitches_path.open("w", buffering=1)

        self.calibration_status = "pending"
        # Plays derived before the offset was confirmed. They are not wrong --
        # they were derived with the same offset that is about to be confirmed
        # -- they are merely unproven, so they wait here rather than being
        # published or thrown away. Confirmation releases them in order;
        # failure discards them and the postgame pass recovers them properly.
        self._held = []
        self.lock_frames = 0
        self.plays_seen = 0
        self.plays_emitted = 0
        self.plays_withheld = 0
        # Wall time spent inside feed(), so the collector can report what live
        # derivation actually costs it rather than asserting that it is cheap.
        self.feed_seconds = 0.0
        self.frames_fed = 0
        self.max_feed_seconds = 0.0

    # -- the calibration self-check ---------------------------------------

    def _note_lock(self, snapshot: dict) -> None:
        if self.calibration_status == "confirmed":
            return
        if locked_fielder(snapshot) is not None:
            self.lock_frames += 1
            if self.lock_frames >= CONFIRM_LOCK_FRAMES:
                self.calibration_status = "confirmed"

    def _check_give_up(self) -> None:
        if self.calibration_status != "pending":
            return
        if self.plays_seen >= GIVE_UP_AFTER_PLAYS and self.lock_frames == 0:
            self.calibration_status = "failed"
            self.plays_withheld += len(self._held)
            self._held.clear()

    def _release(self, play: dict) -> dict:
        play = sanitize_play(play)
        self.plays_emitted += 1
        if self._sink:
            self._sink.write(dumps_play(play) + "\n")
        return play

    # -- the frame loop ----------------------------------------------------

    def feed(self, timer: int, ball: tuple, block, pointers=None) -> list:
        """One captured frame. Returns the plays released by it.

        Pitches resolved by the same frame are collected for `take_pitches`
        rather than returned here, and go on being collected after the offset
        has been given up on: a failed calibration says nothing about the count,
        the batter or how they offered, which is everything a pitch record is.
        """
        started = time.perf_counter()
        snapshot = self.builder.build(timer, ball, block)
        self._pitches.extend(self.pitcher.feed(snapshot))
        if self.calibration_status == "failed":
            self.feed_seconds += time.perf_counter() - started
            self.frames_fed += 1
            return []
        if pointers is not None:
            self.deriver.note_pointers(pointers)
        self._note_lock(snapshot)
        completed = self.deriver.feed(snapshot)
        released = []
        for play in completed:
            self.plays_seen += 1
            # A play derived before the offset was confirmed is not published:
            # if the offset turns out to be wrong, everything in it is wrong in
            # a way that reads as data rather than as an error. It is held, not
            # discarded -- the first confirmed lock proves the same offset that
            # produced it, so the whole backlog becomes publishable at once.
            if self.calibration_status == "confirmed":
                released.append(self._release(play))
            else:
                self._held.append(play)
                self._check_give_up()
        if self.calibration_status == "confirmed" and self._held:
            released = [self._release(held) for held in self._held] + released
            self._held.clear()
        elapsed = time.perf_counter() - started
        self.feed_seconds += elapsed
        self.frames_fed += 1
        self.max_feed_seconds = max(self.max_feed_seconds, elapsed)
        return released

    def take_pitches(self) -> list:
        """The pitches resolved since this was last called.

        Separate from `feed`'s return value because the two are gated
        differently -- see the PitchDeriver comment in __init__ -- and because a
        caller that only wants plays should not have to know pitches exist.
        """
        out, self._pitches = self._pitches, []
        return [self._record_pitch(pitch) for pitch in out]

    def _record_pitch(self, pitch: dict) -> dict:
        """Stamp a pitch and put it in the file, at most once.

        `close` records the last pitch before it closes the sink, so that a
        capture that stops mid-plate-appearance still has it on disk; the
        caller's own `take_pitches` afterwards then finds the sink already gone
        and only hands the record back to be printed.
        """
        pitch["derivation"] = "live"
        if self._pitch_sink:
            self._pitch_sink.write(dumps_play(pitch) + "\n")
        return pitch

    # -- reporting ---------------------------------------------------------

    def status(self) -> dict:
        """What the collector prints, and what the manifest records."""
        mean_ms = (self.feed_seconds / self.frames_fed * 1000.0
                   if self.frames_fed else 0.0)
        return {
            "calibration_status": self.calibration_status,
            "position_offset": self.position_offset,
            "lock_frames": self.lock_frames,
            "plays_seen": self.plays_seen,
            "plays_emitted": self.plays_emitted,
            "plays_withheld": self.plays_withheld,
            "live_path": str(self.out_path) if self.out_path else None,
            "pitches_path": str(self.pitches_path) if self.pitches_path else None,
            "mean_feed_ms": round(mean_ms, 4),
            "max_feed_ms": round(self.max_feed_seconds * 1000.0, 3),
            "max_play_build_ms": round(self.deriver.max_build_seconds * 1000.0, 1),
            "replay_duplicates": self.deriver.replay_duplicates,
            "pitches_emitted": self.pitcher.pitches_emitted,
            "pitch_replay_increments": self.pitcher.replay_increments,
        }

    def close(self) -> list:
        """Release a play still open when the capture stops."""
        for pitch in self.pitcher.flush():
            self._pitches.append(self._record_pitch(pitch))
        released = []
        if self.calibration_status == "confirmed":
            for play in self._held + self.deriver.flush():
                released.append(self._release(play))
            self._held.clear()
        else:
            self.plays_withheld += len(self._held) + len(self.deriver.flush())
            self._held.clear()
        if self._sink:
            self._sink.close()
            self._sink = None
        if self._pitch_sink:
            self._pitch_sink.close()
            self._pitch_sink = None
        self._executor.shutdown(wait=True)
        return released
