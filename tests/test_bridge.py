import importlib.util
from pathlib import Path
import sys
import types
import unittest

# Live's proprietary modules are unavailable outside Ableton.
framework = types.ModuleType("_Framework")
surface = types.ModuleType("_Framework.ControlSurface")
surface.ControlSurface = object
sys.modules["_Framework"] = framework
sys.modules["_Framework.ControlSurface"] = surface
folder = Path(__file__).resolve().parents[1] / "remote_script" / "AbletonMCP"
spec = importlib.util.spec_from_file_location("test_live_bridge", folder / "__init__.py", submodule_search_locations=[str(folder)])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)
bridge = sys.modules["test_live_bridge.bridge"]


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.volume = types.SimpleNamespace(value=0.7)
        self.pan = types.SimpleNamespace(value=0.0)
        track = types.SimpleNamespace(mixer_device=types.SimpleNamespace(volume=self.volume, panning=self.pan))
        self.song = types.SimpleNamespace(tracks=[track], begin_undo_step=lambda: None, end_undo_step=lambda: None)
        self.control = bridge.AbletonMCP.__new__(bridge.AbletonMCP)
        self.control.song = lambda: self.song
        bridge.ALLOW_WRITES = False

    def tearDown(self):
        bridge.ALLOW_WRITES = False

    def test_read_only_blocks_changes(self):
        with self.assertRaises(ValueError):
            self.control._dispatch("set_mixer", dict(track=0, volume=0.1))
        self.assertEqual(self.volume.value, 0.7)

    def test_all_fields_validated_before_change(self):
        bridge.ALLOW_WRITES = True
        with self.assertRaises(ValueError):
            self.control._dispatch("set_mixer", dict(track=0, volume=0.1, pan=2))
        self.assertEqual(self.volume.value, 0.7)

    def test_nonfinite_bool_and_negative_indices_rejected(self):
        for value in [float("nan"), float("inf"), True, "0.2"]:
            with self.assertRaises(ValueError):
                bridge.bounded(value, 0, 1)
        with self.assertRaises(ValueError):
            self.control._dispatch("get_devices", dict(track=-1))

    def test_valid_write(self):
        bridge.ALLOW_WRITES = True
        result = self.control._dispatch("set_mixer", dict(track=0, volume=0.5, pan=-0.2))
        self.assertEqual(result, dict(track=0, volume=0.5, pan=-0.2))


if __name__ == "__main__":
    unittest.main()
