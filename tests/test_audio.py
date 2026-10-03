import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf
from ableton_mcp.audio import analyze_file, list_exports, compare_files


class AudioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.rate = 48000
        self.tone = 0.5 * np.sin(2 * np.pi * 1000 * np.arange(self.rate) / self.rate)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, data, name="test.wav"):
        path = self.folder / name
        sf.write(path, data, self.rate, subtype="FLOAT")
        return path

    def test_known_sine_levels_and_no_file_changes(self):
        path = self.write(self.tone)
        original = hashlib.sha256(path.read_bytes()).digest()
        result = analyze_file(str(path))
        self.assertAlmostEqual(result["sample_peak_dbfs"], -6.0206, places=3)
        self.assertAlmostEqual(result["rms_dbfs"], -9.0309, places=3)
        self.assertAlmostEqual(result["channel_metrics"][0]["crest_factor_db"], 3.0103, places=3)
        # 1 kHz mono sine, K-weighting plus BS.1770 offset yields approximately -9.07 LUFS.
        self.assertAlmostEqual(result["integrated_loudness_lufs"], -9.07, delta=0.15)
        self.assertIsNone(result["stereo"])
        self.assertEqual(original, hashlib.sha256(path.read_bytes()).digest())
        json.dumps(result, allow_nan=False)

    def test_silence_and_short_audio_are_json_safe(self):
        result = analyze_file(str(self.write(np.zeros((self.rate, 2)))))
        self.assertIsNone(result["sample_peak_dbfs"])
        self.assertIsNone(result["integrated_loudness_lufs"])
        self.assertIsNone(result["stereo"]["correlation"])
        json.dumps(result, allow_nan=False)
        short = analyze_file(str(self.write(self.tone[:100])))
        self.assertIsNone(short["integrated_loudness_lufs"])
        self.assertIn("400 ms", short["notices"][0])

    def test_stereo_balance_correlation_and_antiphase(self):
        result = analyze_file(str(self.write(np.column_stack((self.tone, self.tone / 2)))))
        self.assertAlmostEqual(result["stereo"]["left_minus_right_rms_db"], 6.0206, places=3)
        self.assertAlmostEqual(result["stereo"]["correlation"], 1, places=6)
        stereo_equal = analyze_file(str(self.write(np.column_stack((self.tone, self.tone)))))
        mono = analyze_file(str(self.write(self.tone)))
        self.assertAlmostEqual(stereo_equal["integrated_loudness_lufs"] - mono["integrated_loudness_lufs"], 3.0103, places=3)
        anti = analyze_file(str(self.write(np.column_stack((self.tone, -self.tone)))))
        self.assertAlmostEqual(anti["stereo"]["correlation"], -1, places=6)
        self.assertIsNone(anti["stereo"]["mid_rms_dbfs"])
        self.assertTrue(any("mono compatibility" in n for n in anti["notices"]))

    def test_full_scale_counts_and_dc(self):
        data = self.tone.copy()
        data[:3] = [1, -1, 1.2]
        result = analyze_file(str(self.write(data)))
        self.assertEqual(result["channel_metrics"][0]["full_scale_sample_count"], 3)
        self.assertGreater(result["sample_peak_dbfs"], 0)
        dc = analyze_file(str(self.write(np.full((self.rate, 2), 0.1))))
        self.assertAlmostEqual(dc["channel_metrics"][0]["dc_offset"], 0.1, places=6)
        self.assertIsNone(dc["stereo"]["correlation"])

    def test_invalid_inputs_and_limits(self):
        for path in ["relative.wav", str(self.folder / "missing.wav")]:
            with self.assertRaises(ValueError):
                analyze_file(path)
        with self.assertRaises(ValueError):
            analyze_file(str(self.write(np.zeros((100, 3)))))
        with self.assertRaises(ValueError):
            analyze_file(str(self.write(np.array([float("nan"), 0]))))
        path = self.write(self.tone)
        with patch("ableton_mcp.audio.MAX_DECODED_BYTES", 100):
            with self.assertRaises(ValueError):
                analyze_file(str(path))

    def test_export_listing_filters_and_does_not_recurse(self):
        path = self.write(self.tone)
        (self.folder / "token.txt").write_text("private")
        (self.folder / "subfolder").mkdir()
        self.write(self.tone, "subfolder/hidden.wav")
        self.assertEqual(list_exports(str(self.folder))["files"], [str(path)])
        with self.assertRaises(ValueError):
            list_exports("relative")

    def test_frequency_known_tone_and_stereo_antiphase(self):
        mono = analyze_file(str(self.write(self.tone)))
        spectrum = mono["frequency_analysis"]
        bands = {b["name"]: b for b in spectrum["bands"]}
        self.assertGreater(bands["mid"]["energy_percent"], 99.9)
        self.assertAlmostEqual(spectrum["spectral_rms_dbfs"], -9.0309, places=3)
        self.assertAlmostEqual(spectrum["dominant_frequency_hz"], 1000, delta=spectrum["frequency_resolution_hz"])
        self.assertAlmostEqual(spectrum["spectral_centroid_hz"], 1000, delta=1)
        self.assertAlmostEqual(sum(b["energy_percent"] for b in spectrum["bands"]), 100, places=6)
        anti = analyze_file(str(self.write(np.column_stack((self.tone, -self.tone)))))
        self.assertAlmostEqual(anti["frequency_analysis"]["spectral_rms_dbfs"], spectrum["spectral_rms_dbfs"], places=6)

    def test_frequency_sub_bass_and_nyquist_coverage(self):
        tone = 0.5 * np.sin(2 * np.pi * 50 * np.arange(self.rate) / self.rate)
        result = analyze_file(str(self.write(tone)))
        bands = {b["name"]: b for b in result["frequency_analysis"]["bands"]}
        self.assertGreater(bands["sub"]["energy_percent"], 90)
        path = self.folder / "low_rate.wav"
        sf.write(path, self.tone[:8000], 8000, subtype="FLOAT")
        bands = {b["name"]: b for b in analyze_file(str(path))["frequency_analysis"]["bands"]}
        self.assertFalse(bands["treble"]["available"])
        self.assertIsNone(bands["treble"]["energy_percent"])
        self.assertEqual(bands["presence"]["high_hz"], 4000)

    def test_frequency_silence_single_sample_and_streamed_windows(self):
        result = analyze_file(str(self.write(np.zeros(self.rate))))
        self.assertIsNone(result["frequency_analysis"]["spectral_centroid_hz"])
        self.assertIsNone(result["frequency_analysis"]["bands"][0]["energy_percent"])
        single = analyze_file(str(self.write(np.zeros(1))))
        self.assertFalse(single["frequency_analysis"]["available"])
        long = analyze_file(str(self.write(np.tile(self.tone, 7))))
        self.assertGreater(long["frequency_analysis"]["window_count"], 64)
        self.assertAlmostEqual(long["frequency_analysis"]["spectral_rms_dbfs"], -9.0309, places=3)
        json.dumps(long, allow_nan=False)

    def test_comparison_gain_separates_level_and_spectral_shape(self):
        original = self.write(self.tone, "original.wav")
        revised = self.write(self.tone / 2, "revised.wav")
        result = compare_files(str(original), str(revised))
        self.assertTrue(result["export_metadata_match"])
        self.assertAlmostEqual(result["deltas"]["sample_peak_dbfs"], -6.0206, places=3)
        self.assertAlmostEqual(result["deltas"]["integrated_loudness_lufs"], -6.0206, places=3)
        self.assertAlmostEqual(result["revised_listening_gain_db"], 6.0206, places=3)
        mid = next(b for b in result["frequency_band_deltas"] if b["name"] == "mid")
        self.assertAlmostEqual(mid["gain_adjusted_delta_db"], 0, places=5)
        self.assertNotEqual(result["original"]["file_identity"]["sha256"], result["revised"]["file_identity"]["sha256"])

    def test_comparison_flags_different_exports_and_silence(self):
        original = self.write(self.tone, "original.wav")
        revised = self.write(np.zeros((100, 2)), "revised.wav")
        result = compare_files(str(original), str(revised))
        self.assertFalse(result["export_metadata_match"])
        self.assertFalse(result["duration_match"])
        self.assertIsNone(result["deltas"]["integrated_loudness_lufs"])
        self.assertIsNone(result["deltas"]["stereo"])
        json.dumps(result, allow_nan=False)
        identical = compare_files(str(original), str(original))
        self.assertEqual(identical["deltas"]["sample_peak_dbfs"], 0)
        self.assertTrue(any("identical file bytes" in n for n in identical["notices"]))

    def test_comparison_frequency_change_and_samplerate_mismatch(self):
        original = self.write(self.tone, "original.wav")
        t = np.arange(self.rate) / self.rate
        revised = self.write(self.tone + 0.5 * np.sin(2 * np.pi * 3000 * t), "revised.wav")
        result = compare_files(str(original), str(revised))
        presence = next(b for b in result["frequency_band_deltas"] if b["name"] == "presence")
        self.assertGreater(presence["energy_share_delta_percentage_points"], 49)
        lower = self.folder / "low.wav"
        sf.write(lower, 0.5 * np.sin(2 * np.pi * 1000 * np.arange(8000) / 8000), 8000, subtype="FLOAT")
        result = compare_files(str(original), str(lower))
        self.assertTrue(result["duration_match"])
        self.assertIn("sample_rate_hz", [m["field"] for m in result["mismatches"]])
        presence = next(b for b in result["frequency_band_deltas"] if b["name"] == "presence")
        self.assertFalse(presence["same_frequency_coverage"])
        self.assertIsNone(presence["gain_adjusted_delta_db"])


if __name__ == "__main__":
    unittest.main()
