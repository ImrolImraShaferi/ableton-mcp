from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf
from ableton_mcp import exports as exp


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.overrides = [patch.object(exp, "JOBS", self.root / "jobs"),
                          patch.object(exp, "DEFAULT_EXPORTS", self.root / "audio"),
                          patch.object(exp.time, "sleep", lambda seconds: None)]
        for override in self.overrides:
            override.start()
        self.tone = 0.5 * np.sin(2 * np.pi * 1000 * np.arange(48000) / 48000)

    def tearDown(self):
        for override in reversed(self.overrides):
            override.stop()
        self.temp.cleanup()

    def prepare(self, **kwargs):
        return exp.prepare_export("1.1.1", "1.0.0", expected_duration_seconds=1, **kwargs)

    def finish(self, job, settings=None, completed=True):
        return exp.finish_export(job["job_id"], settings or job["requested_settings"], completed)

    def write(self, job, subtype="PCM_24", rate=48000, stereo=True, tone=None):
        signal = self.tone if tone is None else tone
        data = np.column_stack((signal, signal)) if stereo else signal
        sf.write(job["output_path"], data, rate, subtype=subtype)

    def test_prepare_unique_pending_paths_and_list(self):
        first, second = self.prepare(), self.prepare()
        self.assertNotEqual(first["output_path"], second["output_path"])
        self.assertEqual(first["status"], "awaiting_ui_export")
        self.assertFalse(first["requested_settings"]["normalize"])
        self.assertFalse(Path(first["output_path"]).exists())
        self.assertEqual(len(exp.list_exports_jobs()["jobs"]), 2)
        self.assertFalse(exp.get_export(first["job_id"])["output_file_exists"])

    def test_settings_and_range_validation(self):
        for start, length in [("0.1.1", "1.0.0"), ("1.5.1", "1.0.0"),
                              ("1.1.1", "0.0.0"), ("1.1.1", "1.4.0"), ("../file", "1.0.0")]:
            with self.assertRaises(ValueError):
                exp.prepare_export(start, length)
        for rate in (True, 12345, 48000.0):
            with self.assertRaises(ValueError):
                self.prepare(sample_rate_hz=rate)
        for duration in (float("nan"), float("inf"), True, -1, 601):
            with self.assertRaises(ValueError):
                exp.prepare_export("1.1.1", "1.0.0", expected_duration_seconds=duration)
        with self.assertRaises(ValueError):
            exp.prepare_export("1.1.1", "1.0.0", sample_rate_hz=96000, expected_duration_seconds=500)
        with self.assertRaises(ValueError):
            exp.get_export("../private")

    def test_requires_file_observation_and_exact_observed_settings(self):
        job = self.prepare()
        with self.assertRaises(ValueError):
            self.finish(job, completed=False)
        with self.assertRaisesRegex(ValueError, "No export file"):
            self.finish(job)
        self.write(job)
        with self.assertRaisesRegex(ValueError, "settings differ"):
            self.finish(job, dict(job["requested_settings"], normalize=True))
        self.assertEqual(exp.get_export(job["job_id"])["status"], "awaiting_ui_export")

    def test_valid_file_analysis_and_duplicate_completion(self):
        job = self.prepare()
        self.write(job)
        result = self.finish(job)
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["analysis"]["subtype"], "PCM_24")
        self.assertTrue(self.finish(job)["already_verified"])
        self.assertFalse(exp.get_export(job["job_id"])["output_metadata_changed_since_verification"])

    def test_format_rate_channels_and_duration_mismatches(self):
        job = self.prepare()
        for settings in [dict(subtype="PCM_16"), dict(rate=44100), dict(stereo=False),
                         dict(tone=self.tone[:1000])]:
            self.write(job, **settings)
            with self.assertRaises(ValueError):
                self.finish(job)
        self.assertEqual(exp.get_export(job["job_id"])["status"], "awaiting_ui_export")

    def test_changing_file_is_rejected(self):
        job = self.prepare()
        self.write(job)
        def change_file(seconds):
            with open(job["output_path"], "ab") as stream:
                stream.write(b"changing")
        with patch.object(exp.time, "sleep", change_file):
            with self.assertRaisesRegex(ValueError, "still changing"):
                self.finish(job)

    def test_overwritten_verified_file_does_not_return_stale_success(self):
        job = self.prepare()
        self.write(job)
        self.finish(job)
        self.write(job, tone=self.tone / 2)
        with self.assertRaisesRegex(ValueError, "replaced or edited"):
            self.finish(job)

    def test_observed_model_and_pending_job_survive_new_read(self):
        job = self.prepare()
        self.write(job)
        observed = exp.ObservedExportSettings.model_validate(job["requested_settings"])
        self.finish(exp.get_export(job["job_id"]), settings=observed)
        self.assertEqual(exp.get_export(job["job_id"])["status"], "verified")


if __name__ == "__main__":
    unittest.main()
