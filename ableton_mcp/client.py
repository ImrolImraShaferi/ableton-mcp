import json
import socket
import uuid


class BridgeClient:
    def __init__(self, token, port=17891):
        if not token:
            raise ValueError("Set ABLETON_MCP_TOKEN to the installed bridge token")
        self.token = token
        self.port = port

    def call(self, method, **params):
        request_id = uuid.uuid4().hex
        request = dict(id=request_id, token=self.token, method=method, params=params)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("127.0.0.1", self.port))
            sock.settimeout(3)
            sock.send(json.dumps(request).encode("utf-8"))
            try:
                response = json.loads(sock.recv(60000))
            except (TimeoutError, ConnectionRefusedError, ConnectionResetError) as exc:
                raise RuntimeError("Bridge unavailable: open Live and select AbletonMCP as a control surface") from exc
        if response.get("id") != request_id:
            raise RuntimeError("Bridge response ID mismatch")
        if "error" in response:
            raise RuntimeError(response["error"])
        return response["result"]
