"""Read-only measurements of manually exported audio; never alter source files."""
from pathlib import Path
import hashlib
import math
import os
import warnings

import numpy as np
import pyloudnorm as pyln
import soundfile as sf
from scipy.signal import welch

DEFAULT_EXPORTS = Path(__file__).resolve().parents[1] / "audio_exports"
MAX_DECODED_BYTES = 128 * 1024 * 1024
MAX_DURATION = 600
EXTENSIONS = {".wav", ".aif", ".aiff", ".flac"}
BANDS = [("infra", 0, 20), ("sub", 20, 60), ("bass", 60, 250),
         ("low_mid", 250, 500), ("mid", 500, 2000), ("presence", 2000, 6000),
         ("treble", 6000, 12000), ("air", 12000, 20000), ("ultrasonic", 20000, None)]


def frequency_analysis(data, rate):
    """Average channel powers rather than summing channels, retaining anti-phase energy."""
    segment = min(8192, len(data))
    if segment < 2:
        return dict(available=False, reason="At least two samples are needed")
    hop = segment - segment // 2
    accumulated = None
    windows = 0
    # Bound FFT workspace: at most 64 windows at a time, retaining global overlap.
    for start in range(0, len(data) - segment + 1, hop * 64):
        block = data[start:min(start + hop * 63 + segment, len(data))].astype(np.float64)
        count = (len(block) - segment) // hop + 1
        frequencies, density = welch(block, fs=rate, window="hann", nperseg=segment,
            noverlap=segment // 2, detrend="constant", scaling="density", axis=0)
        power_density = np.mean(density, axis=1)
        accumulated = power_density * count if accumulated is None else accumulated + power_density * count
        windows += count
    density = accumulated / windows
    bin_width = rate / segment
    bin_power = density * bin_width
    total = float(np.sum(bin_power))
    bands = []
    for name, low, high in BANDS:
        supported = low < rate / 2
        upper = min(high if high is not None else rate / 2, rate / 2)
        # The final available band includes Nyquist; all other upper edges are exclusive.
        mask = (frequencies >= low) & ((frequencies <= upper) if upper == rate / 2 else (frequencies < upper))
        power = float(np.sum(bin_power[mask])) if supported else None
        bands.append(dict(name=name, low_hz=low, high_hz=upper if supported else None,
            available=supported, power_dbfs=db(math.sqrt(power)) if power is not None else None,
            energy_percent=100 * power / total if supported and total > 0 else None))
    non_dc = frequencies > 0
    audible_power = float(np.sum(bin_power[non_dc]))
    dominant = float(frequencies[non_dc][np.argmax(density[non_dc])]) if audible_power > 0 else None
    centroid = float(np.sum(frequencies * bin_power) / total) if total > 0 else None
    return dict(available=True, method="Welch PSD, Hann window, 50% overlap, per-channel power average",
        frequency_resolution_hz=bin_width, window_samples=segment, window_count=windows,
        spectral_rms_dbfs=db(math.sqrt(total)), dominant_frequency_hz=dominant,
        spectral_centroid_hz=centroid, bands=bands,
        limits=["DC removed per window; trailing incomplete windows are excluded",
                "Whole-file average can hide changes confined to a short section",
                "Bands differ in width; energy shares are descriptive, not EQ targets",
                "Resolution limits narrow peaks and short exports; no instrument separation or masking diagnosis"])


def db(amplitude):
    return float(20 * math.log10(amplitude)) if amplitude > 0 else None


def list_exports(folder=None):
    directory = Path(folder) if folder else DEFAULT_EXPORTS
    if not directory.is_absolute():
        raise ValueError("Provide an absolute export folder path")
    if not directory.is_dir():
        raise ValueError("Export folder does not exist: " + str(directory))
    paths = sorted((p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in EXTENSIONS),
                   key=lambda p: p.name.lower())
    return dict(folder=str(directory.resolve()), files=[str(p.resolve()) for p in paths[:100]],
                truncated=len(paths) > 100)


def analyze_file(path, start_seconds=0, end_seconds=None, processor=None):
    source = Path(path)
    if not source.is_absolute() or not source.is_file():
        raise ValueError("Provide an absolute path to an existing audio file")
    if source.suffix.lower() not in EXTENSIONS:
        raise ValueError("Supported exports: WAV, AIFF and FLAC")
    # Hash and decode the same open file; reject in-place edits during analysis.
    with source.open("rb") as raw:
        before = os.fstat(raw.fileno())
        digest = hashlib.sha256()
        for chunk in iter(lambda: raw.read(1024 * 1024), b""):
            digest.update(chunk)
        raw.seek(0)
        result = _analyze_open_file(source, raw, start_seconds, end_seconds, processor)
        after = os.fstat(raw.fileno())
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError("Audio changed during analysis; retry after export finishes")
        result["file_identity"] = dict(sha256=digest.hexdigest(), size_bytes=after.st_size,
                                      modified_time_ns=after.st_mtime_ns)
        return result


def _analyze_open_file(source, raw, start_seconds=0, end_seconds=None, processor=None):
    with sf.SoundFile(raw) as audio:
        if audio.format not in {"WAV", "WAVEX", "RF64", "AIFF", "FLAC"}:
            raise ValueError("Unsupported audio container")
        if audio.channels not in (1, 2):
            raise ValueError("Only mono and stereo exports are supported")
        if not 8000 <= audio.samplerate <= 192000:
            raise ValueError("Supported sample rates: 8000..192000 Hz")
        source_duration = audio.frames / audio.samplerate
        end_seconds = source_duration if end_seconds is None else end_seconds
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
               for v in (start_seconds, end_seconds)):
            raise ValueError("Range times must be finite numbers")
        if not 0 <= start_seconds < end_seconds <= source_duration:
            raise ValueError("Range must satisfy 0 <= start < end <= file duration")
        first, last = round(start_seconds * audio.samplerate), round(end_seconds * audio.samplerate)
        if last <= first:
            raise ValueError("Range must contain at least one sample")
        duration = (last - first) / audio.samplerate
        if audio.frames == 0:
            raise ValueError("Audio file is empty")
        if duration > MAX_DURATION or (last - first) * audio.channels * 4 > MAX_DECODED_BYTES:
            raise ValueError("Export exceeds 10 minutes or 128 MiB decoded float32 audio; export a shorter section")
        rate, channels, subtype, container = audio.samplerate, audio.channels, audio.subtype, audio.format
        audio.seek(first)
        data = audio.read(last - first, dtype="float32", always_2d=True)
    if not np.isfinite(data).all():
        raise ValueError("Audio contains NaN or infinite samples")
    result = (processor(data, rate, channels, subtype, container, source, first / rate)
              if processor else analyze_samples(data, rate, channels, subtype, container, source))
    result["range"] = dict(start_seconds=first / rate, end_seconds=last / rate,
                           source_duration_seconds=source_duration)
    return result


def analyze_samples(data, rate, channels, subtype, container, source):
    duration = len(data) / rate
    if not np.isfinite(data).all():
        raise ValueError("Audio contains NaN or infinite samples")
    notices = []
    absolute = np.abs(data)
    peaks = np.max(absolute, axis=0)
    # Use float64 accumulation while keeping the source buffer bounded.
    powers = np.mean(np.square(data, dtype=np.float64), axis=0)
    rms = np.sqrt(powers)
    means = np.mean(data, axis=0, dtype=np.float64)
    full_scale = np.count_nonzero(absolute >= 1, axis=0)
    near_scale = np.count_nonzero(absolute >= 10 ** (-0.1 / 20), axis=0)
    del absolute
    loudness = None
    if duration < 0.4:
        notices.append("Integrated loudness unavailable: audio shorter than the 400 ms gating block")
    elif np.any(peaks > 0):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            measured = pyln.Meter(rate).integrated_loudness(data)
        if math.isfinite(measured):
            loudness = float(measured)
        else:
            notices.append("Integrated loudness unavailable: signal is below the meter's gate")
    else:
        notices.append("Silent audio: logarithmic levels and integrated loudness are null")
    if np.any(full_scale):
        notices.append("Samples reach/exceed digital full scale; this flags clipping risk, not proof of prior clipping")
    stereo = None
    if channels == 2:
        left, right = data[:, 0], data[:, 1]
        variances = np.maximum(powers - means ** 2, 0)
        covariance = float(np.mean(np.multiply(left, right, dtype=np.float64)) - means[0] * means[1])
        denominator = float(np.sqrt(variances[0] * variances[1]))
        correlation = float(np.clip(covariance / denominator, -1, 1)) if denominator > 1e-20 else None
        # Direct sum/difference avoids cancellation error for identical or inverted channels.
        mid = (left.astype(np.float64) + right) / 2
        mid_power = float(np.mean(mid * mid))
        del mid
        side = (left.astype(np.float64) - right) / 2
        side_power = float(np.mean(side * side))
        del side
        stereo = dict(correlation=correlation,
                      left_minus_right_rms_db=db(float(rms[0] / rms[1])) if np.all(rms > 0) else None,
                      mid_rms_dbfs=db(math.sqrt(mid_power)), side_rms_dbfs=db(math.sqrt(side_power)))
        if correlation is not None and correlation < 0:
            notices.append("Negative overall stereo correlation: check mono compatibility by listening")
    return dict(path=str(source.resolve()), sample_rate_hz=rate, channels=channels,
                duration_seconds=duration, frames=len(data), subtype=subtype, container=container,
                integrated_loudness_lufs=loudness,
                sample_peak_dbfs=db(float(np.max(peaks))),
                rms_dbfs=db(math.sqrt(float(np.mean(powers)))),
                channel_metrics=[dict(channel=("mono" if channels == 1 else ("left", "right")[i]),
                    sample_peak_dbfs=db(float(peaks[i])), rms_dbfs=db(float(rms[i])),
                    crest_factor_db=db(float(peaks[i] / rms[i])) if rms[i] > 0 else None,
                    dc_offset=float(means[i]), full_scale_sample_count=int(full_scale[i]),
                    near_full_scale_sample_count=int(near_scale[i])) for i in range(channels)],
                stereo=stereo, frequency_analysis=frequency_analysis(data, rate), notices=notices,
                limits=["Sample peaks only; inter-sample true peaks are not measured",
                        "Measurements do not determine mix quality or an appropriate loudness target",
                        "No audio playback, export, normalization, or Live set changes are performed"])


def difference(original, revised):
    return revised - original if original is not None and revised is not None else None


def compare_files(original_path, revised_path):
    original, revised = analyze_file(original_path), analyze_file(revised_path)
    fields = ("sample_rate_hz", "channels", "frames", "subtype", "container")
    mismatches = [dict(field=key, original=original[key], revised=revised[key])
                  for key in fields if original[key] != revised[key]]
    duration_delta = revised["duration_seconds"] - original["duration_seconds"]
    duration_match = abs(duration_delta) <= 0.5 / max(original["sample_rate_hz"], revised["sample_rate_hz"])
    if not duration_match:
        mismatches.append(dict(field="duration_seconds", original=original["duration_seconds"],
                               revised=revised["duration_seconds"]))
    deltas = {key: difference(original[key], revised[key]) for key in
              ("integrated_loudness_lufs", "sample_peak_dbfs", "rms_dbfs")}
    deltas["duration_seconds"] = duration_delta
    deltas["full_scale_sample_count"] = (sum(c["full_scale_sample_count"] for c in revised["channel_metrics"])
                                            - sum(c["full_scale_sample_count"] for c in original["channel_metrics"]))
    if original["stereo"] is not None and revised["stereo"] is not None:
        deltas["stereo"] = {key: difference(original["stereo"][key], revised["stereo"][key])
                           for key in original["stereo"]}
        deltas["stereo"]["side_minus_mid_db"] = difference(
            difference(original["stereo"]["mid_rms_dbfs"], original["stereo"]["side_rms_dbfs"]),
            difference(revised["stereo"]["mid_rms_dbfs"], revised["stereo"]["side_rms_dbfs"]))
    else:
        deltas["stereo"] = None
    frequency_deltas = []
    old_spectrum, new_spectrum = original["frequency_analysis"], revised["frequency_analysis"]
    if old_spectrum["available"] and new_spectrum["available"]:
        spectral_gain = difference(old_spectrum["spectral_rms_dbfs"], new_spectrum["spectral_rms_dbfs"])
        for old, new in zip(old_spectrum["bands"], new_spectrum["bands"]):
            same_coverage = old["available"] and new["available"] and old["high_hz"] == new["high_hz"]
            raw_delta = difference(old["power_dbfs"], new["power_dbfs"]) if same_coverage else None
            frequency_deltas.append(dict(name=old["name"], same_frequency_coverage=same_coverage,
                power_delta_db=raw_delta,
                gain_adjusted_delta_db=raw_delta - spectral_gain if raw_delta is not None and spectral_gain is not None else None,
                energy_share_delta_percentage_points=difference(old["energy_percent"], new["energy_percent"]) if same_coverage else None))
    notices = ["Deltas are revised minus original; null means unavailable",
               "Matching metadata does not establish time alignment or identical musical content",
               "No resampling, alignment, normalization or playback is performed"]
    if mismatches:
        notices.append("Export metadata differs; use the same range and format for a controlled comparison")
    if original["file_identity"]["sha256"] == revised["file_identity"]["sha256"]:
        notices.append("Both paths contain identical file bytes")
    return dict(original=original, revised=revised, export_metadata_match=not mismatches,
                duration_match=duration_match, mismatches=mismatches, deltas=deltas,
                frequency_band_deltas=frequency_deltas,
                revised_listening_gain_db=difference(revised["integrated_loudness_lufs"], original["integrated_loudness_lufs"]),
                listening_gain_note="Suggested gain on the revised version to match original integrated LUFS; not applied",
                notices=notices)
