import json
import math
import socket
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
        if method == "get_project":
            return dict(tempo=song.tempo, playing=song.is_playing,
                        tracks=[dict(index=i, name=t.name, mute=t.mute, solo=t.solo,
                                     volume=t.mixer_device.volume.value,
                                     pan=t.mixer_device.panning.value)
                                for i, t in enumerate(song.tracks)])
        if method not in ("get_devices", "set_mixer"):
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
        if not ALLOW_WRITES:
            raise ValueError("Bridge is read-only; enable ALLOW_WRITES and restart Live")
        volume, pan = params.get("volume"), params.get("pan")
        if volume is None and pan is None:
            raise ValueError("Provide volume or pan")
        # Validate the entire command before touching the set.
        if volume is not None:
            bounded(volume, 0, 1)
        if pan is not None:
            bounded(pan, -1, 1)
        song.begin_undo_step()
        try:
            if volume is not None:
                track.mixer_device.volume.value = volume
            if pan is not None:
                track.mixer_device.panning.value = pan
        finally:
            song.end_undo_step()
        return dict(track=index, volume=track.mixer_device.volume.value,
                    pan=track.mixer_device.panning.value)

    def disconnect(self):
        if self._socket is not None:
            self._socket.close()
            self._socket = None
        super(AbletonMCP, self).disconnect()
