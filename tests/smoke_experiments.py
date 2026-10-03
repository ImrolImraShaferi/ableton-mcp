"""Exercise report tools over MCP stdio, isolating every artifact in a temp folder."""
import asyncio
import json
from pathlib import Path
import sys
import tempfile

import numpy as np
import soundfile as sf
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def payload(result):
    assert not result.isError, result.content
    return result.structuredContent or json.loads(result.content[0].text)


async def main():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        original, revised = root / "original.wav", root / "revised.wav"
        tone = 0.5 * np.sin(2 * np.pi * 1000 * np.arange(48000) / 48000)
        sf.write(original, tone, 48000, subtype="FLOAT")
        sf.write(revised, tone / 2, 48000, subtype="FLOAT")
        runner = ("import sys; from pathlib import Path; from ableton_mcp import experiments; "
                  "experiments.REPORTS = Path(sys.argv[1]); from ableton_mcp.server import main; main()")
        params = StdioServerParameters(command=sys.executable,
            args=["-c", runner, str(root / "reports")], cwd=directory,
            env={"ABLETON_MCP_TOKEN": ""})
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                saved = payload(await session.call_tool("save_mixing_experiment", {
                    "title": "MCP report test", "original_path": str(original),
                    "revised_path": str(revised), "changes": [{
                        "target": "Vocal EQ gain", "original_value": 0.4,
                        "proposed_value": 0.3, "applied_value": 0.3, "status": "applied"}]}))
                report_id = saved["report_id"]
                assert Path(saved["markdown_path"]).is_file()
                reports = payload(await session.call_tool("list_mixing_experiments", {}))
                assert reports["reports"][0]["report_id"] == report_id
                before = payload(await session.call_tool("read_mixing_experiment", {"report_id": report_id}))
                payload(await session.call_tool("update_mixing_experiment", {
                    "report_id": report_id, "listening_notes": "Prefer the revision.", "decision": "kept"}))
                after = payload(await session.call_tool("read_mixing_experiment", {"report_id": report_id}))
                assert before["comparison"] == after["comparison"]
                assert after["decision"] == "kept"
                assert after["changes"][0]["original_value"] == 0.4
    print("MCP report save, list, read and update passed; measurements preserved")


asyncio.run(main())
