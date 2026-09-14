"""Internal 60 Hz capture worker for the public advanced Excel workbook.

The normal tracker starts and stops this worker automatically. Its temporary
capture is removed after the workbook is safely written; this command-line
entry point exists only for process isolation and diagnostics.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import struct
import sys
import time
import zlib
from datetime import datetime, timezone
from pathlib import Path

from .ball_memory import (
    BALL_POINTER_SLOT,
    hook,
    plausible_position,
    resolve_offset,
)
from .live import DEFAULT_POSITION_OFFSET, LiveDerivation
from .tracking_io import dumps_play

STATE_BASE = 0x900D5000
STATE_END = 0x900DBD40
STATE_SIZE = STATE_END - STATE_BASE
GAME_TIMER = 0x900DFCFC
FIELDER_POINTER_TABLE = 0x80708D78
POSITION_NAMES = ["P", "C", "1B", "2B", "3B", "SS", "LF", "CF", "RF"]
FIELDER_STRIDE = 0x2EC
FIELDER_VTABLE = 0x806434E8
OFFENSE_BASE = 0x900DB5D0
OFFENSE_STRIDE = 0x1D4
OFFENSE_VTABLE = 0x8064A318
OFFENSE_NAMES = ["BAT", "R1", "R2", "R3"]
ACTOR_FIELDS = {
    "vtable": 0x000,
    "position_a": 0x004,
    "scale": 0x010,
    "position_b": 0x038,
    "batting_index": 0x029,
    "character_id": 0x02B,
    "buddy_jump_flag": 0x223,
    "airborne_flag": 0x22E,
    "catch_type": 0x2AC,
    "bobble_flag": 0x2B2,
    "fielding_contact_counter": 0x2C4,
    "frozen_flag": 0x240,
    "frozen_timer": 0x20D,
    "knockdown_flag": 0x23F,
    "bases_ran": 0x17D,
    "is_stealing": 0x198,
}
STATE_FIELDS = [
    ("inning", 0x900D5D97, "B"),
    ("inning_half", 0x900D5E25, "B"),
    ("outs", 0x900D5AA9, "B"),
    ("balls", 0x900D5AA8, "B"),
    ("strikes", 0x900D5AA7, "B"),
    ("game_state", 0x900D5C28, "B"),
    ("last_state", 0x900D5C29, "B"),
    ("ball_was_hit", 0x900D6A94, "B"),
    ("ball_status", 0x900D953A, "B"),
    ("home_run_flag", 0x900D953C, "B"),
    ("ball_holder", 0x900D66C9, "b"),
    ("last_ball_holder", 0x900D5056, "B"),
    ("batter_id", 0x900D69EF, "B"),
    ("batter_index", 0x900DB5F9, "B"),
    ("batter_bases_ran", 0x900DB74D, "B"),
    ("pitches", 0x900D692C, "B"),
    ("batters_this_inning", 0x900D5E35, "B"),
    ("num_bases_ran", 0x900D66D2, "b"),
    ("fair_or_foul", 0x900D9516, ">h"),
    ("runs_this_pitch", 0x900D5E30, "B"),
    ("outs_this_pitch", 0x900D5E31, "B"),
    ("throw_target", 0x900D951A, ">h"),
    ("contact_fielder", 0x900D9522, ">h"),
    ("last_contact_fielder", 0x900D9524, ">h"),
    ("buddy_thrower", 0x900D66D0, ">h"),
    ("buddy_partner", 0x900D66CE, ">h"),
    ("swing_frames", 0x900D6A49, "B"),
    ("bunt_frames", 0x900D6A4F, "B"),
    ("laser_throw_flag", 0x900D9AF5, "B"),
]
PARK_KEYS = (
    "mario_stadium",
    "luigis_mansion",
    "peach_ice_garden",
    "daisy_cruiser",
    "wario_city",
    "yoshi_park",
    "dk_jungle",
    "bowser_jr_playroom",
    "bowser_castle",
)
STADIUM_BYTE_TO_PARK = {
    0: "mario_stadium",
    1: "bowser_castle",
    2: "wario_city",
    3: "yoshi_park",
    4: "peach_ice_garden",
    5: "dk_jungle",
    6: "luigis_mansion",
    7: "daisy_cruiser",
    8: "bowser_jr_playroom",
}
STADIUM_ADDRESS = 0x811F769D
DAY_NIGHT_ADDRESSES = (0x811F769E, 0x811F769F)
FRAME_MAGIC = b"MSSTRK02"
CAPTURE_READY_MARKER = "[capture-ready] "
CAPTURE_ATTACHED_MARKER = "[capture-attached] "


def capture_attached_payload(
    *, stem, park, stadium_byte, ball_offset, stream_path, actors, timer
):
    """Everything provable with the game clock stopped."""
    return {
        "status": "attached",
        "stem": str(stem),
        "park": park,
        "stadium_byte": stadium_byte,
        "ball_offset": ball_offset,
        "stream_path": str(stream_path),
        "fielders": len(actors.get("fielders", [])),
        "offense": len(actors.get("offense", [])),
        "game_timer": int(timer),
        "attached": True,
    }


def capture_ready_payload(
    *, frames, missed_frames, bytes_on_disk, elapsed_s, stem, park, timer, live=None
):
    """The evidence line, built where it can be tested without an emulator."""
    payload = {
        "status": "recording",
        "frames": int(frames),
        "missed_frames": int(missed_frames),
        "bytes_on_disk": int(bytes_on_disk),
        "elapsed_s": round(float(elapsed_s), 3),
        "stem": str(stem),
        "park": park,
        "game_timer": int(timer),
        "calibration_status": (
            live.calibration_status if live is not None else "disabled"
        ),
    }
    payload["ready"] = payload["frames"] > 0 and payload["bytes_on_disk"] > 0
    if not payload["ready"]:
        payload["reason"] = (
            "no frames sampled"
            if payload["frames"] <= 0
            else "no bytes written to the capture file"
        )
    return payload


def read_state_field(block: bytes, address: int, fmt: str):
    """Pull one scalar out of an already-captured state block."""
    off = address - STATE_BASE
    if fmt == "B":
        return block[off]
    if fmt == "b":
        value = block[off]
        return value - 256 if value > 127 else value
    return struct.unpack(fmt, block[off : off + struct.calcsize(fmt)])[0]


class ActorsNotReady(RuntimeError):
    """The actor objects are not in memory yet -- or not yet what they will be.

    The advanced worker is started the moment the tracker sees a match begin,
    which is the Match Rules screen handing over to the intro cutscene. The
    thirteen actor objects are allocated when the FIELD loads, several seconds
    later, so every check in resolve_actors fails at that point on a perfectly
    healthy game. Treating those failures as fatal means the worker is always
    dead before the first pitch.
    """


def resolve_actors(dme) -> dict:
    """Locate the thirteen actor structs and prove they are what we think.

    The fielder pointers are read from the game's own table, so they are right
    by construction. The offense base is a derived constant, so it is checked
    against the class vtable before a session is allowed to start -- a wrong
    base here would record 1872 bytes of unrelated memory and the failure would
    only surface hours later during analysis, which is exactly the silent kind
    of breakage that costs a whole session.

    Raises ActorsNotReady for anything that a field which has not finished
    loading also produces; wait_for_actors decides when to stop believing that.
    """
    fielders = []
    table = dme.read_bytes(FIELDER_POINTER_TABLE, 4 * 9)
    for i, name in enumerate(POSITION_NAMES):
        pointer = struct.unpack(">I", table[i * 4 : i * 4 + 4])[0]
        if pointer == 0:
            raise ActorsNotReady(
                f"The {name} fielder pointer is null; the actor objects do not exist outside of play."
            )
        vtable = struct.unpack(">I", dme.read_bytes(pointer, 4))[0]
        if vtable != FIELDER_VTABLE:
            raise ActorsNotReady(
                f"{name} at 0x{pointer:08X} has vtable 0x{vtable:08X}, not the fielder class 0x{FIELDER_VTABLE:08X}."
            )
        fielders.append({"name": name, "address": pointer, "stride": FIELDER_STRIDE})
    offense = []
    for i, name in enumerate(OFFENSE_NAMES):
        address = OFFENSE_BASE + OFFENSE_STRIDE * i
        vtable = struct.unpack(">I", dme.read_bytes(address, 4))[0]
        if vtable != OFFENSE_VTABLE:
            raise ActorsNotReady(
                f"Offense slot {name} at 0x{address:08X} has vtable 0x{vtable:08X}, not 0x{OFFENSE_VTABLE:08X}. Either the field has not loaded or the state block has moved."
            )
        offense.append({"name": name, "address": address, "stride": OFFENSE_STRIDE})
    for actor in fielders + offense:
        end = actor["address"] + actor["stride"]
        if not (STATE_BASE <= actor["address"] and end <= STATE_END):
            raise ActorsNotReady(
                f"{actor['name']} at 0x{actor['address']:08X} lies outside the captured region 0x{STATE_BASE:08X}..0x{STATE_END:08X}."
            )
    return {"fielders": fielders, "offense": offense}


def wait_for_actors(dme, timeout: float, stop_path=None) -> dict:
    """Retry resolve_actors until the field has finished loading.

    Every check it makes is also failed by a field that has not loaded yet, so
    a single attempt at the moment the match begins is a guaranteed failure on
    a healthy game. After the window expires the LAST failure is reported --
    that is the one that distinguishes a moved state block from a slow load.
    """
    deadline = time.time() + timeout
    warned = False
    while True:
        try:
            return resolve_actors(dme)
        except ActorsNotReady as error:
            last = error
        if stop_path is not None and stop_path.exists():
            raise SystemExit("Stopped before the field finished loading.")
        if time.time() >= deadline:
            raise SystemExit(
                f"The actor objects never appeared within {timeout:.0f}s. {last}"
            )
        if not warned:
            print("  waiting for the field to load...", flush=True)
            warned = True
        time.sleep(0.25)


def lock_ball_offset(dme, timeout: float, stop_path=None) -> int:
    """Wait for a pitch reset and take the ball's coordinate offset from it.

    The offset is chosen per stadium load, so the seed in probe_ball_memory is
    wrong more often than right and a stale one reads (0, 0, 0) forever without
    erroring. Blocking here is the cheap version of that failure.
    """
    deadline = time.time() + timeout
    warned = False
    while time.time() < deadline:
        pointer = struct.unpack(">I", dme.read_bytes(BALL_POINTER_SLOT, 4))[0]
        if pointer:
            offset = resolve_offset(dme, pointer)
            if offset is not None:
                x, y, z = struct.unpack(">fff", dme.read_bytes(pointer + offset, 12))
                if plausible_position(x, y, z):
                    return offset
        if stop_path is not None and stop_path.exists():
            raise SystemExit("Stopped before the first pitch.")
        if not warned:
            print("  waiting for a pitch reset to calibrate the ball offset...", flush=True)
            warned = True
        time.sleep(0.05)
    raise SystemExit(
        f"No pitch reset was observed within {timeout:.0f}s, so the ball "
        "coordinate field could not be verified. Advanced tracking stopped "
        "instead of exporting unverified measurements."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--park",
        default="auto",
        choices=("auto",) + PARK_KEYS,
        metavar="PARK",
        help="optional park fallback (default: detect from game memory): "
        + ", ".join(PARK_KEYS),
    )
    parser.add_argument(
        "--out", default="data/player_tracking", help="directory for the session files"
    )
    parser.add_argument("--note", default="", help="free text stored in the header")
    parser.add_argument(
        "--field-timeout",
        type=float,
        default=300.0,
        help="seconds to wait for the field to load before giving up",
    )
    parser.add_argument(
        "--ball-timeout",
        # The worker is started when the match begins, and the first pitch is
        # an intro cutscene, a lineup change and a walk-up away. Waiting costs
        # nothing -- the tracker stops this process explicitly at the end of
        # the game, and the stop file is checked inside the wait.
        type=float,
        default=1800.0,
        help="seconds to wait for a pitch reset before giving up",
    )
    parser.add_argument(
        "--max-seconds",
        type=float,
        default=0.0,
        help="stop automatically after this long (0 = until Ctrl-C)",
    )
    parser.add_argument(
        "--stop-file", default=None, help="stop cleanly when this path appears"
    )
    parser.add_argument(
        "--manifest",
        default=None,
        help="write sidecar status/stem JSON to this fixed path",
    )
    parser.add_argument(
        "--no-live-derive",
        action="store_true",
        help="record only; do not derive plays during capture",
    )
    parser.add_argument(
        "--position-offset",
        type=lambda v: int(v, 0),
        default=DEFAULT_POSITION_OFFSET,
        help="actor live-position offset for live derivation; it is confirmed against the session itself before any play is released",
    )
    parser.add_argument(
        "--live-out",
        default=None,
        help="path for live plays (default <stem>.live.jsonl)",
    )
    parser.add_argument(
        "--ready-frames",
        type=int,
        default=30,
        help="frames that must be captured and flushed before the [capture-ready] evidence line is printed",
    )
    parser.add_argument(
        "--memory-profile",
        choices=("R4QE01",),
        default="R4QE01",
        help="supported Mario Super Sluggers memory layout",
    )
    args = parser.parse_args()
    stop_path = Path(args.stop_file) if args.stop_file else None
    manifest_path = Path(args.manifest) if args.manifest else None

    def write_manifest(payload: dict) -> None:
        if not manifest_path:
            return
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, indent=2))
        temporary.replace(manifest_path)

    dme = hook()
    actors = wait_for_actors(dme, args.field_timeout, stop_path)
    print(
        "fielders  "
        + "  ".join((f"{a['name']}@0x{a['address']:08X}" for a in actors["fielders"]))
    )
    print(
        "offense   "
        + "  ".join((f"{a['name']}@0x{a['address']:08X}" for a in actors["offense"]))
    )
    ball_offset = lock_ball_offset(dme, args.ball_timeout, stop_path)
    print(f"ball coordinate offset 0x{ball_offset:03X}")
    stadium_byte = dme.read_bytes(STADIUM_ADDRESS, 1)[0]
    day_night_bytes = [dme.read_bytes(address, 1)[0] for address in DAY_NIGHT_ADDRESSES]
    is_night = bool(day_night_bytes[0]) if len(set(day_night_bytes)) == 1 else None
    if is_night is None:
        print(
            f"  WARNING: day/night bytes disagree: {day_night_bytes}. The capture will preserve both and leave is_night unknown.",
            flush=True,
        )
    named = STADIUM_BYTE_TO_PARK.get(stadium_byte)
    if named and args.park not in ("auto", named):
        print()
        print(
            f"  the game says this is {named}, not {args.park}. Recording it as {named}.",
            flush=True,
        )
    if named:
        args.park = named
    elif args.park == "auto":
        raise SystemExit(
            f"Stadium byte {stadium_byte} is outside the known 0..8 menu. No capture was started because labelling it with the wrong park would corrupt field geometry."
        )
    extra_regions = []
    capture_size = STATE_SIZE + sum((size for _, _, size in extra_regions))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = out_dir / f"{args.park}-{stamp}"
    header = {
        "format": FRAME_MAGIC.decode(),
        "memory_profile": args.memory_profile,
        "park": args.park,
        "note": args.note,
        "recorded_utc": stamp,
        "state_base": STATE_BASE,
        "state_size": STATE_SIZE,
        "extra_regions": [[n, b, sz] for n, b, sz in extra_regions],
        "capture_size": capture_size,
        "game_timer_address": GAME_TIMER,
        "ball_offset": ball_offset,
        "actor_fields": ACTOR_FIELDS,
        "state_fields": [[n, a, f] for n, a, f in STATE_FIELDS],
        "actors": actors,
        "stadium_byte": stadium_byte,
        "day_night_bytes": day_night_bytes,
        "is_night": is_night,
    }
    stem.with_suffix(".json").write_text(json.dumps(header, indent=2))
    write_manifest(
        {
            "status": "recording",
            "stem": str(stem.resolve()),
            "header_path": str(stem.with_suffix(".json").resolve()),
            "stream_path": str(stem.with_suffix(".bin").resolve()),
            "recorded_utc": stamp,
            "park": args.park,
            "stadium_byte": stadium_byte,
            "day_night_bytes": day_night_bytes,
            "is_night": is_night,
        }
    )
    has_winmm = hasattr(ctypes, "windll")
    if has_winmm:
        ctypes.windll.winmm.timeBeginPeriod(1)
    live = None
    live_failure = None
    if not args.no_live_derive:
        live_path = (
            Path(args.live_out) if args.live_out else stem.with_suffix(".live.jsonl")
        )
        pitches_path = stem.with_suffix(".pitches.jsonl")
        live = LiveDerivation(
            state_base=STATE_BASE,
            actors=[dict(a, kind="fielder") for a in actors["fielders"]]
            + [dict(a, kind="offense") for a in actors["offense"]],
            fields=ACTOR_FIELDS,
            state_fields=[[n, a, f] for n, a, f in STATE_FIELDS],
            out_path=live_path,
            pitches_path=pitches_path,
            position_offset=args.position_offset,
            state_size=STATE_SIZE,
            extra_regions=extra_regions,
        )
        header["live_plays_path"] = str(live_path)
        header["live_pitches_path"] = str(pitches_path)
        header["live_position_offset"] = args.position_offset
        stem.with_suffix(".json").write_text(json.dumps(header, indent=2))
        print(
            f"live derivation on, +0x{args.position_offset:03X} pending confirmation -> {live_path}",
            flush=True,
        )
    else:
        print("live derivation disabled (--no-live-derive)", flush=True)
    initial_status = (
        live.status()
        if live is not None
        else {"calibration_status": "disabled", "plays_emitted": 0, "plays_withheld": 0}
    )
    initial_status.update(
        {
            "status": "recording",
            "park": args.park,
            "stadium_byte": stadium_byte,
            "day_night_bytes": day_night_bytes,
            "is_night": is_night,
            "stem": str(stem.resolve()),
            "live_path": str(live_path.resolve()) if live is not None else None,
        }
    )
    print("[live-status] " + json.dumps(initial_status), flush=True)
    compressor = zlib.compressobj(6)
    frames = 0
    skips = 0
    wandered = False
    previous = bytes(capture_size)
    started = time.perf_counter()
    last_timer = None
    last_report = started
    print("\nrecording -- play normally. Ctrl-C to stop.\n")
    capture_ready_announced = False
    stream_path = stem.with_suffix(".bin")
    sink = stream_path.open("wb")
    try:
        sink.write(FRAME_MAGIC)
        sink.flush()
        try:
            attached_timer = struct.unpack(">I", dme.read_bytes(GAME_TIMER, 4))[0]
        except Exception:
            attached_timer = 0
        print(
            CAPTURE_ATTACHED_MARKER
            + json.dumps(
                capture_attached_payload(
                    stem=stem.resolve(),
                    park=args.park,
                    stadium_byte=stadium_byte,
                    ball_offset=ball_offset,
                    stream_path=stream_path.resolve(),
                    actors=actors,
                    timer=attached_timer,
                )
            ),
            flush=True,
        )
        stalled_since = None
        while True:
            timer = struct.unpack(">I", dme.read_bytes(GAME_TIMER, 4))[0]
            if timer == last_timer:
                now = time.perf_counter()
                if stalled_since is None:
                    stalled_since = now
                elif now - stalled_since >= 5.0:
                    print(
                        f"  game clock has not advanced for {now - stalled_since:.0f}s -- emulator paused?",
                        flush=True,
                    )
                    stalled_since = now
                if args.max_seconds and now - started >= args.max_seconds:
                    break
                if stop_path and stop_path.exists():
                    print(f"  stop signal received ({stop_path})", flush=True)
                    break
                time.sleep(0.001)
                continue
            stalled_since = None
            if last_timer is not None and timer - last_timer > 1:
                skips += timer - last_timer - 1
            last_timer = timer
            block = dme.read_bytes(STATE_BASE, STATE_SIZE)
            for _, base, size in extra_regions:
                block += dme.read_bytes(base, size)
            pointers = dme.read_bytes(FIELDER_POINTER_TABLE, 36)
            if not wandered:
                for i in range(9):
                    target = struct.unpack(">I", pointers[i * 4 : i * 4 + 4])[0]
                    if not (
                        STATE_BASE <= target and target + FIELDER_STRIDE <= STATE_END
                    ):
                        wandered = True
                        print(
                            f"  *** {POSITION_NAMES[i]} now points at 0x{target:08X}, outside the captured region. Its coordinates from here on are STALE. Stop, and widen STATE_BASE..STATE_END to cover it.",
                            flush=True,
                        )
                        break
            ball_pointer = struct.unpack(">I", dme.read_bytes(BALL_POINTER_SLOT, 4))[0]
            if ball_pointer:
                ball = dme.read_bytes(ball_pointer + ball_offset, 12)
            else:
                ball = struct.pack(">fff", 0.0, 0.0, 0.0)
            delta = bytes((a ^ b for a, b in zip(block, previous)))
            previous = block
            record = (
                struct.pack(">IdI", timer, time.perf_counter() - started, ball_pointer)
                + ball
                + pointers
                + delta
            )
            sink.write(compressor.compress(struct.pack(">I", len(record)) + record))
            frames += 1
            if not capture_ready_announced and frames >= max(1, args.ready_frames):
                capture_ready_announced = True
                try:
                    sink.write(compressor.flush(zlib.Z_SYNC_FLUSH))
                    sink.flush()
                    bytes_on_disk = stream_path.stat().st_size
                except OSError as error:
                    bytes_on_disk = 0
                    print(
                        f"  could not flush the capture for the readiness check: {error!r}",
                        flush=True,
                    )
                print(
                    CAPTURE_READY_MARKER
                    + json.dumps(
                        capture_ready_payload(
                            frames=frames,
                            missed_frames=skips,
                            bytes_on_disk=bytes_on_disk,
                            elapsed_s=time.perf_counter() - started,
                            stem=str(stem.resolve()),
                            park=args.park,
                            timer=timer,
                            live=live,
                        )
                    ),
                    flush=True,
                )
            if live is not None:
                try:
                    for play in live.feed(
                        timer,
                        struct.unpack(">fff", ball),
                        block,
                        struct.unpack(">9I", pointers),
                    ):
                        print("[live-play] " + dumps_play(play), flush=True)
                    for pitch in live.take_pitches():
                        print("[live-pitch] " + dumps_play(pitch), flush=True)
                except Exception as error:
                    print(
                        f"  live derivation stopped after an error: {error!r}",
                        flush=True,
                    )
                    live_failure = repr(error)
                    live = None
            now = time.perf_counter()
            if now - last_report >= 2.0:
                elapsed = now - started
                print(
                    f"  {elapsed:6.1f}s  {frames:7d} frames ({frames / elapsed:5.1f}/s, {skips} missed)  {sink.tell() / 1000000.0:6.2f} MB  inning {read_state_field(block, 2416795031, 'B')}.{read_state_field(block, 2416795173, 'B')} {read_state_field(block, 2416794281, 'B')} out  holder={read_state_field(block, 2416797385, 'b')}"
                    + (
                        f"  live {live.plays_emitted} plays ({live.calibration_status}, {live.status()['mean_feed_ms']:.3f} ms/frame)"
                        if live is not None
                        else ""
                    ),
                    flush=True,
                )
                if live is not None:
                    status = live.status()
                    status.update(
                        {
                            "status": "recording",
                            "park": args.park,
                            "stadium_byte": stadium_byte,
                            "day_night_bytes": day_night_bytes,
                            "is_night": is_night,
                            "stem": str(stem.resolve()),
                            "live_path": str(live_path.resolve()),
                        }
                    )
                    print("[live-status] " + json.dumps(status), flush=True)
                last_report = now
            if args.max_seconds and now - started >= args.max_seconds:
                break
            if stop_path and stop_path.exists():
                print(f"  stop signal received ({stop_path})", flush=True)
                break
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        if live is not None:
            try:
                for play in live.close():
                    print("[live-play] " + dumps_play(play), flush=True)
                for pitch in live.take_pitches():
                    print("[live-pitch] " + dumps_play(pitch), flush=True)
            except Exception as error:
                print(f"  live derivation close failed: {error!r}", flush=True)
                live_failure = repr(error)
        sink.write(compressor.flush())
        sink.close()
        if has_winmm:
            ctypes.windll.winmm.timeEndPeriod(1)
    elapsed = time.perf_counter() - started
    size = stem.with_suffix(".bin").stat().st_size
    header["frames"] = frames
    header["missed_frames"] = skips
    header["fielder_pointers_left_region"] = wandered
    header["duration_seconds"] = round(elapsed, 3)
    live_status = live.status() if live is not None else {
        "calibration_status": "failed" if live_failure else "disabled",
    }
    if live_failure:
        live_status["calibration_status"] = "failed"
        live_status["failure"] = live_failure
    header["live_derivation"] = live_status
    header["checksum_sha256"] = hashlib.sha256(
        stem.with_suffix(".bin").read_bytes()
    ).hexdigest()
    stem.with_suffix(".json").write_text(json.dumps(header, indent=2))
    write_manifest(
        {
            "status": "captured",
            "stem": str(stem.resolve()),
            "header_path": str(stem.with_suffix(".json").resolve()),
            "stream_path": str(stem.with_suffix(".bin").resolve()),
            "recorded_utc": stamp,
            "park": args.park,
            "stadium_byte": stadium_byte,
            "day_night_bytes": day_night_bytes,
            "is_night": is_night,
            "frames": frames,
            "missed_frames": skips,
            "duration_seconds": round(elapsed, 3),
            "checksum_sha256": header["checksum_sha256"],
            "fielder_pointers_left_region": wandered,
            "live_derivation": live_status,
        }
    )
    print(
        f"\n{frames} frames in {elapsed:.1f}s ({frames / max(elapsed, 1e-09):.1f}/s, {skips} missed) -> {stem.with_suffix('.bin')} ({size / 1000000.0:.2f} MB, {size / max(frames, 1):.0f} B/frame)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
