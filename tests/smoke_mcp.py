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
                "list_mixing_experiments", "read_mixing_experiment", "update_mixing_experiment",
                "prepare_master_export", "get_master_export", "list_master_exports", "finish_master_export",
                "browse_audio_effects", "load_audio_effect", "get_device_load",
                "browse_plugins", "load_vst3_plugin", "list_device_loads",
                "analyze_audio_section", "analyze_audio_timeline", "compare_audio_reference", "compare_aligned_stems",
                "prepare_resampling_track", "start_resampling_capture", "get_resampling_capture",
                "cancel_resampling_capture", "finish_resampling_capture", "list_resampling_captures",
                "list_live_resampling_captures", "set_playback_start", "create_session_scene"}
            result = await session.call_tool("get_project", {})
            assert result.isError, "Missing bridge token must produce an MCP tool error"
            for name, arguments in [
                ("browse_plugins", {}),
                ("list_device_loads", {}),
                ("load_vst3_plugin", dict(track=0, item_id="a" * 32, expected_item_name="VST3 Effect",
                    expected_track_name="Guitar", expected_device_names=[], plugin_kind="audio_effect")),
                ("set_playback_start", dict(start_beats=32)),
                ("create_session_scene", dict(name="Verse")),
                ("prepare_resampling_track", {}),
                ("start_resampling_capture", dict(track=0, expected_track_name="Agent Capture", start_beats=0, length_beats=8)),
                ("get_resampling_capture", dict(capture_id="a" * 32)),
                ("cancel_resampling_capture", dict(capture_id="a" * 32)),
                ("finish_resampling_capture", dict(capture_id="a" * 32)),
                ("list_live_resampling_captures", {})]:
                result = await session.call_tool(name, arguments)
                assert result.isError, (name, "Missing token must fail")
            result = await session.call_tool("list_resampling_captures", {})
            assert not result.isError
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
                for name, arguments in [
                    ("analyze_audio_section", {"path": str(path), "end_seconds": .5}),
                    ("analyze_audio_timeline", {"path": str(path)}),
                    ("compare_audio_reference", {"mix_path": str(path), "reference_path": str(path)}),
                    ("compare_aligned_stems", {"first_path": str(path), "second_path": str(path), "alignment_confirmed": True})]:
                    result = await session.call_tool(name, arguments)
                    assert not result.isError, (name, result)
    print("MCP handshake, tool discovery, missing-token error, and audio analysis passed")


asyncio.run(main())
