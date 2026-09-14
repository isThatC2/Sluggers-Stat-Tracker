"""Diagnose the tracker's ball-coordinate feed directly against Dolphin.

The feed depends on reading the ball's position out of a live object in the
emulated game's memory. When that read stops landing on the position field the
failure is silent and total -- coordinates read fine, never change, and every
downstream metric (exit velocity, launch angle, distance, pitch type) produces
nothing at all with no error in the log to say why. It looks exactly like a
broken batted-ball state machine, which is a very expensive thing to chase.

This probe answers the question directly, without the tracker in the way:

    pip install dolphin-memory-engine
    python scripts/probe_ball_memory.py            # locate and verify
    python scripts/probe_ball_memory.py --watch 25 # then swing at something

`--watch` samples the object while you play and reports which offsets actually
move. The real position field is the one that traces a trajectory; anything
else is a field that happens to sit nearby.

Known history: the offset was 0x558 when this feed was built and moved to
0x4B4, with the object pointer itself unchanged. That is why the tracker now
calibrates the offset at runtime from the pitch-reset signature rather than
hardcoding it (see patch_tracker_advanced_stats.py).
"""
from __future__ import annotations

import argparse
import struct
import time

BALL_POINTER_SLOT = 0x80795310
# Scan the WHOLE object, not a window around wherever the field was last time.
#
# This started as +0x300..+0x700, chosen to bracket the two offsets then known
# (0x558, then 0x4B4). On 2026-08-17 the field moved to 0x720 -- 0x20 bytes past
# the end of that window -- and the failure was exactly the silent one this
# probe exists to catch: the signature scan found nothing, the fallback read
# 0x4B4, and 0x4B4 now holds (-0.0002, -0.125, 0), which is not a position at
# all. Nothing tracked and nothing errored.
#
# Narrowing the search bought nothing. The signature is unique across the full
# object (verified: exactly one match in 0x1000 bytes), the read is a single
# 4 KB fetch, and the x/y range check rejects a stray constant. So there is no
# reason to guess where to look.
SCAN_START = 0x000
SCAN_SIZE = 0x1000
# Only used until the ball reaches a pitch reset. It is a LAST-KNOWN-GOOD value
# and goes stale exactly when this file matters most, so it is validated before
# being trusted -- see plausible_position().
FALLBACK_OFFSET = 0x720
WATCH_START = 0x400
WATCH_SIZE = 0x200

# Between pitches the ball rests on the mound at a byte-exact Z, which is the
# fingerprint the tracker uses to find the coordinate field. Only Z is matched:
# the resting Y alternates between 0 and 1 depending on whether the pitcher is
# holding the ball, so keying on it would miss half the time.
RESET_Z = -18.6000004
RESET_SIGNATURE = struct.pack(">f", RESET_Z)


def hook():
    try:
        import dolphin_memory_engine as dme
    except ImportError:
        raise SystemExit(
            "dolphin_memory_engine is not installed.\n"
            "  pip install dolphin-memory-engine"
        )
    dme.hook()
    if not dme.is_hooked():
        # "Is it running with a game loaded?" was the whole message, and it is
        # wrong as often as it is right: the library takes no PID and attaches
        # to whichever Dolphin it finds first, so a second instance sitting on
        # the game list makes every probe fail while a game IS loaded in the
        # other one. `get_status` tells the two apart, so say which it is.
        try:
            status = str(dme.get_status()).rsplit(".", 1)[-1]
        except Exception:
            status = "unknown"
        if status == "noEmu":
            raise SystemExit("\n".join([
                "Found Dolphin, but it is not emulating anything.",
                "  dolphin_memory_engine attaches to the FIRST Dolphin process it",
                "  finds and cannot be pointed at a particular one. If you have a",
                "  second Dolphin open on the game list, close it and retry --",
                "  that empty instance is the one it grabbed.",
            ]))
        raise SystemExit(
            f"Could not hook Dolphin (status: {status}). "
            "Is it running with a game loaded?"
        )
    return dme


def plausible_position(x, y, z):
    """Could these three floats be a ball position in this game's units?

    Deliberately loose -- it is here to catch a read landing on padding or an
    unrelated struct, not to validate a coordinate. The fallback offset reading
    (-0.0002, -0.125, 0) is what this is for: y below the ground and z exactly
    zero is not somewhere a ball has ever been.
    """
    for v in (x, y, z):
        if v != v or abs(v) == float("inf"):
            return False
    if x == 0.0 and y == 0.0 and z == 0.0:
        return False
    return -70 < x < 70 and 0 <= y < 80 and -140 < z < 140


def resolve_offset(dme, base):
    """Find the coordinate offset the same way the patched tracker does."""
    window = dme.read_bytes(base + SCAN_START, SCAN_SIZE)
    match_index = window.find(RESET_SIGNATURE)
    while match_index >= 0:
        candidate = SCAN_START + match_index - 8
        if match_index >= 8 and candidate % 4 == 0:
            x, y = struct.unpack(">ff", window[match_index - 8 : match_index])
            if -60 < x < 60 and -5 < y < 60:
                return candidate
        match_index = window.find(RESET_SIGNATURE, match_index + 1)
    return None


def watch(dme, base, offset, seconds):
    print(f"\nWatching +0x{WATCH_START:03X}..+0x{WATCH_START + WATCH_SIZE:03X} for {seconds:.0f}s.")
    print("Put a ball in play now.\n")
    samples = []
    end = time.time() + seconds
    while time.time() < end:
        try:
            samples.append(dme.read_bytes(base + WATCH_START, WATCH_SIZE))
        except Exception:
            break
        time.sleep(1 / 120)

    print(f"collected {len(samples)} samples")
    moved = {}
    for index in range(WATCH_SIZE // 4):
        values = [struct.unpack(">f", s[index * 4 : index * 4 + 4])[0] for s in samples]
        finite = [v for v in values if -1e6 < v < 1e6]
        if len(finite) < len(values) // 2:
            continue
        spread = max(finite) - min(finite)
        if spread > 0.01:
            moved[WATCH_START + index * 4] = spread

    if not moved:
        print("Nothing moved. The ball never left rest, or the feed is dead.")
        return
    print("\noffsets that moved:")
    for field_offset in sorted(moved):
        marker = "  <-- tracker is using this" if field_offset == offset else ""
        print(f"  +0x{field_offset:03X}  spread={moved[field_offset]:10.3f}{marker}")
    print("\nposition triples (three consecutive moving floats):")
    triples = [o for o in sorted(moved) if o + 4 in moved and o + 8 in moved]
    for field_offset in triples:
        print(f"  +0x{field_offset:03X} .. +0x{field_offset + 8:03X}")
    if not triples:
        print("  none -- no field in this window behaves like a position")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--watch",
        type=float,
        metavar="SECONDS",
        help="sample the object while you play and report which offsets move",
    )
    args = parser.parse_args()

    dme = hook()
    base = int.from_bytes(dme.read_bytes(BALL_POINTER_SLOT, 4), "big")
    print(f"pointer slot   0x{BALL_POINTER_SLOT:08X} -> 0x{base:08X}")
    if base == 0:
        print("Pointer is null; no ball object right now.")
        return 1

    offset = resolve_offset(dme, base)
    calibrated = offset is not None
    if not calibrated:
        offset = FALLBACK_OFFSET
        print(
            f"coordinate offset  NOT FOUND -- ball is not at the pitch-reset position.\n"
            f"                   falling back to 0x{offset:03X}. Re-run between pitches."
        )
    else:
        print(f"coordinate offset  0x{offset:03X}  (from the pitch-reset signature)")
        if offset != FALLBACK_OFFSET:
            # The field can move between stadium loads. The fallback is only a
            # startup seed; calibration is authoritative and there is no one
            # permanent value to copy back into the source.
            print(
                f"                   NOTE: this differs from FALLBACK_OFFSET "
                f"(0x{FALLBACK_OFFSET:03X}).\n"
                f"                   That is expected across stadium loads. Do not update\n"
                f"                   the seed; the tracker resolves this signature too."
            )
    print(f"coordinate address 0x{base + offset:08X}")

    x, y, z = struct.unpack(">fff", dme.read_bytes(base + offset, 12))
    print(f"current position   ({x:.6g}, {y:.6g}, {z:.6g})")
    if not plausible_position(x, y, z):
        print(
            "\n  *** THAT IS NOT A POSITION. ***\n"
            "  The offset in use does not point at the coordinate field, so the\n"
            "  feed is dead: coordinates will read fine, never change, and every\n"
            "  downstream metric will produce nothing with no error anywhere.\n"
            "  Get to a pitch reset (between pitches) and re-run to recalibrate."
        )

    if args.watch:
        watch(dme, base, offset, args.watch)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
