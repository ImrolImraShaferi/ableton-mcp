"""Exercise the real MCP stdio handshake without a running Ableton instance."""
import asyncio
import json
import sys
from pathlib import Path
import tempfile
import numpy as np
import soundfile as sf
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    installed = "--installed" in sys.argv
    command = str(Path(sys.executable).with_name("ableton-mcp.exe")) if installed else sys.executable
    params = StdioServerParameters(command=command,
        args=[] if installed else ["-m", "ableton_mcp.server"],
        cwd=tempfile.gettempdir(), env={"ABLETON_MCP_TOKEN": ""})
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            assert {t.name for t in tools.tools} == {"get_project", "get_devices", "set_mixer",
                "set_device_parameter", "create_mixer_snapshot", "restore_mixer_snapshot",
                "delete_mixer_snapshot", "list_audio_exports", "analyze_audio_file",
                "create_device_snapshot", "restore_device_snapshot", "list_device_snapshots",
                "delete_device_snapshot", "compare_audio_files", "save_mixing_experiment",
                "list_mixing_experiments", "read_mixing_experiment", "update_mixing_experiment"}
            result = await session.call_tool("get_project", {})
            assert result.isError, "Missing bridge token must produce an MCP tool error"
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "silence.wav"
                sf.write(path, np.zeros(48000), 48000)
                result = await session.call_tool("analyze_audio_file", {"path": str(path)})
                assert not result.isError, "Audio analysis must work without Live or a bridge token"
                payload = result.structuredContent or json.loads(result.content[0].text)
                assert payload["integrated_loudness_lufs"] is None
                result = await session.call_tool("compare_audio_files", {
                    "original_path": str(path), "revised_path": str(path)})
                assert not result.isError
                payload = result.structuredContent or json.loads(result.content[0].text)
                assert payload["export_metadata_match"]
                assert payload["original"]["frequency_analysis"]["available"]
    print("MCP handshake, tool discovery, missing-token error, and audio analysis passed")


asyncio.run(main())
