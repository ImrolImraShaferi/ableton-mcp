import os
from mcp.server.fastmcp import FastMCP
from .client import BridgeClient

mcp = FastMCP("Ableton Live local prototype")


def call(method, **params):
    return BridgeClient(os.environ.get("ABLETON_MCP_TOKEN", "")).call(method, **params)


@mcp.tool()
def get_project() -> dict:
    """Read tempo, transport and track names from the current Live set."""
    return call("get_project")


@mcp.tool()
def get_devices(track: int) -> list:
    """Read top-level devices and parameters on a zero-based regular track."""
    return call("get_devices", track=track)


@mcp.tool()
def set_mixer(track: int, volume: float | None = None, pan: float | None = None) -> dict:
    """Set native volume (0..1, NOT dB) and pan (-1..1). Requires bridge writes enabled.

    Read the project first; track indices can change when tracks are reordered.
    """
    return call("set_mixer", track=track, volume=volume, pan=pan)


@mcp.tool()
def set_device_parameter(track: int, device: int, parameter: int, value: float,
                         expected_track_name: str, expected_device_name: str,
                         expected_parameter_name: str, expected_value: float) -> dict:
    """Change a top-level device parameter in native units from get_devices.

    Supply current names and value from fresh inspection. Quantized values must
    be integers. Requires bridge writes enabled. Automation can override changes.
    """
    return call("set_device_parameter", track=track, device=device, parameter=parameter,
                value=value, expected_track_name=expected_track_name,
                expected_device_name=expected_device_name,
                expected_parameter_name=expected_parameter_name, expected_value=expected_value)


@mcp.tool()
def create_mixer_snapshot() -> dict:
    """Capture regular/return/master volume, pan, sends, mute and solo in Live memory.

    Save the returned snapshot_id. Snapshots expire on bridge restart; device
    parameters, automation, clips and routing are excluded. Maximum 20 snapshots.
    """
    return call("create_mixer_snapshot")


@mcp.tool()
def restore_mixer_snapshot(snapshot_id: str) -> dict:
    """Restore a saved mixer snapshot in one undo step; requires writes enabled.

    Rejects changed track/send topology or a different set before writing.
    Automation may subsequently override restored values.
    """
    return call("restore_mixer_snapshot", snapshot_id=snapshot_id)


@mcp.tool()
def delete_mixer_snapshot(snapshot_id: str) -> dict:
    """Discard a mixer snapshot to release memory; does not modify the Live set."""
    return call("delete_mixer_snapshot", snapshot_id=snapshot_id)


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
