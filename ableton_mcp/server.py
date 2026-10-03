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


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
