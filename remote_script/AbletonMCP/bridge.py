import json
import math
import socket
import uuid
import time
from _Framework.ControlSurface import ControlSurface
from .config import TOKEN, ALLOW_WRITES


def bounded(value, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Expected a number")
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError("Value outside allowed range")
    return value


class AbletonMCP(ControlSurface):
    def __init__(self, c_instance):
        super(AbletonMCP, self).__init__(c_instance)
        self._socket = None
        self._snapshots = {}
        self._device_snapshots = {}
        self._browser_items = {}
        self._device_loads = {}
        self._captures = {}
        self._capture_tracks = []
        if not TOKEN:
            self.log_message("AbletonMCP disabled: configure TOKEN")
            return
        try:
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._socket.bind(("127.0.0.1", 17891))
            self._socket.setblocking(False)
        except OSError as exc:
            if self._socket:
                self._socket.close()
            self._socket = None
            self.log_message("AbletonMCP socket failed: " + str(exc))
            return
        self.schedule_message(1, self._poll)

    def _poll(self):
        if self._socket is None:
            return
        # All Live API calls run on Live's scheduled control-surface thread.
        for _ in range(8):
            try:
                payload, peer = self._socket.recvfrom(8192)
            except BlockingIOError:
                break
            request_id = None
            try:
                request = json.loads(payload.decode("utf-8"))
                if not isinstance(request, dict) or request.get("token") != TOKEN:
                    continue
                request_id = request.get("id")
                result = self._dispatch(request["method"], request.get("params", {}))
                response = dict(id=request_id, result=result)
            except Exception as exc:
                response = dict(id=request_id, error=str(exc))
            encoded = json.dumps(response, allow_nan=False).encode("utf-8")
            if len(encoded) > 60000:
                encoded = json.dumps(dict(id=request_id, error="Response too large")).encode("utf-8")
            try:
                self._socket.sendto(encoded, peer)
            except OSError:
                pass
        self.schedule_message(1, self._poll)

    def _dispatch(self, method, params):
        song = self.song()
        if not isinstance(params, dict):
            raise ValueError("Expected parameter object")
        if method == "list_live_resampling_captures":
            return [dict(j["public"]) for j in getattr(self, "_captures", {}).values()]
        if method == "get_resampling_capture":
            job = getattr(self, "_captures", {}).get(params.get("capture_id"))
            if job is None:
                raise ValueError("Unknown capture; IDs expire on bridge restart")
            return dict(job["public"])
        if method == "cancel_resampling_capture":
            self._require_writes()
            job = getattr(self, "_captures", {}).get(params.get("capture_id"))
            if job is None:
                raise ValueError("Unknown capture")
            if job["public"]["status"] in ("recording", "finalizing", "cleaning"):
                self._end_capture(job, "cancelled")
            return dict(job["public"])
        if any(j["public"]["status"] in ("recording", "finalizing", "cleaning") for j in getattr(self, "_captures", {}).values()):
            if method not in ("get_project", "get_devices", "list_device_snapshots", "get_device_load"):
                raise ValueError("Capture active; wait or cancel before other commands")
        if method == "finalize_resampling_capture":
            self._require_writes()
            job = self._captures.get(params.get("capture_id"))
            if job is None or job["public"]["status"] != "completed":
                raise ValueError("Only a completed capture can be finalized")
            if job.get("crop_requested"):
                return dict(job["public"])
            if self.song() != job["song"] or job["song"].is_playing:
                raise ValueError("Keep the same set stopped while finalizing")
            slot = job["slot"]
            if not slot.has_clip or slot.clip != job["clip"] or slot.clip.is_recording:
                raise ValueError("Recorded capture clip changed")
            job["crop_requested"] = True
            job["public"]["recorded_source_path"] = job["public"]["source_path"]
            job["public"]["status"] = "finalizing"
            job["crop_deadline"] = time.monotonic() + 30
            try:
                # Crop only the agent-created capture; native audio crop creates
                # a separate sample without deleting the original recording.
                slot.clip.crop()
                self.schedule_message(2, lambda: self._poll_finalized_capture(job))
            except Exception as exc:
                job["public"].update(status="failed", error="Audio crop failed: " + str(exc))
            return dict(job["public"])
        if method == "prepare_resampling_track":
            self._require_writes()
            return self._prepare_capture_track(song)
        if method == "start_resampling_capture":
            self._require_writes()
            return self._start_capture(song, params)
        if method == "browse_audio_effects":
            return self._browse_audio_effects(params)
        if method == "load_audio_effect":
            self._require_writes()
            return self._load_audio_effect(song, params)
        if method == "get_device_load":
            job = self._device_loads.get(params.get("load_id"))
            if job is None:
                raise ValueError("Unknown or expired device load")
            return dict(job)
        if method == "create_device_snapshot":
            return self._create_device_snapshot(song, params.get("track"))
        if method == "list_device_snapshots":
            return [dict(snapshot_id=key, **value["summary"])
                    for key, value in self._device_snapshots.items() if value["song"] == song]
        if method == "delete_device_snapshot":
            snapshot_id = params.get("snapshot_id")
            if snapshot_id not in self._device_snapshots:
                raise ValueError("Unknown or expired device snapshot")
            del self._device_snapshots[snapshot_id]
            return dict(deleted=snapshot_id)
        if method == "restore_device_snapshot":
            self._require_writes()
            return self._restore_device_snapshot(song, params.get("snapshot_id"))
        if method == "create_mixer_snapshot":
            return self._create_snapshot(song)
        if method == "delete_mixer_snapshot":
            snapshot_id = params.get("snapshot_id")
            if snapshot_id not in self._snapshots:
                raise ValueError("Unknown or expired snapshot")
            del self._snapshots[snapshot_id]
            return dict(deleted=snapshot_id)
        if method == "restore_mixer_snapshot":
            self._require_writes()
            return self._restore_snapshot(song, params.get("snapshot_id"))
        if method == "set_playback_start":
            self._require_writes()
            start = bounded(params.get("start_beats"), 0, float("inf"))
            if song.record_mode or song.session_record:
                raise ValueError("Stop recording before changing the playback start marker")
            previous = song.start_time
            self._apply(song, [(song, "start_time", start)], verify=True)
            return dict(previous_start_beats=previous, start_beats=song.start_time)
        if method == "create_session_scene":
            self._require_writes()
            return self._create_session_scene(song, params)
        if method == "get_project":
            return dict(tempo=song.tempo, playing=song.is_playing,
                        scenes=[dict(index=i, name=s.name) for i, s in enumerate(song.scenes)],
                        time_signature=dict(numerator=song.signature_numerator,
                                            denominator=song.signature_denominator),
                        transport=dict(position_beats=song.current_song_time, start_beats=song.start_time,
                                       record_mode=song.record_mode,
                                       session_record=song.session_record, session_overrides=song.back_to_arranger,
                                       metronome=song.metronome, count_in_duration=int(song.count_in_duration), loop=song.loop,
                                       arrangement_length_beats=song.song_length),
                        tracks=[dict(index=i, name=t.name, mute=t.mute, solo=t.solo,
                                     armed=bool(t.arm) if t.can_be_armed else False,
                                     volume=t.mixer_device.volume.value,
                                     pan=t.mixer_device.panning.value)
                                for i, t in enumerate(song.tracks)])
        if method not in ("get_devices", "set_mixer", "set_device_parameter"):
            raise ValueError("Unknown method")
        index = params.get("track")
        if type(index) is not int or not 0 <= index < len(song.tracks):
            raise ValueError("Invalid regular track index")
        track = song.tracks[index]
        if method == "get_devices":
            return [dict(index=i, name=d.name, parameters=[
                dict(index=j, name=p.name, value=p.value, min=p.min, max=p.max,
                     enabled=p.is_enabled, quantized=p.is_quantized)
                for j, p in enumerate(d.parameters)]) for i, d in enumerate(track.devices)]
        self._require_writes()
        if method == "set_device_parameter":
            device_index = self._index(params.get("device"), track.devices, "device")
            device = track.devices[device_index]
            parameter_index = self._index(params.get("parameter"), device.parameters, "parameter")
            parameter = device.parameters[parameter_index]
            for key, actual in (("expected_track_name", track.name),
                                ("expected_device_name", device.name),
                                ("expected_parameter_name", parameter.name)):
                if params.get(key) != actual:
                    raise ValueError("Target changed; inspect tracks and devices again")
            self._validate_parameter(parameter, params.get("value"))
            expected = bounded(params.get("expected_value"), parameter.min, parameter.max)
            if not math.isclose(expected, parameter.value, rel_tol=1e-7, abs_tol=1e-7):
                raise ValueError("Parameter value changed; inspect devices again")
            previous = parameter.value
            self._apply(song, [(parameter, "value", params["value"])])
            return dict(track=index, device=device_index, parameter=parameter_index,
                        name=parameter.name, previous_value=previous, value=parameter.value)
        volume, pan = params.get("volume"), params.get("pan")
        if volume is None and pan is None:
            raise ValueError("Provide volume or pan")
        # Validate the entire command before touching the set.
        if volume is not None:
            bounded(volume, 0, 1)
        if pan is not None:
            bounded(pan, -1, 1)
        changes = []
        if volume is not None:
            changes.append((track.mixer_device.volume, "value", volume))
        if pan is not None:
            changes.append((track.mixer_device.panning, "value", pan))
        self._apply(song, changes)
        return dict(track=index, volume=track.mixer_device.volume.value,
                    pan=track.mixer_device.panning.value)

    @staticmethod
    def _require_writes():
        if not ALLOW_WRITES:
            raise ValueError("Bridge is read-only; enable ALLOW_WRITES and restart Live")

    def _create_session_scene(self, song, params):
        if song.record_mode or song.session_record:
            raise ValueError("Stop recording before adding a Session scene")
        before = tuple(song.scenes)
        index = params.get("index")
        if index is None:
            index = len(before)
        if type(index) is not int or not 0 <= index <= len(before):
            raise ValueError("Invalid scene insertion index")
        name = params.get("name")
        if name is not None and (not isinstance(name, str) or not name.strip() or len(name) > 256):
            raise ValueError("Scene name must be a nonblank string of at most 256 characters")
        song.begin_undo_step()
        try:
            song.create_scene(index)
            after = tuple(song.scenes)
            if len(after) != len(before) + 1 or after[:index] + after[index + 1:] != before:
                raise RuntimeError("Scene insertion readback differs from request")
            scene = after[index]
            if name is not None:
                scene.name = name
                if scene.name != name:
                    raise RuntimeError("Scene name readback differs from request")
            return dict(index=index, name=scene.name, scene_count=len(after))
        except Exception as exc:
            # Do not delete a possibly-created scene on an ambiguous failure.
            raise RuntimeError("Scene creation failed: " + str(exc) +
                               "; inspect get_project before retrying; use Live Undo if needed")
        finally:
            song.end_undo_step()

    def _prepare_capture_track(self, song):
        if song.is_playing or song.record_mode or song.session_record:
            raise ValueError("Stop transport and recording before preparing capture")
        for track in self._capture_tracks:
            if track in song.tracks:
                return dict(track=list(song.tracks).index(track), track_name=track.name)
        selected = song.view.selected_track
        song.begin_undo_step()
        try:
            song.create_audio_track(-1)
            track = song.tracks[-1]
            track.name = "Agent Capture"
            # Retain the actual Live routing object, never guess an identifier.
            routes = [r for r in track.available_input_routing_types if r.display_name == "Resampling"]
            if len(routes) != 1:
                raise ValueError("Resampling routing unavailable; new track remains for inspection")
            track.input_routing_type = routes[0]
            track.current_monitoring_state = 2  # Off
            track.mute = True  # Prevent the recorded clip feeding back on automatic playback.
            track.arm = False
            self._capture_tracks.append(track)
            return dict(track=len(song.tracks) - 1, track_name=track.name)
        finally:
            try:
                song.view.selected_track = selected
            finally:
                song.end_undo_step()

    def _start_capture(self, song, params):
        import Live
        start = bounded(params.get("start_beats"), 0, 1000000)
        length = bounded(params.get("length_beats"), 0.25, 4096)
        if not 1 <= length * 60 / song.tempo <= 120:
            raise ValueError("Capture must last 1..120 seconds at current tempo")
        track = song.tracks[self._index(params.get("track"), song.tracks, "capture track")]
        if track not in self._capture_tracks or params.get("expected_track_name") != track.name:
            raise ValueError("Use prepare_resampling_track and its current track/name")
        if song.is_playing or song.record_mode or song.session_record or song.back_to_arranger:
            raise ValueError("Stop playback/recording and return all tracks to Arrangement first")
        if song.current_song_time > song.song_length:
            raise ValueError("Move the stopped playhead within the Arrangement before capture; Live cannot restore positions beyond song_length")
        if any(t.can_be_armed and t.arm for t in song.tracks):
            raise ValueError("Disarm all tracks before capture")
        if track.devices or track.solo or not track.mute or track.current_monitoring_state != 2:
            raise ValueError("Capture track must be muted, monitor Off, unsoloed, without devices")
        if track.input_routing_type.display_name != "Resampling":
            raise ValueError("Capture input must remain Resampling")
        slot = next((s for s in track.clip_slots if not s.has_clip), None)
        if slot is None:
            raise ValueError("Capture track has no empty Session slot; retain or clear captures manually")
        if len(self._captures) >= 100:
            raise ValueError("Capture limit reached; restart bridge")
        capture_id = uuid.uuid4().hex
        public = dict(capture_id=capture_id, status="recording", track=params["track"],
                      start_beats=start, length_beats=length, tempo_at_start=song.tempo,
                      source_path=None, error=None)
        job = dict(public=public, song=song, track=track, slot=slot,
                   position=song.current_song_time, seen_recording=False,
                   tracks=tuple(song.tracks), returns=tuple(song.return_tracks),
                   selected=song.view.selected_track,
                   settings={key: getattr(song, key) for key in ("metronome", "loop", "session_record")},
                   deadline=time.monotonic() + 150)
        self._captures[capture_id] = job
        try:
            song.metronome = False
            song.loop = False
            song.current_song_time = start
            track.arm = True
            # count_in_duration is read-only. Start transport before Session
            # recording, so stopped-transport recording count-in is not used.
            song.start_playing()
            slot.fire(record_length=length, launch_quantization=Live.Song.Quantization.q_no_q)
            job["seen_recording"] = slot.has_clip and slot.clip.is_recording
            self.schedule_message(1, lambda: self._poll_capture(job))
        except Exception as exc:
            self._end_capture(job, "failed", str(exc))
        return dict(public)

    def _poll_capture(self, job):
        if job["public"]["status"] != "recording":
            return
        try:
            song, track, slot = job["song"], job["track"], job["slot"]
            if self.song() != song or tuple(song.tracks) != job["tracks"] or tuple(song.return_tracks) != job["returns"]:
                raise ValueError("Set or track topology changed")
            if time.monotonic() > job["deadline"]:
                raise ValueError("Capture watchdog expired")
            if not track.arm or not track.mute or track.input_routing_type.display_name != "Resampling":
                raise ValueError("Capture configuration changed")
            if not math.isclose(song.tempo, job["public"]["tempo_at_start"], abs_tol=1e-6):
                raise ValueError("Tempo changed; fixed-tempo capture required")
            if song.loop or song.metronome or track.current_monitoring_state != 2:
                raise ValueError("Capture/transport configuration changed")
            recording = slot.has_clip and slot.clip.is_recording
            if recording:
                job["seen_recording"] = True
            elif job["seen_recording"]:
                if not song.is_playing:
                    raise ValueError("Transport stopped before completion was observed")
                if song.current_song_time < job["public"]["start_beats"] + job["public"]["length_beats"] - 0.01:
                    raise ValueError("Recording ended before the requested range")
                job["public"]["source_path"] = slot.clip.file_path
                job["clip"] = slot.clip
                if not job["public"]["source_path"]:
                    raise ValueError("Recorded clip has no audio file")
                self._end_capture(job, "completed")
                return
            elif not song.is_playing and not slot.is_triggered:
                raise ValueError("Recording did not start or was stopped")
            self.schedule_message(1, lambda: self._poll_capture(job))
        except Exception as exc:
            self._end_capture(job, "failed", str(exc))

    def _end_capture(self, job, status, error=None):
        job["public"].update(status="cleaning", error=error)
        job["terminal_status"] = status
        job["cleanup_checks"] = 0
        job["stable_cleanup_checks"] = 0
        self._cleanup_capture(job)

    def _cleanup_capture(self, job):
        errors = []
        # Each cleanup is attempted even if a preceding Live operation fails.
        for action in (lambda: job["song"].stop_all_clips(0), job["song"].stop_playing,
                       lambda: setattr(job["track"], "arm", False),
                       lambda: setattr(job["song"], "back_to_arranger", False),
                       lambda: setattr(job["song"].view, "selected_track", job["selected"]),
                       lambda: setattr(job["song"], "current_song_time", job["position"])):
            try:
                action()
            except Exception as exc:
                errors.append(str(exc))
        for key, value in job["settings"].items():
            try:
                setattr(job["song"], key, value)
            except Exception as exc:
                errors.append(str(exc))
        job["public"]["cleanup_errors"] = errors
        if errors:
            job["public"]["status"] = "failed"
        else:
            self.schedule_message(2, lambda: self._verify_capture_cleanup(job))

    def _verify_capture_cleanup(self, job):
        if job["public"]["status"] != "cleaning":
            return
        try:
            song = job["song"]
            stable = (not song.is_playing and not song.back_to_arranger and not job["track"].arm
                      and abs(song.current_song_time - job["position"]) <= 0.0001
                      and all(getattr(song, key) == value for key, value in job["settings"].items()))
            job["cleanup_checks"] += 1
            job["stable_cleanup_checks"] = job["stable_cleanup_checks"] + 1 if stable else 0
            if job["stable_cleanup_checks"] >= 3:
                job["public"]["status"] = job["terminal_status"]
            elif job["cleanup_checks"] >= 15:
                raise ValueError("Live did not settle into the restored stopped state; inspect transport")
            elif not stable:
                self._cleanup_capture(job)
            else:
                self.schedule_message(2, lambda: self._verify_capture_cleanup(job))
        except Exception as exc:
            job["public"].update(status="failed", cleanup_errors=[str(exc)])

    def _poll_finalized_capture(self, job):
        if job["public"]["status"] != "finalizing":
            return
        try:
            if (self.song() != job["song"] or tuple(job["song"].tracks) != job["tracks"]
                    or not job["slot"].has_clip):
                raise ValueError("Capture clip or set changed during crop")
            # Native audio crop replaces the Clip object in the same slot.
            # Reacquire it only after our verified crop request, retaining the
            # source path and applying full file/range checks when archiving.
            clip = job["slot"].clip
            if clip.is_recording:
                raise ValueError("Capture slot started recording during crop")
            path = clip.file_path
            if path and path != job["public"]["recorded_source_path"]:
                job["clip"] = clip
                job["public"].update(status="completed", source_path=path, native_crop_applied=True)
                return
            if time.monotonic() > job["crop_deadline"]:
                raise ValueError("Audio crop did not produce a new file within 30 seconds")
            self.schedule_message(2, lambda: self._poll_finalized_capture(job))
        except Exception as exc:
            job["public"].update(status="failed", error=str(exc))

    def _browse_audio_effects(self, params):
        # Session handles retain the actual BrowserItem, avoiding guessed names/URIs.
        parent_id = params.get("parent_id")
        if parent_id is None:
            parent = self.application().browser.audio_effects
        else:
            parent = self._browser_items.get(parent_id)
            if parent is None:
                raise ValueError("Unknown or expired browser item; browse again")
        offset = params.get("offset", 0)
        if type(offset) is not int or offset < 0:
            raise ValueError("Offset must be a nonnegative integer")
        children = tuple(parent.children)
        if offset > len(children):
            raise ValueError("Offset exceeds child count")
        if len(self._browser_items) + min(50, len(children) - offset) > 2000:
            raise ValueError("Browser handle limit reached; restart the bridge")
        items = []
        for item in children[offset:offset + 50]:
            item_id = uuid.uuid4().hex
            self._browser_items[item_id] = item
            items.append(dict(item_id=item_id, name=item.name,
                              loadable=bool(item.is_loadable), folder=bool(item.is_folder)))
        return dict(items=items, total=len(children),
                    next_offset=offset + 50 if offset + 50 < len(children) else None)

    def _load_audio_effect(self, song, params):
        import Live
        index = self._index(params.get("track"), song.tracks, "regular track")
        track = song.tracks[index]
        devices = tuple(track.devices)
        if (params.get("expected_track_name") != track.name or
                params.get("expected_device_names") != [d.name for d in devices]):
            raise ValueError("Target changed; inspect tracks and devices again")
        item = self._browser_items.get(params.get("item_id"))
        if item is None or not item.is_loadable:
            raise ValueError("Unknown, expired or non-loadable browser item")
        if params.get("expected_item_name") != item.name:
            raise ValueError("Browser item changed; browse again")
        if any(job["status"] == "pending" for job in self._device_loads.values()):
            raise ValueError("A device load is pending; check its status before loading again")
        if len(self._device_loads) >= 100:
            raise ValueError("Device load limit reached; restart the bridge")
        browser = self.application().browser
        if browser.hotswap_target is not None:
            raise ValueError("Exit Live hot-swap mode before adding a device")
        # Live 11 exposes selected_right/default, not an "end" enum member.
        # Select the last top-level device explicitly, then insert to its right.
        mode = (Live.Track.DeviceInsertMode.selected_right if devices
                else Live.Track.DeviceInsertMode.default)
        old_mode = track.view.device_insert_mode
        old_track = song.view.selected_track
        old_device = track.view.selected_device
        load_id = uuid.uuid4().hex
        job = dict(load_id=load_id, status="pending", track=index,
                   track_name=track.name, browser_item_name=item.name, verified=False)
        self._device_loads[load_id] = job
        song.begin_undo_step()
        try:
            song.view.selected_track = track
            if devices:
                song.view.select_device(devices[-1])
            track.view.device_insert_mode = mode
            browser.load_item(item)
        except Exception as exc:
            job.update(status="failed", error=str(exc) + "; inspect Live before retrying")
        finally:
            try:
                track.view.device_insert_mode = old_mode
                if old_device is not None:
                    song.view.select_device(old_device)
                song.view.selected_track = old_track
            finally:
                song.end_undo_step()
        if job["status"] == "pending":
            self.schedule_message(2, lambda: self._verify_device_load(load_id, song, track, devices))
        return dict(job)

    def _verify_device_load(self, load_id, song, track, before):
        job = self._device_loads[load_id]
        try:
            current = tuple(track.devices)
            if (self.song() != song or track not in song.tracks or
                    len(current) != len(before) + 1 or current[:-1] != before):
                raise ValueError("Expected one appended device with the original chain intact")
            device = current[-1]
            job.update(status="completed", verified=True, device=len(before),
                       device_name=device.name, class_name=device.class_name)
        except Exception as exc:
            job.update(status="unverified", error=str(exc) +
                       "; inspect Live and use Undo if needed; do not automatically retry")

    @staticmethod
    def _index(index, items, label):
        if type(index) is not int or not 0 <= index < len(items):
            raise ValueError("Invalid " + label + " index")
        return index

    @staticmethod
    def _validate_parameter(parameter, value):
        if not parameter.is_enabled:
            raise ValueError("Parameter is disabled")
        bounded(value, parameter.min, parameter.max)
        if parameter.is_quantized and value != int(value):
            raise ValueError("Quantized parameter requires an integer native value")

    @staticmethod
    def _apply(song, changes, verify=False, verification_targets=None):
        originals = [(obj, attr, getattr(obj, attr)) for obj, attr, value in changes]
        verification_originals = ([(obj, attr, getattr(obj, attr))
                                  for obj, attr, value in verification_targets]
                                 if verification_targets is not None else None)
        song.begin_undo_step()
        applied = []
        try:
            for (obj, attr, value), original in zip(changes, originals):
                applied.append(original)
                setattr(obj, attr, value)
            if verify:
                for obj, attr, value in (verification_targets if verification_targets is not None else changes):
                    if not math.isclose(getattr(obj, attr), value, rel_tol=1e-6, abs_tol=1e-6):
                        raise RuntimeError("Parameter readback differs from requested value")
        except Exception as exc:
            rollback_errors = []
            rollback_targets = verification_originals if verification_originals is not None else applied
            for obj, attr, value in reversed(rollback_targets):
                try:
                    if math.isclose(getattr(obj, attr), value, rel_tol=1e-6, abs_tol=1e-6):
                        continue
                    setattr(obj, attr, value)
                except Exception as rollback_exc:
                    rollback_errors.append(str(rollback_exc))
            for obj, attr, value in rollback_targets:
                try:
                    if not math.isclose(getattr(obj, attr), value, rel_tol=1e-6, abs_tol=1e-6):
                        rollback_errors.append("Rollback readback differs from original value")
                except Exception as rollback_exc:
                    rollback_errors.append(str(rollback_exc))
            detail = "rollback incomplete; inspect current state" if rollback_errors else "original values restored"
            raise RuntimeError("Live rejected change: " + str(exc) + "; " + detail)
        finally:
            song.end_undo_step()

    @staticmethod
    def _mixer_tracks(song):
        return tuple(song.tracks) + tuple(song.return_tracks) + (song.master_track,)

    def _create_snapshot(self, song):
        if len(self._snapshots) >= 20:
            raise ValueError("Snapshot limit reached; delete an existing snapshot")
        tracks = self._mixer_tracks(song)
        entries = []
        for track in tracks:
            mixer = track.mixer_device
            parameters = (mixer.volume, mixer.panning) + tuple(mixer.sends)
            values = tuple(p.value for p in parameters)
            entry = dict(track=track, parameters=parameters, values=values)
            if track != song.master_track:
                entry.update(mute=track.mute, solo=track.solo)
            entries.append(entry)
        snapshot_id = uuid.uuid4().hex
        self._snapshots[snapshot_id] = dict(song=song, tracks=tracks, entries=entries,
                                           regular=tuple(song.tracks), returns=tuple(song.return_tracks))
        return dict(snapshot_id=snapshot_id, track_count=len(tracks),
                    track_names=[t.name for t in tracks])

    def _restore_snapshot(self, song, snapshot_id):
        snapshot = self._snapshots.get(snapshot_id)
        if snapshot is None:
            raise ValueError("Unknown or expired snapshot")
        if (song != snapshot["song"] or self._mixer_tracks(song) != snapshot["tracks"]
                or tuple(song.tracks) != snapshot["regular"]
                or tuple(song.return_tracks) != snapshot["returns"]):
            raise ValueError("Set or track topology changed; snapshot cannot be restored")
        changes = []
        for entry in snapshot["entries"]:
            track = entry["track"]
            mixer = track.mixer_device
            current = (mixer.volume, mixer.panning) + tuple(mixer.sends)
            if current != entry["parameters"]:
                raise ValueError("Mixer/send topology changed; snapshot cannot be restored")
            for parameter, value in zip(current, entry["values"]):
                self._validate_parameter(parameter, value)
                changes.append((parameter, "value", value))
            for attr in ("mute", "solo"):
                if attr in entry:
                    changes.append((track, attr, entry[attr]))
        self._apply(song, changes)
        return dict(restored=snapshot_id, track_count=len(snapshot["tracks"]))

    def _create_device_snapshot(self, song, track_index):
        if len(self._device_snapshots) >= 20:
            raise ValueError("Device snapshot limit reached; delete an existing snapshot")
        if track_index is None:
            tracks = self._mixer_tracks(song)
        else:
            self._index(track_index, song.tracks, "regular track")
            tracks = (song.tracks[track_index],)
        entries = []
        parameter_count = skipped = slots = device_count = 0
        for track in tracks:
            devices = tuple(track.devices)
            captured = []
            for device in devices:
                parameters = tuple(device.parameters)
                slots += len(parameters)
                if slots > 10000:
                    raise ValueError("Snapshot exceeds 10000 parameter slots; capture one track at a time")
                values = []
                metadata = []
                for parameter in parameters:
                    metadata.append((parameter.name, parameter.min, parameter.max, parameter.is_quantized))
                    if parameter.is_enabled:
                        self._validate_parameter(parameter, parameter.value)
                        values.append(parameter.value)
                        parameter_count += 1
                    else:
                        values.append(None)
                        skipped += 1
                captured.append(dict(device=device, parameters=parameters, values=tuple(values),
                                     metadata=tuple(metadata)))
                device_count += 1
            entries.append(dict(track=track, devices=devices, captured=captured))
        snapshot_id = uuid.uuid4().hex
        summary = dict(track_names=[t.name for t in tracks], device_count=device_count,
                       captured_parameter_count=parameter_count,
                       skipped_disabled_parameter_count=skipped,
                       scope="all top-level devices" if track_index is None else "regular track " + str(track_index))
        self._device_snapshots[snapshot_id] = dict(song=song, entries=entries, summary=summary,
            regular=tuple(song.tracks), returns=tuple(song.return_tracks), master=song.master_track)
        return dict(snapshot_id=snapshot_id, **summary)

    def _restore_device_snapshot(self, song, snapshot_id):
        snapshot = self._device_snapshots.get(snapshot_id)
        if snapshot is None:
            raise ValueError("Unknown or expired device snapshot")
        if (song != snapshot["song"] or tuple(song.tracks) != snapshot["regular"]
                or tuple(song.return_tracks) != snapshot["returns"]
                or song.master_track != snapshot["master"]):
            raise ValueError("Set or track topology changed; device snapshot cannot be restored")
        changes = []
        verification_targets = []
        # Validate every captured target before starting any writes or undo step.
        for entry in snapshot["entries"]:
            if tuple(entry["track"].devices) != entry["devices"]:
                raise ValueError("Device topology changed; snapshot cannot be restored")
            for captured in entry["captured"]:
                current = tuple(captured["device"].parameters)
                if current != captured["parameters"]:
                    raise ValueError("Parameter topology changed; snapshot cannot be restored")
                metadata = tuple((p.name, p.min, p.max, p.is_quantized) for p in current)
                if metadata != captured["metadata"]:
                    raise ValueError("Parameter names or ranges changed; snapshot cannot be restored")
                for parameter, value in zip(current, captured["values"]):
                    if value is None:
                        continue
                    verification_targets.append((parameter, "value", value))
                    if math.isclose(parameter.value, value, rel_tol=1e-7, abs_tol=1e-7):
                        continue
                    self._validate_parameter(parameter, value)
                    changes.append((parameter, "value", value))
        if changes:
            self._apply(song, changes, verify=True, verification_targets=verification_targets)
        return dict(restored=snapshot_id, changed_parameter_count=len(changes),
                    skipped_disabled_parameter_count=snapshot["summary"]["skipped_disabled_parameter_count"],
                    verified=True)

    def disconnect(self):
        for job in self._captures.values():
            if job["public"]["status"] in ("recording", "finalizing", "cleaning"):
                self._end_capture(job, "cancelled", "Bridge disconnected")
        self._browser_items.clear()
        self._device_loads.clear()
        self._snapshots.clear()
        self._device_snapshots.clear()
        if self._socket is not None:
            self._socket.close()
            self._socket = None
        super(AbletonMCP, self).disconnect()
