"""Crash-tolerant session storage for the Excel-only advanced tracker."""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .normalize import normalize_play, play_id, sanitize_play
from .summary import batting_summary, defense_summary, pitching_summary
from .workbook import AdvancedWorkbookData, write_advanced_workbook


def advanced_workbook_path(original_workbook: Path | str) -> Path:
    original = Path(original_workbook)
    return original.with_name(f"{original.stem} - Advanced.xlsx")


class AdvancedSession:
    """Collect normalized rows and remove the journal after a successful export."""

    def __init__(
        self,
        output_dir: Path | str,
        *,
        session_id: str | None = None,
        recover: bool = False,
    ) -> None:
        self.output_dir = Path(output_dir).resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.session_id = session_id or uuid.uuid4().hex
        self.journal_dir = self.output_dir / ".advanced-sessions"
        self.journal_dir.mkdir(parents=True, exist_ok=True)
        self.journal_path = self.journal_dir / f"{self.session_id}.jsonl"
        self._plays: dict[str, dict[str, Any]] = {}
        self._pitches: dict[str, dict[str, Any]] = {}
        self._capture_health: list[dict[str, Any]] = []
        if recover and self.journal_path.exists():
            self._load_journal()

    def _append(self, record: Mapping[str, Any]) -> None:
        encoded = json.dumps(record, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        with self.journal_path.open("a", encoding="utf-8", newline="\n") as sink:
            sink.write(encoded + "\n")
            sink.flush()
            os.fsync(sink.fileno())

    def _apply(self, record: Mapping[str, Any]) -> None:
        kind = record.get("type")
        if kind == "play":
            play = record.get("play")
            if not isinstance(play, Mapping):
                raise ValueError("journal play record is missing play data")
            identifier = play_id(self.session_id, play.get("contact_timer"))
            self._plays[identifier] = {
                "play": dict(play),
                "batted_ball": dict(record.get("batted_ball") or {}),
                "pitcher": record.get("pitcher"),
            }
        elif kind == "pitch":
            pitch = record.get("pitch")
            if not isinstance(pitch, Mapping):
                raise ValueError("journal pitch record is missing pitch data")
            identifier = str(pitch.get("pitch_id") or pitch.get("pitch_timer"))
            if identifier in ("None", ""):
                raise ValueError("pitch record requires pitch_id or pitch_timer")
            self._pitches[identifier] = dict(pitch)
        elif kind == "health":
            health = record.get("health")
            if isinstance(health, Mapping):
                self._capture_health.append(dict(health))
        else:
            raise ValueError(f"unknown advanced journal record type: {kind!r}")

    def _load_journal(self) -> None:
        for line_number, line in enumerate(
            self.journal_path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            try:
                self._apply(json.loads(line))
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                raise ValueError(
                    f"invalid advanced journal {self.journal_path} line {line_number}: {error}"
                ) from error

    def record_play(
        self,
        play: Mapping[str, Any],
        *,
        batted_ball: Mapping[str, Any] | None = None,
        pitcher: str | None = None,
    ) -> bool:
        identifier = play_id(self.session_id, play.get("contact_timer"))
        if identifier in self._plays:
            return False
        record = {
            "type": "play",
            "play": sanitize_play(play),
            "batted_ball": dict(batted_ball or {}),
            "pitcher": pitcher,
        }
        self._append(record)
        self._apply(record)
        return True

    def record_pitch(self, pitch: Mapping[str, Any]) -> bool:
        identifier = str(pitch.get("pitch_id") or pitch.get("pitch_timer"))
        if identifier in ("None", ""):
            raise ValueError("pitch record requires pitch_id or pitch_timer")
        if identifier in self._pitches:
            return False
        record = {"type": "pitch", "pitch": dict(pitch)}
        self._append(record)
        self._apply(record)
        return True

    def record_health(self, health: Mapping[str, Any]) -> None:
        record = {"type": "health", "health": dict(health)}
        self._append(record)
        self._apply(record)

    def _pitch_rows(self) -> list[dict[str, Any]]:
        rows = []
        for pitch in self._pitches.values():
            has_trajectory_metric = any(
                pitch.get(key) is not None
                for key in ("velocity_mph", "horizontal_break_units", "vertical_break_units")
            )
            rows.append({
                "pitch": None,
                "pitch_timer": pitch.get("pitch_timer"),
                "inning": pitch.get("inning"),
                "inning_half": pitch.get("inning_half"),
                "outs": pitch.get("outs"),
                "balls_before": pitch.get("balls_before"),
                "strikes_before": pitch.get("strikes_before"),
                "batter": pitch.get("batter"),
                "pitcher": pitch.get("pitcher"),
                "pitch_in_pa": pitch.get("pitch_in_pa"),
                "velocity_mph": pitch.get("velocity_mph"),
                "horizontal_break_units": pitch.get("horizontal_break_units"),
                "vertical_break_units": pitch.get("vertical_break_units"),
                "offer": pitch.get("offer"),
                "outcome": pitch.get("outcome"),
                "measurement_status": pitch.get("measurement_status")
                    or ("measured" if has_trajectory_metric else "event_only"),
                "invalid_reason": pitch.get("invalid_reason")
                    or (None if has_trajectory_metric else "trajectory_metrics_unavailable"),
            })
        # Numbered in the order they were thrown, for the same reason plays are.
        rows.sort(key=lambda row: (row["pitch_timer"] is None, row["pitch_timer"]))
        for number, row in enumerate(rows, start=1):
            row["pitch"] = number
        return rows

    def finalize(
        self,
        original_workbook: Path | str,
        game: Mapping[str, Any],
    ) -> Path:
        rows = {"batted_balls": [], "fielding": [], "baserunning": [], "throws": []}
        # Plays are numbered in the order they happened, so `play` reads as the
        # first, second, third ball put in play rather than as a game timer.
        ordered = sorted(
            self._plays.values(),
            key=lambda record: (record["play"].get("contact_timer") is None,
                                record["play"].get("contact_timer")),
        )
        for number, record in enumerate(ordered, start=1):
            normalized = normalize_play(
                number,
                record["play"],
                batted_ball=record["batted_ball"],
                pitcher=record["pitcher"],
            )
            for key in rows:
                rows[key].extend(normalized[key])

        game_row = dict(game)
        game_row["session_id"] = self.session_id
        health_rows = [dict(item, session_id=self.session_id) for item in self._capture_health]
        destination = advanced_workbook_path(original_workbook)
        pitch_rows = self._pitch_rows()
        write_advanced_workbook(
            destination,
            AdvancedWorkbookData(
                game=game_row,
                batted_balls=rows["batted_balls"],
                fielding=rows["fielding"],
                baserunning=rows["baserunning"],
                throws=rows["throws"],
                pitches=pitch_rows,
                capture_health=health_rows,
                batting=batting_summary(rows["batted_balls"]),
                pitching=pitching_summary(pitch_rows),
                defense=defense_summary(rows["fielding"], rows["throws"],
                                        rows["baserunning"]),
            ),
        )
        self.journal_path.unlink(missing_ok=True)
        try:
            self.journal_dir.rmdir()
        except OSError:
            pass
        return destination
