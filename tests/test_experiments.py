import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf
from pydantic import ValidationError
from ableton_mcp import experiments as exp


class ExperimentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.override = patch.object(exp, "REPORTS", self.root / "reports")
        self.override.start()
        self.original = self.root / "original.wav"
        self.revised = self.root / "revised.wav"
        tone = 0.5 * np.sin(2 * np.pi * 1000 * np.arange(48000) / 48000)
        sf.write(self.original, tone, 48000, subtype="FLOAT")
        sf.write(self.revised, tone / 2, 48000, subtype="FLOAT")
        self.change = dict(target="Vocal / EQ / Gain", original_value=0.4,
            proposed_value=0.3, applied_value=0.3, rationale="Try a small reduction", status="applied")

    def tearDown(self):
        self.override.stop()
        self.temp.cleanup()

    def test_save_comparison_records_changes_and_generated_measurements(self):
        saved = exp.save_experiment("First pass", str(self.original), str(self.revised),
            changes=[self.change], mixer_snapshot_id="a" * 32, device_snapshot_id="b" * 32)
        report = exp.read_experiment(saved["report_id"])
        self.assertEqual(report["changes"][0]["original_value"], 0.4)
        self.assertEqual(report["decision"], "pending")
        self.assertAlmostEqual(report["comparison"]["deltas"]["sample_peak_dbfs"], -6.0206, places=3)
        self.assertEqual(report["mixer_snapshot_id"], "a" * 32)
        markdown = Path(saved["markdown_path"]).read_text(encoding="utf-8")
        self.assertIn("Frequency bands", markdown)
        self.assertIn("Gain-adjusted", markdown)
        self.assertIn("expire on Live/bridge restart", markdown)
        self.assertTrue(report["created_at"].endswith("+08:00"))
        self.assertEqual(len(exp.list_experiments()["reports"]), 1)

    def test_baseline_update_preserves_measurements_after_sources_removed(self):
        saved = exp.save_experiment("Proposal", str(self.original), changes=[self.change])
        before = exp.read_experiment(saved["report_id"])
        self.original.unlink()
        exp.update_experiment(saved["report_id"], "The vocal is clearer at matched loudness.", "kept")
        after = exp.read_experiment(saved["report_id"])
        self.assertEqual(before["baseline"], after["baseline"])
        self.assertEqual(before["changes"], after["changes"])
        self.assertEqual(after["decision"], "kept")
        self.assertIsNone(after["comparison"])
        self.assertIn("vocal is clearer", Path(saved["markdown_path"]).read_text(encoding="utf-8"))
        self.assertEqual(list((self.root / "reports").glob("*/*.tmp")), [])

    def test_validation_prevents_invalid_report_paths_and_values(self):
        for report_id in ["../token", "", "not-a-uuid"]:
            with self.assertRaises(ValueError):
                exp.read_experiment(report_id)
        with self.assertRaises(ValueError):
            exp.save_experiment("", str(self.original))
        with self.assertRaises(ValueError):
            exp.save_experiment("Test", str(self.original), decision="excellent")
        with self.assertRaises(ValueError):
            exp.save_experiment("Test", str(self.original), device_snapshot_id="invalid")
        with self.assertRaises(ValidationError):
            exp.save_experiment("Test", str(self.original), changes=[dict(self.change, original_value=float("nan"))])
        self.assertFalse((self.root / "reports").exists())

    def test_unique_reports_and_pydantic_records(self):
        record = exp.MixingChange.model_validate(self.change)
        first = exp.save_experiment("Same title", str(self.original), changes=[record])
        second = exp.save_experiment("Same title", str(self.original))
        self.assertNotEqual(first["report_id"], second["report_id"])
        self.assertEqual(len(exp.list_experiments()["reports"]), 2)
        json.dumps(exp.read_experiment(first["report_id"]), allow_nan=False)
