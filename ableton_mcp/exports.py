"""Export job preparation and validation; Live rendering is performed through its UI."""
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import re
import threading
import time
import uuid

import soundfile as sf
from pydantic import BaseModel, ConfigDict
from .audio import DEFAULT_EXPORTS, analyze_file, MAX_DURATION, MAX_DECODED_BYTES

JOBS = Path(__file__).resolve().parents[1] / "export_jobs"
LOCK = threading.Lock()
SAMPLE_RATES = (44100, 48000, 88200, 96000)


class ObservedExportSettings(BaseModel):
    """Agent-recorded observations from the export dialog, not file-derived facts."""
    model_config = ConfigDict(extra="forbid", strict=True)
    rendered_track: str
    render_start: str
    render_length: str
    sample_rate_hz: int
    bit_depth: int
    normalize: bool
    convert_to_mono: bool
    render_as_loop: bool
    pcm_enabled: bool
    file_type: str
    mp3_enabled: bool
    dither: str


def _now():
    return datetime.now(timezone(timedelta(hours=8))).isoformat()


def _job_path(job_id):
    if not isinstance(job_id, str) or not re.fullmatch(r"[0-9a-f]{32}", job_id):
        raise ValueError("Invalid export job ID")
    return JOBS / (job_id + ".json")


def _save(job):
    JOBS.mkdir(parents=True, exist_ok=True)
    path = _job_path(job["job_id"])
    temporary = JOBS / (uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(job, indent=2, allow_nan=False), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _read(job_id):
    path = _job_path(job_id)
    if not path.is_file():
        raise ValueError("Unknown export job")
    return json.loads(path.read_text(encoding="utf-8"))


def _range(value, duration=False):
    # Live's three-part bar.beat.sixteenth representation; v1 supports fixed 4/4 only.
    if not isinstance(value, str) or not re.fullmatch(r"\d{1,6}\.\d\.\d", value):
        raise ValueError("Use bar.beat.sixteenth notation, for example 1.1.1 or duration 8.0.0")
    bar, beat, sixteenth = map(int, value.split("."))
    if duration:
        if not 0 <= beat < 4 or not 0 <= sixteenth < 4 or bar * 4 + beat + sixteenth / 4 <= 0:
            raise ValueError("Duration must be positive; beat and sixteenth duration fields are 0..3")
    elif bar < 1 or not 1 <= beat <= 4 or not 1 <= sixteenth <= 4:
        raise ValueError("Start position uses bars >=1, beats and sixteenths 1..4")
    return f"{bar}.{beat}.{sixteenth}"


def prepare_export(render_start, render_length, sample_rate_hz=48000,
                   expected_duration_seconds=None, label="Master mix"):
    start, length = _range(render_start), _range(render_length, duration=True)
    if type(sample_rate_hz) is not int or sample_rate_hz not in SAMPLE_RATES:
        raise ValueError("Supported rates: 44100, 48000, 88200 or 96000 Hz")
    if expected_duration_seconds is not None:
        if (isinstance(expected_duration_seconds, bool) or not isinstance(expected_duration_seconds, (int, float))
                or not math.isfinite(expected_duration_seconds) or not 0 < expected_duration_seconds <= MAX_DURATION):
            raise ValueError("Expected duration must be finite and within 0..600 seconds")
        if expected_duration_seconds * sample_rate_hz * 2 * 4 > MAX_DECODED_BYTES:
            raise ValueError("Requested duration/rate exceeds the audio analyzer's decoded-size limit; use a shorter range")
    if not isinstance(label, str) or not label.strip() or len(label) > 300:
        raise ValueError("Label must contain 1..300 characters")
    job_id = uuid.uuid4().hex
    directory = DEFAULT_EXPORTS / job_id
    directory.mkdir(parents=True)
    path = directory / "master.wav"
    settings = dict(rendered_track="Master", render_start=start, render_length=length,
        sample_rate_hz=sample_rate_hz, bit_depth=24, normalize=False,
        convert_to_mono=False, render_as_loop=False, pcm_enabled=True, file_type="WAV",
        mp3_enabled=False, dither="None")
    job = dict(schema_version=1, job_id=job_id, label=label.strip(), created_at=_now(),
        status="awaiting_ui_export", output_path=str(path), requested_settings=settings,
        expected_duration_seconds=expected_duration_seconds, verified_analysis=None)
    _save(job)
    return dict(**job, workflow_guide=str(Path(__file__).resolve().parents[1] / "docs" / "agent-export-workflow.md"),
        next_action="Use the computer-use skill to observe Live and operate Export Audio/Video; this tool does not start rendering",
        limitations=["Version 1 requires a fixed 4/4 arrangement and matching musical range",
                     "Computer use must be operational; no blind keyboard/click macro is used",
                     "Use a fresh path; never accept an overwrite prompt",
                     "Arrange-view playback must be the intended content; check Session overrides and armed recording",
                     "External hardware or tempo/meter automation requires a separately verified workflow"])


def get_export(job_id):
    job = _read(job_id)
    path = Path(job["output_path"])
    current = path.stat() if path.is_file() else None
    identity = job["verified_analysis"]["file_identity"] if job["verified_analysis"] else None
    changed = ((current is None or (current.st_size, current.st_mtime_ns) !=
               (identity["size_bytes"], identity["modified_time_ns"])) if identity else None)
    return dict(**job, output_file_exists=current is not None,
        output_file_size_bytes=current.st_size if current else None,
        output_metadata_changed_since_verification=changed)


def list_exports_jobs():
    if not JOBS.is_dir():
        return dict(jobs=[], truncated=False)
    paths = sorted(JOBS.glob("*.json"), key=lambda p: p.stat().st_mtime_ns, reverse=True)
    entries = []
    for path in paths[:100]:
        if re.fullmatch(r"[0-9a-f]{32}", path.stem):
            job = _read(path.stem)
            entries.append({key: job[key] for key in ("job_id", "label", "status", "output_path", "created_at")})
    return dict(jobs=entries, truncated=len(paths) > 100)


def _stable(path):
    if not path.is_file():
        raise ValueError("No export file yet; operate Live's export dialog first")
    previous = path.stat()
    for _ in range(2):
        time.sleep(0.5)
        current = path.stat()
        if (previous.st_size, previous.st_mtime_ns) != (current.st_size, current.st_mtime_ns):
            raise ValueError("Export file is still changing; wait for rendering to finish")
        previous = current
    if current.st_size == 0:
        raise ValueError("Export file is empty")


def finish_export(job_id, observed_settings, render_complete_observed):
    if render_complete_observed is not True:
        raise ValueError("Observe Live finishing its render before completing this job")
    observed = ObservedExportSettings.model_validate(observed_settings).model_dump()
    with LOCK:
        job = _read(job_id)
        if observed != job["requested_settings"]:
            raise ValueError("Observed export settings differ from the request; do not mark this export verified")
        if job["status"] == "verified":
            current = analyze_file(job["output_path"])
            if current["file_identity"]["sha256"] != job["verified_analysis"]["file_identity"]["sha256"]:
                raise ValueError("Previously verified export was replaced or edited; create a new export job")
            return dict(job_id=job_id, status="verified", output_path=job["output_path"],
                        analysis=job["verified_analysis"], already_verified=True)
        expected_path = (DEFAULT_EXPORTS / job_id / "master.wav").resolve()
        path = Path(job["output_path"])
        if path.resolve() != expected_path:
            raise ValueError("Export job path differs from its unique destination")
        _stable(path)
        with sf.SoundFile(str(path)) as audio:
            if (audio.format not in ("WAV", "WAVEX") or audio.subtype != "PCM_24"
                    or audio.channels != 2 or audio.samplerate != job["requested_settings"]["sample_rate_hz"]):
                raise ValueError("Export must be stereo 24-bit PCM WAV at the requested sample rate")
            if audio.frames <= 0:
                raise ValueError("Export contains no audio frames")
            duration = audio.frames / audio.samplerate
            expected = job["expected_duration_seconds"]
            if expected is not None and abs(duration - expected) > 1 / audio.samplerate:
                raise ValueError("Export duration differs from the requested duration by more than one frame")
        analysis = analyze_file(str(path))
        job.update(status="verified", completed_at=_now(), observed_settings=observed,
                   render_complete_observed=True, verified_analysis=analysis,
                   verification_limits=["Master selection, normalization and musical range are recorded UI observations, not proven by file headers",
                                        "File stability alone does not prove render completion; caller must observe Live finishing"])
        _save(job)
    return dict(job_id=job_id, status="verified", output_path=str(path), analysis=analysis,
                verification_limits=job["verification_limits"])
