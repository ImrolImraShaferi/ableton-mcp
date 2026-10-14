import sys
import types
import unittest
from test_bridge import bridge


def insertion_position(track):
    if not track.devices:
        if track.view.device_insert_mode != "default":
            raise AssertionError("Empty track must use the default insertion mode")
        return 0
    selected = next(i for i, device in enumerate(track.devices)
                    if device is track.view.selected_device)
    if track.view.device_insert_mode == "selected_left":
        return selected
    if track.view.device_insert_mode == "selected_right":
        return selected + 1
    raise AssertionError("Expected an explicit insertion side on a nonempty track")


class DeviceLoadingTests(unittest.TestCase):
    def setUp(self):
        self.control = bridge.AbletonMCP.__new__(bridge.AbletonMCP)
        self.control._browser_items = {}
        self.control._device_loads = {}
        self.effect = types.SimpleNamespace(name="EQ Eight", is_loadable=True, is_folder=False, children=[])
        folder = types.SimpleNamespace(name="EQ", is_loadable=False, is_folder=True, children=[self.effect])
        self.track = types.SimpleNamespace(name="Guitar", devices=[], has_audio_input=True, has_midi_input=False,
                                          view=types.SimpleNamespace(device_insert_mode="default", selected_device=None))
        self.previous = object()
        self.steps = []
        self.song = types.SimpleNamespace(tracks=[self.track], record_mode=False, session_record=False,
            view=types.SimpleNamespace(selected_track=self.previous),
            begin_undo_step=lambda: self.steps.append("begin"),
            end_undo_step=lambda: self.steps.append("end"))
        self.control.song = lambda: self.song
        self.song.view.select_device = lambda device: setattr(self.track.view, "selected_device", device)
        self.browser = types.SimpleNamespace(audio_effects=types.SimpleNamespace(children=[folder]),
                                            hotswap_target=None, load_item=self.load)
        self.control.application = lambda: types.SimpleNamespace(browser=self.browser)
        self.callbacks = []
        self.control.schedule_message = lambda ticks, callback: self.callbacks.append(callback)
        sys.modules["Live"] = types.SimpleNamespace(Track=types.SimpleNamespace(
            DeviceInsertMode=types.SimpleNamespace(default="default", selected_right="selected_right",
                                                   selected_left="selected_left")))
        bridge.ALLOW_WRITES = True
        parent = self.control._dispatch("browse_audio_effects", {})["items"][0]
        leaf = self.control._dispatch("browse_audio_effects", {"parent_id": parent["item_id"]})["items"][0]
        self.params = dict(track=0, item_id=leaf["item_id"], expected_item_name="EQ Eight",
                           expected_track_name="Guitar", expected_device_names=[])

    def tearDown(self):
        bridge.ALLOW_WRITES = False
        sys.modules.pop("Live", None)

    def load(self, item):
        self.assertIs(self.song.view.selected_track, self.track)
        position = insertion_position(self.track)
        self.track.devices.insert(position, types.SimpleNamespace(name=item.name, class_name="Eq8", type=2))

    def dispatch(self, **changes):
        return self.control._dispatch("load_audio_effect", dict(self.params, **changes))

    def test_append_and_delayed_verification(self):
        result = self.dispatch()
        self.assertEqual(result["status"], "pending")
        self.assertIs(self.song.view.selected_track, self.previous)
        self.assertEqual(self.track.view.device_insert_mode, "default")
        self.assertEqual(self.steps, ["begin", "end"])
        self.callbacks.pop()()
        status = self.control._dispatch("get_device_load", {"load_id": result["load_id"]})
        self.assertTrue(status["verified"])
        self.assertEqual(status["device"], 0)

    def test_invalid_targets_and_read_only_do_not_load(self):
        for changes in [dict(track=-1), dict(expected_track_name="Other"),
                        dict(expected_device_names=["Other"]), dict(item_id="bad"),
                        dict(expected_item_name="Compressor")]:
            with self.assertRaises(ValueError):
                self.dispatch(**changes)
        bridge.ALLOW_WRITES = False
        with self.assertRaises(ValueError):
            self.dispatch()
        self.assertEqual(self.steps, [])

    def test_hot_swap_rejected(self):
        self.browser.hotswap_target = object()
        with self.assertRaisesRegex(ValueError, "hot-swap"):
            self.dispatch()
        self.assertEqual(self.steps, [])

    def test_pending_blocks_second_load(self):
        self.dispatch()
        with self.assertRaisesRegex(ValueError, "pending"):
            self.dispatch(expected_device_names=["EQ Eight"])
        self.assertEqual(len(self.track.devices), 1)

    def test_no_addition_not_claimed_success(self):
        self.browser.load_item = lambda item: None
        result = self.dispatch()
        self.callbacks.pop()()
        status = self.control._device_loads[result["load_id"]]
        self.assertEqual(status["status"], "unverified")
        self.assertFalse(status["verified"])

    def test_replacement_not_claimed_success(self):
        original = types.SimpleNamespace(name="Compressor")
        self.track.devices = [original]
        result = self.dispatch(expected_device_names=["Compressor"])
        self.track.devices[0] = types.SimpleNamespace(name="Compressor")
        # SimpleNamespace compares by value; change metadata to emulate identity change.
        self.track.devices[0].replaced = True
        self.callbacks.pop()()
        self.assertEqual(self.control._device_loads[result["load_id"]]["status"], "unverified")

    def test_append_after_tuner_restores_previous_device_selection(self):
        amp = types.SimpleNamespace(name="SHB-1")
        tuner = types.SimpleNamespace(name="Tuner")
        self.track.devices = [amp, tuner]
        self.track.view.selected_device = amp
        result = self.dispatch(expected_device_names=["SHB-1", "Tuner"])
        self.assertIs(self.track.view.selected_device, amp)
        self.callbacks.pop()()
        self.assertEqual([d.name for d in self.track.devices], ["SHB-1", "Tuner", "EQ Eight"])
        self.assertTrue(self.control._device_loads[result["load_id"]]["verified"])

    def test_load_exception_restores_view_and_closes_undo(self):
        def reject(item):
            raise RuntimeError("Unavailable")
        self.browser.load_item = reject
        result = self.dispatch()
        self.assertEqual(result["status"], "failed")
        self.assertIs(self.song.view.selected_track, self.previous)
        self.assertEqual(self.steps, ["begin", "end"])

    def test_pagination_and_expired_parent(self):
        self.browser.audio_effects.children = [self.effect] * 51
        first = self.control._dispatch("browse_audio_effects", {})
        self.assertEqual(len(first["items"]), 50)
        self.assertEqual(first["next_offset"], 50)
        self.assertEqual(len(self.control._dispatch("browse_audio_effects", {"offset": 50})["items"]), 1)
        for params in [{"offset": True}, {"offset": -1}, {"parent_id": "expired"}]:
            with self.assertRaises(ValueError):
                self.control._dispatch("browse_audio_effects", params)

    def test_start_middle_and_explicit_end_preserve_original_order(self):
        for position in (0, 1, 2):
            with self.subTest(position=position):
                original = [types.SimpleNamespace(name="Amp", type=2),
                            types.SimpleNamespace(name="Tuner", type=2)]
                self.track.devices = original[:]
                self.track.view.selected_device = original[1]
                result = self.dispatch(position=position, expected_device_names=["Amp", "Tuner"])
                self.callbacks.pop()()
                self.assertEqual(result["position"], position)
                self.assertIs(self.track.view.selected_device, original[1])
                self.assertEqual(self.track.view.device_insert_mode, "default")
                status = self.control._dispatch("get_device_load", {"load_id": result["load_id"]})
                self.assertTrue(status["verified"])
                self.assertEqual(status["device"], position)
                self.assertEqual([d.name for d in self.track.devices],
                                 [d.name for d in original[:position]] + ["EQ Eight"] +
                                 [d.name for d in original[position:]])
                remaining = self.track.devices[:position] + self.track.devices[position + 1:]
                self.assertTrue(all(a is b for a, b in zip(remaining, original)))

    def test_invalid_positions_rejected_before_any_write(self):
        for position in (-1, 1, True, False, 0.5, "0", float("nan")):
            with self.subTest(position=position), self.assertRaisesRegex(ValueError, "Position"):
                self.dispatch(position=position)
        self.assertEqual(self.steps, [])
        self.assertEqual(self.track.devices, [])

    def test_wrong_insertion_position_or_reordered_chain_is_unverified(self):
        original = [types.SimpleNamespace(name="Amp", type=2), types.SimpleNamespace(name="Tuner", type=2)]
        self.track.devices = original[:]
        self.browser.load_item = lambda item: self.track.devices.append(
            types.SimpleNamespace(name=item.name, class_name="Eq8", type=2))
        result = self.dispatch(position=0, expected_device_names=["Amp", "Tuner"])
        self.callbacks.pop()()
        self.assertEqual(self.control._device_loads[result["load_id"]]["status"], "unverified")
        self.track.devices = original[:]
        self.browser.load_item = self.load
        result = self.dispatch(position=1, expected_device_names=["Amp", "Tuner"])
        self.track.devices[0], self.track.devices[2] = self.track.devices[2], self.track.devices[0]
        self.callbacks.pop()()
        self.assertEqual(self.control._device_loads[result["load_id"]]["status"], "unverified")

    def test_stock_effect_on_midi_track_must_follow_instrument(self):
        self.track.has_audio_input = False
        self.track.has_midi_input = True
        self.track.devices = [types.SimpleNamespace(name="Synth", type=1)]
        with self.assertRaisesRegex(ValueError, "after the instrument"):
            self.dispatch(position=0, expected_device_names=["Synth"])
        self.assertEqual(self.steps, [])
        result = self.dispatch(position=1, expected_device_names=["Synth"])
        self.callbacks.pop()()
        self.assertTrue(self.control._device_loads[result["load_id"]]["verified"])
