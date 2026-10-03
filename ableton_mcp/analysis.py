"""Section and timeline measurements; never modify audio or the Live set."""
import math

import numpy as np
import pyloudnorm as pyln
from scipy.signal import welch, find_peaks

from .audio import analyze_file, analyze_samples, difference, frequency_analysis, db

MAX_WINDOWS = 300


def _spectral_peaks(data, rate):
    if len(data) < 2:
        return []
    frequencies, density = welch(data, fs=rate, nperseg=min(8192, len(data)), axis=0)
    density = np.mean(density, axis=1)
    indices, _ = find_peaks(density)
    indices = [i for i in indices if 20 <= frequencies[i] <= 20000 and density[i] > 0]
    strongest = sorted(indices, key=lambda i: density[i], reverse=True)[:8]
    maximum = float(np.max(density))
    return [dict(frequency_hz=float(frequencies[i]),
                 relative_to_max_db=float(10 * math.log10(density[i] / maximum)))
            for i in strongest if density[i] >= maximum * 1e-6]


def _positive(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(name + " must be a positive finite number")


def _ungated_loudness(data, rate, seconds):
    if len(data) < round(seconds * rate):
        return None
    # Full-window K-weighted energy, with no relative or absolute gating.
    meter = pyln.Meter(rate)
    filtered = data.astype(np.float64)
    for stage in meter._filters.values():
        for channel in range(filtered.shape[1]):
            filtered[:, channel] = stage.apply_filter(filtered[:, channel])
    energy = float(np.sum(np.mean(filtered ** 2, axis=0)))
    return -0.691 + 10 * math.log10(energy) if energy > 0 else None


def timeline(path, start_seconds=0, end_seconds=None, window_seconds=3, hop_seconds=3):
    _positive(window_seconds, "window_seconds")
    _positive(hop_seconds, "hop_seconds")
    if window_seconds < 0.4:
        raise ValueError("window_seconds must be at least 0.4")

    def process(data, rate, channels, subtype, container, source, offset):
        window, hop = round(window_seconds * rate), max(1, round(hop_seconds * rate))
        count = math.ceil(len(data) / hop)
        if count > MAX_WINDOWS:
            raise ValueError("Timeline exceeds 300 windows; increase hop_seconds or select a shorter range")
        rows = []
        for first in range(0, len(data), hop):
            block = data[first:first + window]
            metrics = analyze_samples(block, rate, channels, subtype, container, source)
            stereo = metrics["stereo"]
            mono = np.mean(block, axis=1, dtype=np.float64)[:, None]
            mono_spectrum = frequency_analysis(mono, rate)
            mono_rms = db(float(np.sqrt(np.mean(mono ** 2))))
            mono_bands = mono_spectrum.get("bands", [])
            bands = metrics["frequency_analysis"].get("bands", [])
            rows.append(dict(start_seconds=offset + first / rate,
                end_seconds=offset + (first + len(block)) / rate,
                complete_window=len(block) == window,
                integrated_loudness_lufs=metrics["integrated_loudness_lufs"],
                momentary_loudness_lufs=_ungated_loudness(block[-round(.4 * rate):], rate, .4),
                short_term_loudness_lufs=_ungated_loudness(block[-3 * rate:], rate, 3),
                sample_peak_dbfs=metrics["sample_peak_dbfs"], rms_dbfs=metrics["rms_dbfs"],
                channel_crest_factors=[dict(channel=c["channel"], crest_factor_db=c["crest_factor_db"]) for c in metrics["channel_metrics"]], stereo=stereo,
                frequency_analysis=metrics["frequency_analysis"],
                spectral_peaks=_spectral_peaks(block, rate),
                mono=dict(rms_dbfs=mono_rms,
                    level_delta_db=difference(metrics["rms_dbfs"], mono_rms),
                    complete_cancellation=bool(np.any(block != 0) and not np.any(mono != 0)),
                    frequency_band_deltas=[dict(name=a["name"],
                        power_delta_db=difference(a["power_dbfs"], b["power_dbfs"]),
                        complete_cancellation=a["power_dbfs"] is not None and b["power_dbfs"] is None)
                        for a, b in zip(bands, mono_bands)])))
        return dict(path=str(source.resolve()), sample_rate_hz=rate, channels=channels,
            window_seconds=window / rate, hop_seconds=hop / rate, windows=rows,
            limits=["Momentary/short-term: ungated K-weighted final 0.4/3 seconds of each window; filter resets per window",
                    "Partial windows are retained; unavailable loudness is null",
                    "Mono is (L+R)/2, compared against average stereo channel power",
                    "Sample peaks only; no true-peak measurement, instrument identification or mix-quality score"])
    return analyze_file(path, start_seconds, end_seconds, process)


def compare_reference(mix_path, reference_path, mix_start_seconds=0, mix_end_seconds=None,
                      reference_start_seconds=0, reference_end_seconds=None):
    mix = analyze_file(mix_path, mix_start_seconds, mix_end_seconds)
    reference = analyze_file(reference_path, reference_start_seconds, reference_end_seconds)
    gain = difference(mix["integrated_loudness_lufs"], reference["integrated_loudness_lufs"])
    bands = []
    for a, b in zip(mix["frequency_analysis"].get("bands", []),
                    reference["frequency_analysis"].get("bands", [])):
        coverage = a["available"] and b["available"] and a["high_hz"] == b["high_hz"]
        delta = difference(b["power_dbfs"], a["power_dbfs"]) if coverage else None
        bands.append(dict(name=a["name"], same_frequency_coverage=coverage,
            mix_minus_reference_db=delta,
            loudness_matched_mix_minus_reference_db=delta + gain if delta is not None and gain is not None else None))
    return dict(mix=mix, reference=reference, suggested_mix_gain_db=gain,
        frequency_band_deltas=bands,
        limits=["Gain is suggested only; files are unchanged",
                "Choose musically comparable passages; durations may differ",
                "No resampling or universal tonal target; null means unavailable"])


def compare_stems(first_path, second_path, alignment_confirmed=False, start_seconds=0,
                  end_seconds=None, window_seconds=3, hop_seconds=3):
    if alignment_confirmed is not True:
        raise ValueError("Confirm both stems share the same timeline origin; metadata cannot prove alignment")
    first = timeline(first_path, start_seconds, end_seconds, window_seconds, hop_seconds)
    second = timeline(second_path, start_seconds, end_seconds, window_seconds, hop_seconds)
    if any(first[key] != second[key] for key in ("sample_rate_hz", "range")):
        raise ValueError("Stems must have identical sample rates, source durations and selected ranges")
    rows = []
    for a, b in zip(first["windows"], second["windows"]):
        bands = []
        for x, y in zip(a["frequency_analysis"].get("bands", []), b["frequency_analysis"].get("bands", [])):
            # Relative activity floor prevents FFT leakage from flagging every band.
            active = (x["energy_percent"] is not None and y["energy_percent"] is not None
                      and x["energy_percent"] >= 1 and y["energy_percent"] >= 1
                      and x["power_dbfs"] is not None and y["power_dbfs"] is not None
                      and x["power_dbfs"] > -70 and y["power_dbfs"] > -70)
            bands.append(dict(name=x["name"], simultaneous_activity=active,
                first_power_dbfs=x["power_dbfs"], second_power_dbfs=y["power_dbfs"],
                first_minus_second_db=difference(y["power_dbfs"], x["power_dbfs"])))
        rows.append(dict(start_seconds=a["start_seconds"], end_seconds=a["end_seconds"], bands=bands))
    return dict(first_path=first_path, second_path=second_path,
        first_file_identity=first["file_identity"], second_file_identity=second["file_identity"],
        range=first["range"], windows=rows,
        limits=["User-confirmed alignment; no automatic alignment or source separation",
                "Simultaneous activity: each band >=1% of its stem energy and >-70 dBFS",
                "Broad-band coactivity suggests possible competition, not perceptual masking or an EQ prescription"])
