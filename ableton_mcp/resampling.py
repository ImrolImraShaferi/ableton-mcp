"""Archive completed Live resampling captures; never modify Live's source recording."""
import json
from pathlib import Path
import re
import shutil
import threading
import uuid

from .audio import DEFAULT_EXPORTS, analyze_file
from .exports import _stable, _now

LOCK = threading.Lock()


def finish_capture(capture_id, call):
    if not isinstance(capture_id, str) or not re.fullmatch(r"[0-9a-f]{32}", capture_id):
        raise ValueError("Invalid capture ID")
    with LOCK:
        directory = DEFAULT_EXPORTS / ("capture-" + capture_id)
        manifest = directory / "capture.json"
        if manifest.is_file():
            result = json.loads(manifest.read_text(encoding="utf-8"))
            current = analyze_file(result["output_path"])
            if current["file_identity"]["sha256"] != result["analysis"]["file_identity"]["sha256"]:
                raise ValueError("Archived capture was modified")
            return result
        job = call("get_resampling_capture", capture_id=capture_id)
        if job["status"] != "completed" or job.get("cleanup_errors"):
            raise ValueError("Capture must complete successfully before archiving: " + job["status"])
        source = Path(job["source_path"])
        if not source.is_absolute() or source.suffix.lower() not in {".wav", ".aif", ".aiff"}:
            raise ValueError("Live must return an absolute WAV/AIFF recording path")
        _stable(source)
        before = source.stat()
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / ("master" + source.suffix.lower())
        temporary = directory / (uuid.uuid4().hex + source.suffix.lower())
        try:
            try:
                shutil.copyfile(source, temporary)
            except PermissionError as exc:
                if job.get("crop_requested") or job.get("native_crop_applied"):
                    raise ValueError("Live's finalized capture is still locked; wait and retry") from exc
                result = call("finalize_resampling_capture", capture_id=capture_id)
                raise ValueError("Live holds its recording file open. Native audio crop requested; poll capture status, then finish again: "
                                 + result["status"]) from exc
            after = source.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError("Live recording changed during copy; retry after it settles")
            analysis = analyze_file(str(temporary.resolve()))
            expected = job["length_beats"] * 60 / job["tempo_at_start"]
            # Real-time capture may contain a small record-boundary discrepancy.
            if abs(analysis["duration_seconds"] - expected) > 0.1:
                raise ValueError("Recorded duration differs from requested range by over 100 ms")
            if analysis["channels"] != 2:
                raise ValueError("Master capture must be stereo")
            temporary.replace(destination)
            analysis["path"] = str(destination.resolve())
            result = dict(capture_id=capture_id, status="archived", completed_at=_now(),
                          output_path=str(destination.resolve()), capture=job, analysis=analysis,
                          limits=["Real-time fixed-tempo Arrangement capture, not offline export",
                                  "100 ms duration tolerance does not prove sample-accurate passage alignment",
                                  "Recording format/rate come from Live's recording preferences",
                                  "Includes Master processing; start-of-passage effect state may differ from continuous playback",
                                  "No normalization, dither, playback or perceptual listening is performed"])
            temp_manifest = directory / (uuid.uuid4().hex + ".tmp")
            try:
                temp_manifest.write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
                temp_manifest.replace(manifest)
            finally:
                temp_manifest.unlink(missing_ok=True)
            return result
        finally:
            temporary.unlink(missing_ok=True)


def list_captures():
    paths = sorted(DEFAULT_EXPORTS.glob("capture-*/capture.json"),
                   key=lambda p: p.stat().st_mtime_ns, reverse=True)
    entries = []
    for path in paths[:100]:
        job = json.loads(path.read_text(encoding="utf-8"))
        entries.append({key: job[key] for key in ("capture_id", "status", "output_path", "completed_at")})
    return dict(captures=entries, truncated=len(paths) > 100)
