import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf
from test_bridge import bridge
from ableton_mcp import resampling


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.control = bridge.AbletonMCP.__new__(bridge.AbletonMCP)
        self.control._captures = {}
        self.clip = types.SimpleNamespace(is_recording=True, file_path="C:/capture.wav")
        self.slot = types.SimpleNamespace(has_clip=False, is_triggered=False,
                                          fire=self.fire, stop=lambda: None)
        self.track = types.SimpleNamespace(name="Agent Capture", can_be_armed=True,
            arm=False, mute=True, solo=False, devices=[], current_monitoring_state=2,
            back_to_arranger=False, input_routing_type=types.SimpleNamespace(display_name="Resampling"),
            clip_slots=[self.slot])
        self.control._capture_tracks = [self.track]
        self.song = types.SimpleNamespace(tracks=[self.track], return_tracks=[],
            is_playing=False, record_mode=False, session_record=False, back_to_arranger=False,
            metronome=True, count_in_duration=2, loop=True, current_song_time=40, song_length=400, tempo=120,
            view=types.SimpleNamespace(selected_track=self.track), stop_playing=self.stop,
            start_playing=lambda: setattr(self.song, "is_playing", True))
        self.song.stop_all_clips = lambda quantized: self.slot.stop()
        self.control.song = lambda: self.song
        self.callbacks = []
        self.control.schedule_message = lambda ticks, callback: self.callbacks.append(callback)
        sys.modules["Live"] = types.SimpleNamespace(Song=types.SimpleNamespace(
            Quantization=types.SimpleNamespace(q_no_q=0)))
        bridge.ALLOW_WRITES = True

    def tearDown(self):
        bridge.ALLOW_WRITES = False
        sys.modules.pop("Live", None)

    def fire(self, **kwargs):
        self.assertEqual(kwargs, dict(record_length=8, launch_quantization=0))
        self.assertTrue(self.track.arm)
        self.assertTrue(self.song.is_playing)
        self.assertFalse(self.song.loop)
        self.assertEqual(self.song.current_song_time, 16)
        self.song.is_playing = True
        self.slot.has_clip = True
        self.slot.clip = self.clip
        self.song.back_to_arranger = True

    def flush(self):
        for _ in range(40):
            if not self.callbacks:
                return
            self.callbacks.pop(0)()
        self.fail("Callbacks did not settle")

    def stop(self):
        self.song.is_playing = False

    def start(self, **overrides):
        params = dict(track=0, expected_track_name="Agent Capture", start_beats=16, length_beats=8)
        params.update(overrides)
        return self.control._dispatch("start_resampling_capture", params)

    def test_record_complete_restore_and_recover_id(self):
        result = self.start()
        self.assertEqual(result["status"], "recording")
        self.assertEqual(self.control._dispatch("list_live_resampling_captures", {})[0], result)
        with self.assertRaisesRegex(ValueError, "Capture active"):
            self.control._dispatch("set_mixer", dict(track=0, volume=0.2))
        self.clip.is_recording = False
        self.song.current_song_time = 24
        self.flush()
        result = self.control._dispatch("get_resampling_capture", dict(capture_id=result["capture_id"]))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["source_path"], "C:/capture.wav")
        self.assertFalse(self.track.arm)
        self.assertFalse(self.song.back_to_arranger)
        self.assertFalse(self.song.is_playing)
        self.assertEqual(self.song.current_song_time, 40)
        self.assertTrue(self.song.metronome)
        self.assertTrue(self.song.loop)
        self.assertEqual(self.song.count_in_duration, 2)

    def test_preflight_and_readonly_have_no_side_effects(self):
        for key, value in [("arm", True), ("mute", False), ("solo", True), ("current_monitoring_state", 0)]:
            original = getattr(self.track, key)
            setattr(self.track, key, value)
            with self.assertRaises(ValueError):
                self.start()
            setattr(self.track, key, original)
        for params in [dict(start_beats=-1), dict(length_beats=float("nan")),
                       dict(track=True), dict(expected_track_name="Wrong"), dict(length_beats=4096)]:
            with self.assertRaises(ValueError):
                self.start(**params)
        bridge.ALLOW_WRITES = False
        with self.assertRaises(ValueError):
            self.start()
        self.assertEqual(self.control._captures, {})
        self.assertEqual(self.song.current_song_time, 40)

    def test_cancel_idempotent_and_callback_cannot_complete(self):
        result = self.start()
        params = dict(capture_id=result["capture_id"])
        self.assertEqual(self.control._dispatch("cancel_resampling_capture", params)["status"], "cleaning")
        self.flush()
        self.assertEqual(self.control._dispatch("cancel_resampling_capture", params)["status"], "cancelled")

    def test_late_launch_after_cancel_is_stopped_before_terminal_status(self):
        result = self.start()
        params = dict(capture_id=result["capture_id"])
        self.control._dispatch("cancel_resampling_capture", params)
        def deferred_launch():
            self.song.is_playing = True
            self.song.back_to_arranger = True
        self.callbacks.insert(0, deferred_launch)
        self.flush()
        self.assertEqual(self.control._dispatch("get_resampling_capture", params)["status"], "cancelled")
        self.assertFalse(self.song.is_playing)
        self.assertFalse(self.song.back_to_arranger)

    def test_cleanup_readback_failure_never_claims_cancellation(self):
        result = self.start()
        self.song.stop_playing = lambda: None
        params = dict(capture_id=result["capture_id"])
        self.control._dispatch("cancel_resampling_capture", params)
        self.flush()
        result = self.control._dispatch("get_resampling_capture", params)
        self.assertEqual(result["status"], "failed")
        self.assertIn("settle", result["cleanup_errors"][0])

    def test_early_completion_tempo_change_and_timeout_fail(self):
        for failure in ("early", "tempo", "watchdog", "topology"):
            self.setUp()
            result = self.start()
            job = self.control._captures[result["capture_id"]]
            if failure == "early":
                self.clip.is_recording = False
                self.song.current_song_time = 17
            elif failure == "tempo":
                self.song.tempo = 121
            elif failure == "watchdog":
                job["deadline"] = time.monotonic() - 1
            else:
                self.song.return_tracks.append(object())
            self.flush()
            self.assertEqual(job["public"]["status"], "failed")
            self.assertFalse(self.track.arm)
            self.assertFalse(self.song.is_playing)

    def test_cleanup_attempts_all_steps(self):
        result = self.start()
        self.slot.stop = lambda: (_ for _ in ()).throw(RuntimeError("stop failed"))
        job = self.control._captures[result["capture_id"]]
        self.control._end_capture(job, "cancelled")
        self.assertEqual(job["public"]["status"], "failed")
        self.assertEqual(job["public"]["cleanup_errors"], ["stop failed"])
        self.assertFalse(self.track.arm)
        self.assertEqual(self.song.current_song_time, 40)

    def test_start_exception_cleans_up(self):
        self.slot.fire = lambda **kwargs: (_ for _ in ()).throw(RuntimeError("fire failed"))
        result = self.start()
        self.flush()
        result = self.control._dispatch("get_resampling_capture", dict(capture_id=result["capture_id"]))
        self.assertEqual(result["status"], "failed")
        self.assertFalse(self.track.arm)
        self.assertEqual(self.song.current_song_time, 40)

    def test_playhead_beyond_arrangement_is_rejected_before_recording(self):
        self.song.current_song_time = 401
        with self.assertRaisesRegex(ValueError, "playhead"):
            self.start()
        self.assertEqual(self.control._captures, {})
        self.assertFalse(self.track.arm)

    def test_readonly_count_in_is_preserved(self):
        class ReadonlyCountInSong(types.SimpleNamespace):
            @property
            def count_in_duration(self):
                return 1
        values = vars(self.song).copy()
        del values["count_in_duration"]
        self.song = ReadonlyCountInSong(**values)
        result = self.start()
        self.assertEqual(result["status"], "recording")
        self.control._dispatch("cancel_resampling_capture", dict(capture_id=result["capture_id"]))
        self.flush()
        result = self.control._dispatch("get_resampling_capture", dict(capture_id=result["capture_id"]))
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(self.song.count_in_duration, 1)

    def test_prepare_creates_and_reuses_actual_routing(self):
        self.control._capture_tracks = []
        self.song.begin_undo_step = lambda: None
        self.song.end_undo_step = lambda: None
        new = types.SimpleNamespace(name="Audio", available_input_routing_types=[
            types.SimpleNamespace(display_name="Resampling")])
        self.song.create_audio_track = lambda index: self.song.tracks.append(new)
        result = self.control._dispatch("prepare_resampling_track", {})
        self.assertEqual(result, dict(track=1, track_name="Agent Capture"))
        self.assertIs(new.input_routing_type, new.available_input_routing_types[0])
        self.assertTrue(new.mute)
        self.assertFalse(new.arm)
        self.assertEqual(self.control._dispatch("prepare_resampling_track", {}), result)
        self.assertEqual(len(self.song.tracks), 2)

    def test_native_crop_changes_only_completed_capture_and_waits_for_path(self):
        result = self.start()
        self.clip.is_recording = False
        self.song.current_song_time = 24
        self.flush()
        self.clip.crop = lambda: setattr(self.clip, "file_path", "C:/cropped.wav")
        params = dict(capture_id=result["capture_id"])
        result = self.control._dispatch("finalize_resampling_capture", params)
        self.assertEqual(result["status"], "finalizing")
        self.flush()
        result = self.control._dispatch("get_resampling_capture", params)
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["native_crop_applied"])
        self.assertEqual(result["source_path"], "C:/cropped.wav")
        self.assertEqual(self.control._dispatch("finalize_resampling_capture", params), result)

    def test_crop_timeout_or_replaced_clip_fails(self):
        for change in ("timeout", "replaced"):
            self.setUp()
            result = self.start()
            self.clip.is_recording = False
            self.song.current_song_time = 24
            self.flush()
            self.clip.crop = lambda: None
            self.control._dispatch("finalize_resampling_capture", dict(capture_id=result["capture_id"]))
            job = self.control._captures[result["capture_id"]]
            if change == "timeout":
                job["crop_deadline"] = time.monotonic() - 1
            else:
                self.slot.clip = object()
            self.flush()
            self.assertEqual(job["public"]["status"], "failed")

    def test_audio_crop_replaces_clip_in_owned_slot(self):
        result = self.start()
        self.clip.is_recording = False
        self.song.current_song_time = 24
        self.flush()
        replacement = types.SimpleNamespace(is_recording=False, file_path="C:/cropped.wav")
        self.clip.crop = lambda: setattr(self.slot, "clip", replacement)
        params = dict(capture_id=result["capture_id"])
        self.control._dispatch("finalize_resampling_capture", params)
        self.flush()
        result = self.control._dispatch("get_resampling_capture", params)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["source_path"], "C:/cropped.wav")
        self.assertEqual(result["recorded_source_path"], "C:/capture.wav")


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "live.wav"
        sf.write(self.source, np.zeros((48000, 2), dtype=np.float32), 48000, subtype="PCM_24")
        self.job = dict(status="completed", source_path=str(self.source), length_beats=2,
                        tempo_at_start=120, cleanup_errors=[])
        self.identifier = "a" * 32
        self.patches = [patch.object(resampling, "DEFAULT_EXPORTS", self.root / "archives"),
                        patch.object(resampling, "_stable", lambda path: None)]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in self.patches:
            item.stop()
        self.temp.cleanup()

    def finish(self):
        return resampling.finish_capture(self.identifier, lambda *args, **kwargs: self.job)

    def test_archive_idempotent_source_preserved_and_list(self):
        before = self.source.read_bytes()
        result = self.finish()
        self.assertEqual(result["status"], "archived")
        self.assertEqual(Path(result["output_path"]).read_bytes(), before)
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(self.finish(), result)
        self.assertEqual(resampling.list_captures()["captures"][0]["capture_id"], self.identifier)
        sf.write(result["output_path"], np.ones((48000, 2)), 48000)
        with self.assertRaisesRegex(ValueError, "modified"):
            self.finish()

    def test_unfinished_mono_wrong_duration_and_invalid_id_rejected(self):
        self.job["status"] = "recording"
        with self.assertRaises(ValueError):
            self.finish()
        self.job["status"] = "completed"
        sf.write(self.source, np.zeros(48000), 48000)
        with self.assertRaisesRegex(ValueError, "stereo"):
            self.finish()
        sf.write(self.source, np.zeros((24000, 2)), 48000)
        with self.assertRaisesRegex(ValueError, "duration"):
            self.finish()
        with self.assertRaises(ValueError):
            resampling.finish_capture("../bad", lambda *args, **kwargs: self.job)

    def test_locked_recording_requests_native_crop_without_claiming_archive(self):
        calls = []
        def call(method, **params):
            calls.append(method)
            return self.job if method == "get_resampling_capture" else dict(status="finalizing")
        with patch.object(resampling.shutil, "copyfile", side_effect=PermissionError("locked")):
            with self.assertRaisesRegex(ValueError, "Native audio crop requested"):
                resampling.finish_capture(self.identifier, call)
        self.assertEqual(calls, ["get_resampling_capture", "finalize_resampling_capture"])
        self.assertEqual(resampling.list_captures()["captures"], [])


if __name__ == "__main__":
    unittest.main()
