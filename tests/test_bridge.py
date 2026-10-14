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
        self.control._device_snapshots = {}
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


class TransportSceneTests(unittest.TestCase):
    def setUp(self):
        self.steps = []
        self.song = types.SimpleNamespace(
            start_time=8.0, current_song_time=12.0, record_mode=False,
            session_record=False, is_playing=False, scenes=[types.SimpleNamespace(name="Original")],
            tracks=[], tempo=120, signature_numerator=4, signature_denominator=4,
            back_to_arranger=False, metronome=False, count_in_duration=0, loop=False,
            song_length=100,
            begin_undo_step=lambda: self.steps.append("begin"),
            end_undo_step=lambda: self.steps.append("end"))
        def create_scene(index):
            self.song.scenes.insert(index, types.SimpleNamespace(name="New Scene"))
        self.song.create_scene = create_scene
        self.control = bridge.AbletonMCP.__new__(bridge.AbletonMCP)
        self.control.song = lambda: self.song
        bridge.ALLOW_WRITES = True

    def tearDown(self):
        bridge.ALLOW_WRITES = False

    def test_start_marker_changes_without_seeking_or_playing(self):
        result = self.control._dispatch("set_playback_start", dict(start_beats=32))
        self.assertEqual(result, dict(previous_start_beats=8, start_beats=32))
        self.assertEqual(self.song.current_song_time, 12)
        self.assertFalse(self.song.is_playing)
        self.assertEqual(self.steps, ["begin", "end"])
        project = self.control._dispatch("get_project", {})
        self.assertEqual(project["transport"]["start_beats"], 32)
        self.assertEqual(project["time_signature"], dict(numerator=4, denominator=4))
        self.assertEqual(project["scenes"], [dict(index=0, name="Original")])

    def test_invalid_start_never_mutates(self):
        for value in [-1, True, "32", None, float("nan"), float("inf")]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.control._dispatch("set_playback_start", dict(start_beats=value))
        self.assertEqual(self.song.start_time, 8)
        self.assertEqual(self.steps, [])

    def test_append_and_insert_preserve_existing_scenes(self):
        original = self.song.scenes[0]
        result = self.control._dispatch("create_session_scene", dict(name="Chorus"))
        self.assertEqual(result, dict(index=1, name="Chorus", scene_count=2))
        result = self.control._dispatch("create_session_scene", dict(index=0))
        self.assertEqual(result, dict(index=0, name="New Scene", scene_count=3))
        self.assertIs(self.song.scenes[1], original)
        self.assertEqual(self.steps, ["begin", "end", "begin", "end"])

    def test_invalid_scene_inputs_never_mutate(self):
        for params in [dict(index=-1), dict(index=2), dict(index=True), dict(index=0.5),
                       dict(name=""), dict(name="  "), dict(name=12), dict(name="x" * 257)]:
            with self.subTest(params=params), self.assertRaises(ValueError):
                self.control._dispatch("create_session_scene", params)
        self.assertEqual(len(self.song.scenes), 1)
        self.assertEqual(self.steps, [])

    def test_write_recording_and_capture_guards(self):
        requests = [("set_playback_start", dict(start_beats=32)),
                    ("create_session_scene", dict(name="Verse"))]
        bridge.ALLOW_WRITES = False
        for method, params in requests:
            with self.assertRaisesRegex(ValueError, "read-only"):
                self.control._dispatch(method, params)
        bridge.ALLOW_WRITES = True
        for attr in ["record_mode", "session_record"]:
            setattr(self.song, attr, True)
            for method, params in requests:
                with self.assertRaisesRegex(ValueError, "recording"):
                    self.control._dispatch(method, params)
            setattr(self.song, attr, False)
        for status in ["recording", "finalizing", "cleaning"]:
            self.control._captures = {"id": {"public": {"status": status}}}
            for method, params in requests:
                with self.assertRaisesRegex(ValueError, "Capture active"):
                    self.control._dispatch(method, params)
        self.assertEqual(self.steps, [])

    def test_failed_scene_readback_closes_undo_and_warns_against_retry(self):
        self.song.create_scene = lambda index: None
        with self.assertRaisesRegex(RuntimeError, "inspect get_project before retrying"):
            self.control._dispatch("create_session_scene", {})
        self.assertEqual(self.steps, ["begin", "end"])

    def test_marker_readback_failure_restores_previous_value(self):
        class ClampingSong:
            record_mode = session_record = False
            def __init__(self):
                self._start_time = 8
            @property
            def start_time(self):
                return self._start_time
            @start_time.setter
            def start_time(self, value):
                self._start_time = min(value, 16)
        song = ClampingSong()
        song.begin_undo_step = self.song.begin_undo_step
        song.end_undo_step = self.song.end_undo_step
        self.song = song
        with self.assertRaisesRegex(RuntimeError, "original values restored"):
            self.control._dispatch("set_playback_start", dict(start_beats=32))
        self.assertEqual(song.start_time, 8)
        self.assertEqual(self.steps, ["begin", "end"])


class ExtendedTests(ValidationTests):
    def setUp(self):
        ValidationTests.setUp(self)
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
        self.song.return_tracks[0].devices = []
        self.song.master_track.devices = []
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


class DeviceSnapshotTests(unittest.TestCase):
    def setUp(self):
        ExtendedTests.setUp(self)
        class LiveObject(types.SimpleNamespace):
            def __eq__(self, other):
                return self is other
            def __ne__(self, other):
                return self is not other
        self.LiveObject = LiveObject
        self.song = LiveObject(**vars(self.song))
        self.track = LiveObject(**vars(self.track))
        self.song.tracks = [self.track]
        self.parameter = LiveObject(**vars(self.parameter))
        self.device = LiveObject(name="EQ", parameters=[self.parameter])
        self.track.devices = [self.device]

    def tearDown(self):
        bridge.ALLOW_WRITES = False

    def snapshot(self, **params):
        return self.control._dispatch("create_device_snapshot", params)["snapshot_id"]

    def restore(self, snapshot_id):
        return self.control._dispatch("restore_device_snapshot", dict(snapshot_id=snapshot_id))

    def test_capture_restore_and_repeat_noop(self):
        snapshot_id = self.snapshot()
        self.parameter.value = 0.8
        self.volume.value = 0.2
        bridge.ALLOW_WRITES = True
        result = self.restore(snapshot_id)
        self.assertEqual(self.parameter.value, 0.4)
        self.assertEqual(self.volume.value, 0.2)
        self.assertTrue(result["verified"])
        self.assertEqual(result["changed_parameter_count"], 1)
        self.assertEqual(self.steps, ["begin", "end"])
        self.assertEqual(self.restore(snapshot_id)["changed_parameter_count"], 0)
        self.assertEqual(self.steps, ["begin", "end"])

    def test_all_tracks_and_single_track_scope(self):
        other = self.LiveObject(**vars(self.parameter))
        other.value = 0.5
        self.song.return_tracks[0].devices = [self.LiveObject(name="Return EQ", parameters=[other])]
        master = self.LiveObject(**vars(self.parameter))
        self.song.master_track.devices = [self.LiveObject(name="Limiter", parameters=[master])]
        all_id = self.snapshot()
        one_id = self.snapshot(track=0)
        listing = self.control._dispatch("list_device_snapshots", {})
        self.assertEqual(listing[0]["captured_parameter_count"], 3)
        bridge.ALLOW_WRITES = True
        self.parameter.value = other.value = master.value = 0.9
        self.restore(one_id)
        self.assertEqual(other.value, 0.9)
        self.restore(all_id)
        self.assertEqual(other.value, 0.5)
        self.assertEqual(master.value, 0.4)

    def test_disabled_capture_is_skipped_and_reported(self):
        self.parameter.is_enabled = False
        snapshot_id = self.snapshot()
        self.parameter.is_enabled = True
        self.parameter.value = 0.9
        bridge.ALLOW_WRITES = True
        result = self.restore(snapshot_id)
        self.assertEqual(result["skipped_disabled_parameter_count"], 1)
        self.assertEqual(self.parameter.value, 0.9)
        self.assertEqual(self.steps, [])

    def test_disabled_changed_parameter_blocks_entire_restore(self):
        other = self.LiveObject(**vars(self.parameter))
        self.device.parameters.append(other)
        snapshot_id = self.snapshot()
        self.parameter.value = other.value = 0.9
        other.is_enabled = False
        bridge.ALLOW_WRITES = True
        with self.assertRaises(ValueError):
            self.restore(snapshot_id)
        self.assertEqual(self.parameter.value, 0.9)
        self.assertEqual(self.steps, [])

    def test_replaced_same_named_device_rejected(self):
        snapshot_id = self.snapshot()
        self.track.devices = [self.LiveObject(**vars(self.device))]
        bridge.ALLOW_WRITES = True
        with self.assertRaisesRegex(ValueError, "Device topology"):
            self.restore(snapshot_id)
        self.assertEqual(self.steps, [])

    def test_replaced_parameter_and_changed_metadata_rejected(self):
        snapshot_id = self.snapshot()
        self.device.parameters = [self.LiveObject(**vars(self.parameter))]
        bridge.ALLOW_WRITES = True
        with self.assertRaisesRegex(ValueError, "Parameter topology"):
            self.restore(snapshot_id)
        self.device.parameters = [self.parameter]
        for attr, value in [("name", "Other"), ("max", 2), ("is_quantized", True)]:
            previous = getattr(self.parameter, attr)
            setattr(self.parameter, attr, value)
            with self.assertRaises(ValueError):
                self.restore(snapshot_id)
            setattr(self.parameter, attr, previous)
        self.assertEqual(self.steps, [])

    def test_set_and_track_changes_rejected(self):
        snapshot_id = self.snapshot()
        bridge.ALLOW_WRITES = True
        original = self.song
        self.song = self.LiveObject(**vars(original))
        with self.assertRaises(ValueError):
            self.restore(snapshot_id)
        self.assertEqual(self.control._dispatch("list_device_snapshots", {}), [])
        self.song = original
        self.song.tracks = [self.LiveObject(**vars(self.track))]
        with self.assertRaises(ValueError):
            self.restore(snapshot_id)

    def test_read_only_limit_deletion_and_bad_index(self):
        snapshot_id = self.snapshot()
        with self.assertRaises(ValueError):
            self.restore(snapshot_id)
        for _ in range(19):
            self.snapshot()
        with self.assertRaises(ValueError):
            self.snapshot()
        self.control._dispatch("delete_device_snapshot", dict(snapshot_id=snapshot_id))
        self.assertEqual(len(self.control._dispatch("list_device_snapshots", {})), 19)
        bridge.ALLOW_WRITES = True
        with self.assertRaises(ValueError):
            self.restore(snapshot_id)
        with self.assertRaises(ValueError):
            self.snapshot(track=-1)

    def test_parameter_slot_limit(self):
        self.device.parameters = [self.parameter] * 10001
        with self.assertRaises(ValueError):
            self.snapshot()
        self.assertEqual(self.control._device_snapshots, {})

    def test_readback_mismatch_rolls_back(self):
        class ClampingParameter:
            def __init__(self):
                self._value = 0.5
            @property
            def value(self):
                return self._value
            @value.setter
            def value(self, value):
                self._value = min(value, 0.5)
        parameter = ClampingParameter()
        with self.assertRaisesRegex(RuntimeError, "original values restored"):
            self.control._apply(self.song, [(parameter, "value", 0.9)], verify=True)
        self.assertEqual(parameter.value, 0.5)
        self.assertEqual(self.steps, ["begin", "end"])

    def test_restore_checks_indirect_changes_to_unchanged_parameters(self):
        other = self.LiveObject(**vars(self.parameter))
        class CoupledParameter:
            name, min, max, is_quantized, is_enabled = "Coupled", 0, 1, False, True
            def __init__(self):
                self._value = 0.4
            @property
            def value(self):
                return self._value
            @value.setter
            def value(self, value):
                self._value = value
                if value == 0.4:
                    other.value = 0.7
        coupled = CoupledParameter()
        self.device.parameters = [coupled, other]
        snapshot_id = self.snapshot()
        coupled._value = 0.9
        bridge.ALLOW_WRITES = True
        with self.assertRaisesRegex(RuntimeError, "readback"):
            self.restore(snapshot_id)
        self.assertEqual(coupled.value, 0.9)
        self.assertEqual(other.value, 0.4)

    def test_rollback_readback_failure_is_reported(self):
        class RejectingRollback:
            def __init__(self):
                self._value = 0.8
            @property
            def value(self):
                return self._value
            @value.setter
            def value(self, value):
                self._value = min(value, 0.5)
        parameter = RejectingRollback()
        with self.assertRaisesRegex(RuntimeError, "rollback incomplete"):
            self.control._apply(self.song, [(parameter, "value", 0.9)], verify=True)


if __name__ == "__main__":
    unittest.main()
