"""Persist experiment measurements and user-supplied notes inside the project."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import threading
from typing import Literal
import uuid

from pydantic import BaseModel, ConfigDict, Field
from .audio import analyze_file, compare_files

REPORTS = Path(__file__).resolve().parents[1] / "mixing_reports"
LOCK = threading.Lock()
Decision = Literal["pending", "kept", "reverted", "inconclusive"]


class MixingChange(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    target: str = Field(min_length=1, max_length=500)
    original_value: float | str | bool
    proposed_value: float | str | bool
    applied_value: float | str | bool | None = None
    unit: str = Field(default="native", max_length=100)
    rationale: str = Field(default="", max_length=4000)
    status: Literal["proposed", "applied", "reverted"] = "proposed"


def _now():
    return datetime.now(timezone(timedelta(hours=8))).isoformat()


def _report_folder(report_id):
    if not isinstance(report_id, str) or not re.fullmatch(r"[0-9a-f]{32}", report_id):
        raise ValueError("Invalid report ID")
    return REPORTS / report_id


def _text(value):
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _number(value):
    return "unavailable" if value is None else f"{value:.3f}"


def render_report(report):
    lines = ["# " + _text(report["title"]), "", "Report ID: `" + report["report_id"] + "`",
             "Created: " + report["created_at"] + " (Asia/Singapore)",
             "Updated: " + report["updated_at"] + " (Asia/Singapore)",
             "Decision recorded by user/agent: " + report["decision"], "",
             "## Measurements", ""]
    comparison = report["comparison"]
    original = comparison["original"] if comparison else report["baseline"]
    revised = comparison["revised"] if comparison else None
    lines += ["Original file: " + _text(original["path"]),
              "Original SHA-256: `" + original["file_identity"]["sha256"] + "`"]
    if revised:
        lines += ["Revised file: " + _text(revised["path"]),
                  "Revised SHA-256: `" + revised["file_identity"]["sha256"] + "`",
                  "", "Export metadata matches: " + str(comparison["export_metadata_match"])]
        for mismatch in comparison["mismatches"]:
            lines.append("- " + mismatch["field"] + ": " + _text(mismatch["original"]) + " → " + _text(mismatch["revised"]))
    lines += ["", "| Measurement | Original | Revised | Delta |", "| --- | ---: | ---: | ---: |"]
    for key, label in [("integrated_loudness_lufs", "Integrated loudness (LUFS)"),
                       ("sample_peak_dbfs", "Sample peak (dBFS)"), ("rms_dbfs", "RMS (dBFS)")]:
        lines.append("| " + label + " | " + _number(original[key]) + " | " +
            _number(revised[key] if revised else None) + " | " +
            _number(comparison["deltas"][key] if comparison else None) + " |")
    if original["stereo"] is not None:
        for key, label in [("correlation", "Stereo correlation"),
                           ("left_minus_right_rms_db", "Channel balance (dB; positive = left)"),
                           ("mid_rms_dbfs", "Mid RMS (dBFS)"), ("side_rms_dbfs", "Side RMS (dBFS)")]:
            new_value = revised["stereo"][key] if revised and revised["stereo"] else None
            delta = comparison["deltas"]["stereo"][key] if comparison and comparison["deltas"]["stereo"] else None
            lines.append("| " + label + " | " + _number(original["stereo"][key]) + " | " +
                         _number(new_value) + " | " + _number(delta) + " |")
    lines += ["", "## Frequency bands", "",
              "| Band | Original energy % | Revised energy % | Gain-adjusted change (dB) |",
              "| --- | ---: | ---: | ---: |"]
    old_spectrum = original["frequency_analysis"]
    if old_spectrum["available"]:
        new_bands = revised["frequency_analysis"].get("bands", []) if revised else []
        changes = comparison["frequency_band_deltas"] if comparison else []
        for i, band in enumerate(old_spectrum["bands"]):
            band_label = band["name"] + " (" + str(band["low_hz"]) + "–" + str(band["high_hz"]) + " Hz)"
            lines.append("| " + band_label + " | " + _number(band["energy_percent"]) + " | " +
                _number(new_bands[i]["energy_percent"] if i < len(new_bands) else None) + " | " +
                _number(changes[i]["gain_adjusted_delta_db"] if i < len(changes) else None) + " |")
    else:
        lines += ["", "Frequency analysis unavailable: " + old_spectrum["reason"]]
    lines += ["", "## Proposed and recorded changes", "",
              "These entries are supplied by the caller; they are not an automatic audit of Live.", "",
              "| Target | Original | Proposed | Applied | Unit | Status | Rationale |",
              "| --- | --- | --- | --- | --- | --- | --- |"]
    for change in report["changes"]:
        lines.append("| " + " | ".join(_text(change[key]) for key in
            ("target", "original_value", "proposed_value", "applied_value", "unit", "status", "rationale")) + " |")
    lines += ["", "## Snapshot references", "",
              "Mixer: " + str(report["mixer_snapshot_id"]), "Device: " + str(report["device_snapshot_id"]),
              "Snapshot IDs are references only and expire on Live/bridge restart. This report does not persist snapshot state.",
              "", "## Listening notes", "", report["listening_notes"] or "No listening notes recorded.",
              "", "## Measurement limits", "",
              "Sample peaks are not true peaks. Matching export metadata does not prove time alignment.",
              "Frequency shares and centroids describe the export; they do not establish mix quality, masking or required EQ."]
    for notice in (comparison["notices"] if comparison else original["notices"]):
        lines.append("- " + notice)
    return "\n".join(lines) + "\n"


def _write(report, folder):
    # JSON is the source of truth. Markdown is a generated preview updated afterwards.
    for filename, content in (("report.json", json.dumps(report, indent=2, allow_nan=False)),
                              ("report.md", render_report(report))):
        temporary = folder / (uuid.uuid4().hex + ".tmp")
        try:
            temporary.write_text(content, encoding="utf-8")
            temporary.replace(folder / filename)
        finally:
            temporary.unlink(missing_ok=True)


def save_experiment(title, original_path, revised_path=None, changes=None,
                    mixer_snapshot_id=None, device_snapshot_id=None,
                    listening_notes="", decision="pending"):
    if not isinstance(title, str) or not title.strip() or len(title) > 300:
        raise ValueError("Title must contain 1..300 characters")
    _validate_notes(listening_notes, decision)
    for snapshot_id in (mixer_snapshot_id, device_snapshot_id):
        if snapshot_id is not None and not re.fullmatch(r"[0-9a-f]{32}", snapshot_id):
            raise ValueError("Snapshot IDs must be 32-character hex strings")
    if changes is not None and len(changes) > 100:
        raise ValueError("At most 100 recorded changes per report")
    records = [MixingChange.model_validate(c).model_dump() for c in (changes or [])]
    comparison = compare_files(original_path, revised_path) if revised_path else None
    baseline = None if comparison else analyze_file(original_path)
    report_id = uuid.uuid4().hex
    now = _now()
    report = dict(schema_version=1, report_id=report_id, title=title.strip(),
        created_at=now, updated_at=now, decision=decision, changes=records,
        mixer_snapshot_id=mixer_snapshot_id, device_snapshot_id=device_snapshot_id,
        listening_notes=listening_notes, baseline=baseline, comparison=comparison)
    folder = _report_folder(report_id)
    folder.mkdir(parents=True)
    _write(report, folder)
    return dict(report_id=report_id, json_path=str(folder / "report.json"),
                markdown_path=str(folder / "report.md"), decision=decision)


def _validate_notes(notes, decision):
    if not isinstance(notes, str) or len(notes) > 20000:
        raise ValueError("Listening notes must be at most 20000 characters")
    if decision not in ("pending", "kept", "reverted", "inconclusive"):
        raise ValueError("Unknown decision")


def read_experiment(report_id):
    folder = _report_folder(report_id)
    path = folder / "report.json"
    if not path.is_file():
        raise ValueError("Unknown experiment report")
    return json.loads(path.read_text(encoding="utf-8"))


def update_experiment(report_id, listening_notes, decision):
    _validate_notes(listening_notes, decision)
    with LOCK:
        report = read_experiment(report_id)
        report.update(listening_notes=listening_notes, decision=decision, updated_at=_now())
        _write(report, _report_folder(report_id))
    return dict(report_id=report_id, decision=decision,
                markdown_path=str(_report_folder(report_id) / "report.md"))


def list_experiments():
    if not REPORTS.is_dir():
        return dict(reports=[], truncated=False)
    summaries = []
    paths = sorted(REPORTS.glob("*/report.json"), key=lambda p: p.stat().st_mtime_ns, reverse=True)
    for path in paths[:100]:
        if not re.fullmatch(r"[0-9a-f]{32}", path.parent.name):
            continue
        report = read_experiment(path.parent.name)
        summaries.append(dict(report_id=report["report_id"], title=report["title"],
                              updated_at=report["updated_at"], decision=report["decision"]))
    return dict(reports=summaries, truncated=len(paths) > 100)
