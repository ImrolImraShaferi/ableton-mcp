import json
import math
import socket
import uuid
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
        if method == "get_project":
            return dict(tempo=song.tempo, playing=song.is_playing,
                        tracks=[dict(index=i, name=t.name, mute=t.mute, solo=t.solo,
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
        self._browser_items.clear()
        self._device_loads.clear()
        self._snapshots.clear()
        self._device_snapshots.clear()
        if self._socket is not None:
            self._socket.close()
            self._socket = None
        super(AbletonMCP, self).disconnect()
