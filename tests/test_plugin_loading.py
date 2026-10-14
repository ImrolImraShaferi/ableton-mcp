import types
import unittest
from unittest.mock import patch
import test_device_loading as device_tests
from test_bridge import bridge


def item(name, children=(), uri="", loadable=False):
    return types.SimpleNamespace(name=name, children=list(children), uri=uri,
                                 is_folder=bool(children), is_loadable=loadable)


class PluginLoadingTests(unittest.TestCase):
    def setUp(self):
        device_tests.DeviceLoadingTests.setUp(self)
        self.effect = item("Test Effect", uri="vst3:effect-id", loadable=True)
        self.instrument = item("Test Synth", uri="vst3:instrument-id", loadable=True)
        vendor = item("Vendor", [self.effect, self.instrument])
        self.browser.plugins = item("Plug-Ins", [item("VST", [item("Test Effect", loadable=True)]),
                                               item("VST3", [vendor])])
        roots = self.control._dispatch("browse_plugins", {})["items"]
        self.root = roots[1]
        vendors = self.control._dispatch("browse_plugins", {"parent_id": self.root["item_id"]})
        self.leaves = self.control._dispatch("browse_plugins", {
            "parent_id": vendors["items"][0]["item_id"]})["items"]
        self.params = dict(track=0, item_id=self.leaves[0]["item_id"], expected_item_name="Test Effect",
            expected_track_name="Guitar", expected_device_names=[], plugin_kind="audio_effect")
        self.loaded_kind = 2

    def tearDown(self):
        device_tests.DeviceLoadingTests.tearDown(self)

    def load(self, browser_item):
        self.assertIs(self.song.view.selected_track, self.track)
        position = device_tests.insertion_position(self.track)
        self.track.devices.insert(position, types.SimpleNamespace(
            name=browser_item.name, type=getattr(self, "loaded_kind", 2), class_name="PluginDevice", parameters=[]))

    def dispatch(self, **changes):
        return self.control._dispatch("load_vst3_plugin", dict(self.params, **changes))

    def status(self, result):
        return self.control._dispatch("get_device_load", {"load_id": result["load_id"]})

    def midi_track(self):
        self.track.has_midi_input = True
        self.track.has_audio_input = False

    def synth(self, **changes):
        return self.dispatch(item_id=self.leaves[1]["item_id"], expected_item_name="Test Synth",
                             plugin_kind="instrument", **changes)

    def test_browse_format_and_vendor_path(self):
        self.assertEqual(self.root["plugin_format"], "VST3")
        self.assertEqual(self.leaves[0]["path"], ["VST3", "Vendor", "Test Effect"])
        self.assertEqual(self.leaves[0]["plugin_format"], "VST3")
        self.assertTrue(self.leaves[0]["loadable"])

    def test_format_inherited_without_uri_and_flat_vst3_uri_supported(self):
        self.effect.uri = ""
        vendor = self.control._dispatch("browse_plugins", {"parent_id": self.root["item_id"]})["items"][0]
        leaf = self.control._dispatch("browse_plugins", {"parent_id": vendor["item_id"]})["items"][0]
        self.assertEqual(leaf["plugin_format"], "VST3")
        self.browser.plugins.children = [self.instrument]
        leaf = self.control._dispatch("browse_plugins", {})["items"][0]
        self.assertEqual(leaf["plugin_format"], "VST3")

    def test_other_or_unknown_formats_rejected_without_loading(self):
        for uri in ("vst:old", "au:unit", "unknown"):
            with self.subTest(uri=uri):
                self.browser.plugins.children = [item("Other", uri=uri, loadable=True)]
                leaf = self.control._dispatch("browse_plugins", {})["items"][0]
                with self.assertRaisesRegex(ValueError, "identified by Live as VST3"):
                    self.dispatch(item_id=leaf["item_id"], expected_item_name="Other")
        self.assertEqual(self.steps, [])

    def test_browser_categories_cannot_be_mixed(self):
        stock = self.control._dispatch("browse_audio_effects", {})["items"][0]
        for method, parent in [("browse_plugins", stock), ("browse_audio_effects", self.root)]:
            with self.assertRaisesRegex(ValueError, "different category"):
                self.control._dispatch(method, {"parent_id": parent["item_id"]})
        leaf = self.control._dispatch("browse_audio_effects", {"parent_id": stock["item_id"]})["items"][0]
        with self.assertRaisesRegex(ValueError, "different category"):
            self.dispatch(item_id=leaf["item_id"])
        with self.assertRaisesRegex(ValueError, "different category"):
            self.control._dispatch("load_audio_effect", self.params)
        self.assertEqual(self.steps, [])

    def test_effect_load_recovery_and_verification(self):
        result = self.dispatch()
        self.assertEqual(result["status"], "pending")
        self.assertEqual(self.control._dispatch("list_device_loads", {})[0], result)
        self.assertIs(self.song.view.selected_track, self.previous)
        self.assertEqual(self.steps, ["begin", "end"])
        self.callbacks.pop()()
        status = self.status(result)
        self.assertTrue(status["verified"])
        self.assertEqual(status["plugin_format"], "VST3")
        self.assertEqual(status["device_name"], "Test Effect")
        device = self.control._dispatch("get_devices", {"track": 0})[0]
        self.assertEqual(device["device_kind"], "audio_effect")

    def test_delayed_initialization_keeps_pending_then_completes(self):
        self.browser.load_item = lambda browser_item: None
        result = self.dispatch()
        self.callbacks.pop()()
        self.assertEqual(self.status(result)["status"], "pending")
        with self.assertRaisesRegex(ValueError, "pending"):
            self.dispatch()
        self.track.devices.append(types.SimpleNamespace(name="Test Effect", type=2, class_name="PluginDevice"))
        self.callbacks.pop()()
        self.assertTrue(self.status(result)["verified"])

    def test_initialization_timeout_is_unverified_not_success(self):
        self.browser.load_item = lambda browser_item: None
        with patch.object(bridge.time, "monotonic", return_value=0):
            result = self.dispatch()
        with patch.object(bridge.time, "monotonic", return_value=16):
            self.callbacks.pop()()
        self.assertEqual(self.status(result)["status"], "unverified")
        self.assertFalse(self.status(result)["verified"])
        self.assertEqual(self.callbacks, [])

    def test_wrong_kind_never_claims_success(self):
        self.loaded_kind = 1
        result = self.dispatch()
        self.callbacks.pop()()
        self.assertEqual(self.status(result)["status"], "unverified")
        self.assertIn("device type", self.status(result)["error"])

    def test_replaced_chain_or_changed_set_never_claims_success(self):
        original = types.SimpleNamespace(name="Existing", type=2)
        self.track.devices = [original]
        result = self.dispatch(expected_device_names=["Existing"])
        self.track.devices[0] = types.SimpleNamespace(name="Replacement", type=2)
        self.callbacks.pop()()
        self.assertEqual(self.status(result)["status"], "unverified")
        self.track.devices = []
        result = self.dispatch()
        self.control.song = lambda: object()
        self.callbacks.pop()()
        self.assertEqual(self.status(result)["status"], "unverified")

    def test_instrument_on_empty_midi_track(self):
        self.midi_track()
        self.loaded_kind = 1
        result = self.synth()
        self.callbacks.pop()()
        self.assertTrue(self.status(result)["verified"])

    def test_instrument_after_midi_effect_preserves_selection(self):
        self.midi_track()
        midi_effect = types.SimpleNamespace(name="Arpeggiator", type=3)
        self.track.devices = [midi_effect]
        self.track.view.selected_device = midi_effect
        self.loaded_kind = 1
        result = self.synth(expected_device_names=["Arpeggiator"])
        self.callbacks.pop()()
        self.assertTrue(self.status(result)["verified"])
        self.assertIs(self.track.devices[0], midi_effect)
        self.assertIs(self.track.view.selected_device, midi_effect)

    def test_instrument_incompatible_tracks_or_existing_devices_rejected(self):
        with self.assertRaisesRegex(ValueError, "MIDI track"):
            self.synth()
        self.midi_track()
        for kind in (0, 1, 2):
            self.track.devices = [types.SimpleNamespace(name="Existing", type=kind)]
            with self.assertRaisesRegex(ValueError, "will not be replaced"):
                self.synth(expected_device_names=["Existing"])
        self.assertEqual(self.steps, [])

    def test_audio_effect_requires_audio_signal_on_midi_track(self):
        self.midi_track()
        with self.assertRaisesRegex(ValueError, "with an instrument"):
            self.dispatch()
        synth = types.SimpleNamespace(name="Existing Synth", type=1)
        self.track.devices = [synth]
        result = self.dispatch(expected_device_names=["Existing Synth"])
        self.callbacks.pop()()
        self.assertTrue(self.status(result)["verified"])

    def test_stale_targets_invalid_kind_readonly_recording_hotswap_and_capture(self):
        for changes in (dict(track=-1), dict(expected_track_name="Other"), dict(item_id="expired"),
                        dict(expected_device_names=["Missing"]), dict(expected_item_name="Wrong"),
                        dict(plugin_kind="midi_effect")):
            with self.assertRaises(ValueError):
                self.dispatch(**changes)
        bridge.ALLOW_WRITES = False
        with self.assertRaisesRegex(ValueError, "read-only"):
            self.dispatch()
        bridge.ALLOW_WRITES = True
        for attr in ("record_mode", "session_record"):
            setattr(self.song, attr, True)
            with self.assertRaisesRegex(ValueError, "recording"):
                self.dispatch()
            setattr(self.song, attr, False)
        self.browser.hotswap_target = object()
        with self.assertRaisesRegex(ValueError, "hot-swap"):
            self.dispatch()
        self.browser.hotswap_target = None
        self.control._captures = {"id": {"public": {"status": "recording"}}}
        with self.assertRaisesRegex(ValueError, "Capture active"):
            self.dispatch()
        self.assertEqual(self.control._dispatch("list_device_loads", {}), [])
        self.assertEqual(self.steps, [])

    def test_browser_unavailable_pagination_and_expired_parent(self):
        self.browser.plugins.children = [self.effect] * 51
        first = self.control._dispatch("browse_plugins", {})
        self.assertEqual(first["next_offset"], 50)
        self.assertEqual(len(self.control._dispatch("browse_plugins", {"offset": 50})["items"]), 1)
        for params in ({"offset": True}, {"offset": -1}, {"offset": 52}, {"parent_id": "expired"}):
            with self.assertRaises(ValueError):
                self.control._dispatch("browse_plugins", params)
        del self.browser.plugins
        with self.assertRaisesRegex(ValueError, "does not expose"):
            self.control._dispatch("browse_plugins", {})

    def test_load_exception_restores_view_and_records_recoverable_failure(self):
        def reject(browser_item):
            raise RuntimeError("Plug-in unavailable")
        self.browser.load_item = reject
        result = self.dispatch()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.control._dispatch("list_device_loads", {})[0]["status"], "failed")
        self.assertIs(self.song.view.selected_track, self.previous)
        self.assertEqual(self.steps, ["begin", "end"])

    def test_selection_restore_failure_still_restores_track_and_verifies_chain(self):
        original = types.SimpleNamespace(name="Original Effect", type=2)
        self.track.devices = [original]
        self.track.view.selected_device = original
        def select(device):
            if len(self.track.devices) > 1:
                raise RuntimeError("Selection rejected")
            self.track.view.selected_device = device
        self.song.view.select_device = select
        result = self.dispatch(expected_device_names=["Original Effect"])
        self.assertFalse(result["view_restored"])
        self.assertEqual(result["restoration_errors"], ["Selection rejected"])
        self.assertIs(self.song.view.selected_track, self.previous)
        self.assertEqual(self.steps, ["begin", "end"])
        self.callbacks.pop()()
        self.assertTrue(self.status(result)["verified"])

    def test_begin_undo_failure_does_not_leave_pending_job(self):
        def reject():
            raise RuntimeError("Undo unavailable")
        self.song.begin_undo_step = reject
        result = self.dispatch()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.callbacks, [])
        self.assertEqual(self.track.devices, [])
        self.assertEqual(self.steps, [])

    def test_duplicate_names_keep_distinct_vst2_and_vst3_provenance(self):
        self.effect.uri = ""
        roots = self.control._dispatch("browse_plugins", {})["items"]
        old = self.control._dispatch("browse_plugins", {"parent_id": roots[0]["item_id"]})["items"][0]
        self.assertEqual(old["plugin_format"], "VST2")
        with self.assertRaisesRegex(ValueError, "identified by Live as VST3"):
            self.dispatch(item_id=old["item_id"], expected_item_name=old["name"])
        result = self.dispatch()
        self.callbacks.pop()()
        self.assertTrue(self.status(result)["verified"])

    def test_vst3_start_middle_and_explicit_end(self):
        for position in (0, 1, 2):
            with self.subTest(position=position):
                original = [types.SimpleNamespace(name="Effect One", type=2),
                            types.SimpleNamespace(name="Effect Two", type=2)]
                self.track.devices = original[:]
                self.track.view.selected_device = original[0]
                result = self.dispatch(position=position, expected_device_names=["Effect One", "Effect Two"])
                self.callbacks.pop()()
                self.assertTrue(self.status(result)["verified"])
                self.assertEqual(self.status(result)["device"], position)
                self.assertEqual(self.track.devices[position].name, "Test Effect")
                remaining = self.track.devices[:position] + self.track.devices[position + 1:]
                self.assertTrue(all(a is b for a, b in zip(remaining, original)))
                self.assertIs(self.track.view.selected_device, original[0])

    def test_vst3_invalid_position_has_no_side_effects(self):
        for position in (-1, 1, True, 0.5, "0"):
            with self.subTest(position=position), self.assertRaisesRegex(ValueError, "Position"):
                self.dispatch(position=position)
        self.assertEqual(self.steps, [])

    def test_effect_cannot_precede_instrument_but_can_precede_audio_effects(self):
        self.midi_track()
        midi = types.SimpleNamespace(name="Arpeggiator", type=3)
        synth = types.SimpleNamespace(name="Synth", type=1)
        effect = types.SimpleNamespace(name="Reverb", type=2)
        self.track.devices = [midi, synth, effect]
        for position in (0, 1):
            with self.assertRaisesRegex(ValueError, "after the instrument"):
                self.dispatch(position=position, expected_device_names=["Arpeggiator", "Synth", "Reverb"])
        self.assertEqual(self.steps, [])
        result = self.dispatch(position=2, expected_device_names=["Arpeggiator", "Synth", "Reverb"])
        self.callbacks.pop()()
        self.assertTrue(self.status(result)["verified"])
        self.assertEqual([d.name for d in self.track.devices],
                         ["Arpeggiator", "Synth", "Test Effect", "Reverb"])

    def test_instrument_cannot_precede_midi_effects(self):
        self.midi_track()
        self.loaded_kind = 1
        self.track.devices = [types.SimpleNamespace(name="Arpeggiator", type=3)]
        with self.assertRaisesRegex(ValueError, "after all MIDI effects"):
            self.synth(position=0, expected_device_names=["Arpeggiator"])
        self.assertEqual(self.steps, [])
        result = self.synth(position=1, expected_device_names=["Arpeggiator"])
        self.callbacks.pop()()
        self.assertTrue(self.status(result)["verified"])

    def test_slow_initialization_at_start_and_wrong_position_readback(self):
        original = types.SimpleNamespace(name="Existing", type=2)
        self.track.devices = [original]
        self.browser.load_item = lambda browser_item: None
        result = self.dispatch(position=0, expected_device_names=["Existing"])
        self.callbacks.pop()()
        self.assertEqual(self.status(result)["status"], "pending")
        self.track.devices.insert(0, types.SimpleNamespace(name="Test Effect", type=2, class_name="PluginDevice"))
        self.callbacks.pop()()
        self.assertTrue(self.status(result)["verified"])
        self.track.devices = [original]
        result = self.dispatch(position=0, expected_device_names=["Existing"])
        self.track.devices.append(types.SimpleNamespace(name="Test Effect", type=2, class_name="PluginDevice"))
        self.callbacks.pop()()
        self.assertEqual(self.status(result)["status"], "unverified")
