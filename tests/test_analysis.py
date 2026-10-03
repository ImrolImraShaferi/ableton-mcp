import json
import unittest
import numpy as np
from tests.test_audio import AudioTests
from ableton_mcp.audio import analyze_file
from ableton_mcp.analysis import timeline, compare_reference, compare_stems


class SectionTests(AudioTests):
    def test_section_and_timeline_follow_level_and_frequency(self):
        low = .5 * np.sin(2 * np.pi * 100 * np.arange(self.rate * 3) / self.rate)
        high = .1 * np.sin(2 * np.pi * 3000 * np.arange(self.rate * 3) / self.rate)
        path = str(self.write(np.concatenate([low, high])))
        section = analyze_file(path, 3, 6)
        self.assertEqual(section['frames'], self.rate * 3)
        self.assertEqual(section['range']['start_seconds'], 3)
        self.assertAlmostEqual(section['sample_peak_dbfs'], -20, places=3)
        result = timeline(path)
        a, b = result['windows']
        self.assertLess(b['rms_dbfs'], a['rms_dbfs'] - 10)
        self.assertAlmostEqual(a['frequency_analysis']['dominant_frequency_hz'], 100, delta=6)
        self.assertAlmostEqual(b['frequency_analysis']['dominant_frequency_hz'], 3000, delta=6)
        self.assertIsNotNone(a['short_term_loudness_lufs'])
        self.assertAlmostEqual(b['spectral_peaks'][0]['frequency_hz'], 3000, delta=6)
        self.assertAlmostEqual(b['spectral_peaks'][0]['relative_to_max_db'], 0, places=6)
        json.dumps(result, allow_nan=False)

    def test_reference_gain_and_coverage(self):
        original = str(self.write(self.tone, 'original.wav'))
        quieter = str(self.write(self.tone / 2, 'quiet.wav'))
        result = compare_reference(quieter, original)
        self.assertAlmostEqual(result['suggested_mix_gain_db'], 6.0206, places=3)
        mid = next(b for b in result['frequency_band_deltas'] if b['name'] == 'mid')
        self.assertAlmostEqual(mid['loudness_matched_mix_minus_reference_db'], 0, places=5)

    def test_mono_cancellation_and_identical_channels(self):
        path = str(self.write(np.column_stack([self.tone, -self.tone])))
        result = timeline(path)['windows'][0]
        self.assertTrue(result['mono']['complete_cancellation'])
        self.assertIsNone(result['mono']['rms_dbfs'])
        path = str(self.write(np.column_stack([self.tone, self.tone])))
        result = timeline(path)['windows'][0]
        self.assertAlmostEqual(result['mono']['level_delta_db'], 0, places=6)

    def test_stem_coactivity_is_not_leakage(self):
        a = str(self.write(self.tone, 'a.wav'))
        b = str(self.write(self.tone / 2, 'b.wav'))
        result = compare_stems(a, b, True)
        active = [x for x in result['windows'][0]['bands'] if x['simultaneous_activity']]
        self.assertEqual([x['name'] for x in active], ['mid'])
        self.assertAlmostEqual(active[0]['first_minus_second_db'], 6.0206, places=3)
        other = .5 * np.sin(2 * np.pi * 3000 * np.arange(self.rate) / self.rate)
        b = str(self.write(other, 'b.wav'))
        result = compare_stems(a, b, True)
        self.assertFalse(any(x['simultaneous_activity'] for x in result['windows'][0]['bands']))
        with self.assertRaises(ValueError):
            compare_stems(a, b)
        b = str(self.write(other[:100], 'short.wav'))
        with self.assertRaises(ValueError):
            compare_stems(a, b, True)

    def test_validation_partial_windows_and_silence(self):
        path = str(self.write(np.zeros(self.rate)))
        result = timeline(path, window_seconds=.4, hop_seconds=.4)
        self.assertEqual(len(result['windows']), 3)
        self.assertFalse(result['windows'][-1]['complete_window'])
        self.assertIsNone(result['windows'][-1]['momentary_loudness_lufs'])
        json.dumps(result, allow_nan=False)
        for start, end in [(-1, 1), (0, 2), (1, 1), (float('nan'), 1), (True, 1), (0, .000001)]:
            with self.assertRaises(ValueError):
                analyze_file(path, start, end)
        for window, hop in [(.1, 1), (1, 0), (float('inf'), 1), (1, .001)]:
            with self.assertRaises(ValueError):
                timeline(path, window_seconds=window, hop_seconds=hop)


if __name__ == '__main__':
    unittest.main()
