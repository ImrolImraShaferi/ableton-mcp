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
        self.control._snapshots = {}
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


class ExtendedTests(ValidationTests):
    def setUp(self):
        super().setUp()
        self.steps = []
        self.song.begin_undo_step = lambda: self.steps.append("begin")
        self.song.end_undo_step = lambda: self.steps.append("end")
        def parameter(name, value, low=0, high=1):
            return types.SimpleNamespace(name=name, value=value, min=low, max=high,
                                         is_enabled=True, is_quantized=False)
        self.volume = parameter("Volume", 0.7)
        self.pan = parameter("Pan", 0, -1, 1)
        self.send = parameter("Send A", 0.3)
        self.track = self.song.tracks[0]
        self.track.name = "Vocal"
        self.track.mute = False
        self.track.solo = False
        self.track.mixer_device = types.SimpleNamespace(volume=self.volume, panning=self.pan, sends=[self.send])
        self.parameter = parameter("Gain", 0.4)
        self.track.devices = [types.SimpleNamespace(name="EQ", parameters=[self.parameter])]
        def extra_track(name):
            return types.SimpleNamespace(name=name, mute=False, solo=False,
                mixer_device=types.SimpleNamespace(volume=parameter("Volume", 0.8),
                    panning=parameter("Pan", 0, -1, 1), sends=[]))
        self.song.return_tracks = [extra_track("Reverb")]
        self.song.master_track = extra_track("Master")
        self.request = dict(track=0, device=0, parameter=0, value=0.6,
            expected_track_name="Vocal", expected_device_name="EQ",
            expected_parameter_name="Gain", expected_value=0.4)

    def test_parameter_write_and_undo(self):
        bridge.ALLOW_WRITES = True
        result = self.control._dispatch("set_device_parameter", self.request)
        self.assertEqual(result["previous_value"], 0.4)
        self.assertEqual(self.parameter.value, 0.6)
        self.assertEqual(self.steps, ["begin", "end"])

    def test_parameter_guards(self):
        bridge.ALLOW_WRITES = True
        for field, value in [("expected_track_name", "Other"), ("expected_device_name", "Other"),
                             ("expected_parameter_name", "Other"), ("expected_value", 0.2),
                             ("value", 5), ("device", -1), ("parameter", True)]:
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    self.control._dispatch("set_device_parameter", dict(self.request, **{field: value}))
        self.assertEqual(self.parameter.value, 0.4)
        self.assertEqual(self.steps, [])

    def test_parameter_read_only_disabled_and_quantized(self):
        with self.assertRaises(ValueError):
            self.control._dispatch("set_device_parameter", self.request)
        bridge.ALLOW_WRITES = True
        self.parameter.is_enabled = False
        with self.assertRaises(ValueError):
            self.control._dispatch("set_device_parameter", self.request)
        self.parameter.is_enabled = True
        self.parameter.is_quantized = True
        with self.assertRaises(ValueError):
            self.control._dispatch("set_device_parameter", self.request)
        self.control._dispatch("set_device_parameter", dict(self.request, value=1))
        self.assertEqual(self.parameter.value, 1)

    def snapshot(self):
        return self.control._dispatch("create_mixer_snapshot", {})["snapshot_id"]

    def test_snapshot_restores_all_mixer_fields(self):
        snapshot_id = self.snapshot()
        self.volume.value = 0.1
        self.pan.value = -0.2
        self.send.value = 0.9
        self.track.mute = self.track.solo = True
        self.song.return_tracks[0].mixer_device.volume.value = 0.2
        self.song.master_track.mixer_device.volume.value = 0.3
        self.parameter.value = 0.9
        bridge.ALLOW_WRITES = True
        self.control._dispatch("restore_mixer_snapshot", dict(snapshot_id=snapshot_id))
        self.assertEqual((self.volume.value, self.pan.value, self.send.value), (0.7, 0, 0.3))
        self.assertFalse(self.track.mute)
        self.assertFalse(self.track.solo)
        self.assertEqual(self.song.return_tracks[0].mixer_device.volume.value, 0.8)
        self.assertEqual(self.song.master_track.mixer_device.volume.value, 0.8)
        self.assertEqual(self.parameter.value, 0.9)
        self.assertEqual(self.steps, ["begin", "end"])

    def test_snapshot_rejects_track_replacement(self):
        snapshot_id = self.snapshot()
        self.song.tracks = [types.SimpleNamespace(name="Vocal")]
        bridge.ALLOW_WRITES = True
        with self.assertRaises(ValueError):
            self.control._dispatch("restore_mixer_snapshot", dict(snapshot_id=snapshot_id))
        self.assertEqual(self.steps, [])

    def test_snapshot_rejects_send_changes_and_validates_before_writes(self):
        snapshot_id = self.snapshot()
        bridge.ALLOW_WRITES = True
        self.volume.value = 0.1
        self.song.master_track.mixer_device.volume.is_enabled = False
        with self.assertRaises(ValueError):
            self.control._dispatch("restore_mixer_snapshot", dict(snapshot_id=snapshot_id))
        self.assertEqual(self.volume.value, 0.1)
        self.song.master_track.mixer_device.volume.is_enabled = True
        self.track.mixer_device.sends = []
        with self.assertRaises(ValueError):
            self.control._dispatch("restore_mixer_snapshot", dict(snapshot_id=snapshot_id))
        self.assertEqual(self.steps, [])

    def test_snapshot_read_only_limit_and_delete(self):
        snapshot_id = self.snapshot()
        with self.assertRaises(ValueError):
            self.control._dispatch("restore_mixer_snapshot", dict(snapshot_id=snapshot_id))
        for _ in range(19):
            self.snapshot()
        with self.assertRaises(ValueError):
            self.snapshot()
        self.control._dispatch("delete_mixer_snapshot", dict(snapshot_id=snapshot_id))
        self.snapshot()
        bridge.ALLOW_WRITES = True
        with self.assertRaises(ValueError):
            self.control._dispatch("restore_mixer_snapshot", dict(snapshot_id=snapshot_id))

    def test_runtime_failure_rolls_back_and_closes_undo(self):
        class RejectingParameter:
            value = property(lambda self: 0, lambda self, value: (_ for _ in ()).throw(RuntimeError("rejected")) if value else None)
        with self.assertRaisesRegex(RuntimeError, "original values restored"):
            self.control._apply(self.song, [(self.volume, "value", 0.1), (RejectingParameter(), "value", 1)])
        self.assertEqual(self.volume.value, 0.7)
        self.assertEqual(self.steps, ["begin", "end"])


if __name__ == "__main__":
    unittest.main()
