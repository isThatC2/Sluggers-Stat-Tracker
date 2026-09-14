"""Run the hidden capture worker without putting the original tracker at risk."""

from __future__ import annotations

import atexit
import json
import shutil
import subprocess
import sys
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .session import AdvancedSession


class AdvancedTrackingError(RuntimeError):
    pass


def _read_json_lines(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise AdvancedTrackingError(f"invalid {path.name} line {line_number}: {error}") from error
        if not isinstance(record, dict):
            raise AdvancedTrackingError(f"invalid {path.name} line {line_number}: expected an object")
        records.append(record)
    return records


class AdvancedSupervisor:
    """One match's capture process and final Excel export."""

    def __init__(self, output_dir: Path | str, *, session_id: str | None = None) -> None:
        self.output_dir = Path(output_dir).resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.session_id = session_id or uuid.uuid4().hex
        self.runtime_dir = self.output_dir / ".advanced-runtime" / self.session_id
        self.capture_dir = self.runtime_dir / "capture"
        self.manifest_path = self.runtime_dir / "manifest.json"
        self.stop_path = self.runtime_dir / "stop"
        self.log_path = self.runtime_dir / "worker.log"
        self.process: subprocess.Popen | None = None
        self._log_sink = None

    def start(self) -> None:
        if self.process is not None:
            raise AdvancedTrackingError("advanced worker already started")
        self.runtime_dir.mkdir(parents=True, exist_ok=False)
        self.capture_dir.mkdir()
        if getattr(sys, "frozen", False):
            command = [sys.executable, "--advanced-worker"]
        else:
            command = [sys.executable, "-m", "advanced_tracking.engine.collector"]
        command += [
            "--out", str(self.capture_dir),
            "--manifest", str(self.manifest_path),
            "--stop-file", str(self.stop_path),
            "--memory-profile", "R4QE01",
        ]
        self._log_sink = self.log_path.open("w", encoding="utf-8")
        creationflags = 0
        if sys.platform == "win32":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self.process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=self._log_sink,
                stderr=subprocess.STDOUT,
                cwd=Path.cwd(),
                creationflags=creationflags,
            )
            atexit.register(self._shutdown_at_exit)
        except Exception:
            self._log_sink.close()
            self._log_sink = None
            self._cleanup_runtime()
            raise

    def stop(self, timeout: float = 30.0) -> int | None:
        if self.process is None:
            return None
        process = self.process
        if process.poll() is None:
            self.stop_path.touch()
        try:
            return process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as error:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            raise AdvancedTrackingError("advanced worker did not stop cleanly") from error
        finally:
            self.process = None
            if self._log_sink is not None:
                self._log_sink.close()
                self._log_sink = None

    def discard(self, timeout: float = 30.0) -> None:
        """Stop the worker and delete its capture without writing a workbook.

        A match that is abandoned before the stat screen produces no original
        workbook either, so there is nothing for an advanced workbook to sit
        beside. Left alone the capture is a 50-70 MB orphan in the user's
        output folder, and on a rematch the worker itself would go on reading
        Dolphin beside its replacement.
        """

        try:
            self.stop(timeout=timeout)
        except AdvancedTrackingError:
            pass
        self._cleanup_runtime()

    def _shutdown_at_exit(self) -> None:
        """Do not leave a hidden capture worker behind when the tracker exits."""

        try:
            self.stop(timeout=5.0)
        except Exception:
            pass

    def _manifest(self) -> dict[str, Any]:
        if not self.manifest_path.exists():
            raise AdvancedTrackingError("advanced worker produced no capture manifest")
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict) or not manifest.get("stem"):
            raise AdvancedTrackingError("advanced capture manifest is incomplete")
        return manifest

    def export(self, original_workbook: Path | str, game: Mapping[str, Any]) -> Path:
        manifest = self._manifest()
        stem = Path(manifest["stem"])
        header_path = stem.with_suffix(".json")
        header = json.loads(header_path.read_text(encoding="utf-8"))
        plays_path = Path(header.get("live_plays_path") or stem.with_suffix(".live.jsonl"))
        pitches_path = Path(header.get("live_pitches_path") or stem.with_suffix(".pitches.jsonl"))

        session = AdvancedSession(self.output_dir, session_id=self.session_id)
        pitches = _read_json_lines(pitches_path)
        pitchers_by_swing = {
            pitch.get("swing_timer"): pitch.get("pitcher")
            for pitch in pitches if pitch.get("swing_timer") is not None
        }
        for play in _read_json_lines(plays_path):
            session.record_play(
                play,
                batted_ball=play.get("batted_ball"),
                pitcher=pitchers_by_swing.get(play.get("swing_timer")),
            )
        for pitch in pitches:
            session.record_pitch(pitch)

        frames = manifest.get("frames") or 0
        missed = manifest.get("missed_frames") or 0
        total = frames + missed
        live = manifest.get("live_derivation") or {}
        calibration_status = live.get("calibration_status")
        capture_complete = (
            manifest.get("status") == "captured"
            and calibration_status == "confirmed"
            and not live.get("failure")
        )
        session.record_health({
            "capture_status": manifest.get("status"),
            "calibration_status": calibration_status,
            "frames_captured": frames,
            "frames_missed": missed,
            "missed_frame_rate": round(missed / total, 6) if total else None,
            "position_offset": live.get("position_offset"),
            "fielder_pointers_valid": not manifest.get("fielder_pointers_left_region", False),
            "advanced_export_status": "complete" if capture_complete else "partial",
            "notes": live.get("failure"),
        })
        game_summary = dict(game)
        game_summary.setdefault("started_utc", manifest.get("recorded_utc"))
        game_summary.setdefault("ended_utc", datetime.now(UTC).isoformat())
        destination = session.finalize(original_workbook, game_summary)
        self._cleanup_runtime()
        return destination

    def _cleanup_runtime(self) -> None:
        """Remove raw capture artifacts after their workbook is safely written."""

        runtime_root = (self.output_dir / ".advanced-runtime").resolve()
        runtime_dir = self.runtime_dir.resolve()
        if runtime_dir.parent != runtime_root or runtime_dir.name != self.session_id:
            raise AdvancedTrackingError("refusing to clean an unexpected runtime directory")
        if runtime_dir.exists():
            shutil.rmtree(runtime_dir)
        try:
            runtime_root.rmdir()
        except OSError:
            pass

    def stop_and_export(
        self,
        original_workbook: Path | str,
        game: Mapping[str, Any],
        *,
        timeout: float = 30.0,
    ) -> Path:
        exit_code = self.stop(timeout=timeout)
        if exit_code not in (0, None):
            raise AdvancedTrackingError(
                f"advanced worker exited with code {exit_code}: {self._last_worker_message()}")
        return self.export(original_workbook, game)

    def _last_worker_message(self) -> str:
        """The worker's own last words, so a failure is readable in the log.

        Everything the worker prints goes to a file the user never opens. A
        bare exit code sends them looking for a bug in the tracker when the
        worker has usually said exactly what happened.
        """
        try:
            lines = [line.strip() for line in
                     self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()
                     if line.strip()]
        except OSError:
            return "no worker log was written"
        return lines[-1] if lines else "the worker log is empty"
