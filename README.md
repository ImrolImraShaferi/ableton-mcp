# Ableton MCP local prototype

First milestone toward agent-assisted mixing on Ableton Live 11 Standard: inspect
a set through a Python Remote Script, with optional mixer adjustments. No Max for
Live dependency. This is an experimental bridge; Live compatibility must be
verified on the installed version before relying on it.

User testing on Live 11 Standard has confirmed track listing, device/parameter
inspection, and pan changes through a connected MCP client. Automated tests use
mock Live objects; volume changes and other Live API behavior still need live
verification.

## Setup on Windows

Use Python 3.10+ outside Live. The bridge uses Live's bundled Python and has no
third-party dependencies. From this project directory in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
python scripts/prepare_bridge.py
```

Copy the generated `build/AbletonMCP` folder into `Remote Scripts` inside your
configured Ableton User Library (create `Remote Scripts` if needed). Find the
actual User Library location in Live's Library preferences; do not assume the
default path. Restart Live, then choose **AbletonMCP** under Preferences →
Link/Tempo/MIDI → Control Surface. Leave its MIDI input and output at None.
Select it in only one control-surface slot and run only one Live instance.

Configure an MCP client to launch the absolute path to
`.venv/Scripts/ableton-mcp.exe` using stdio, with environment variable
`ABLETON_MCP_TOKEN` set to the contents of `build/token.txt`. Keep the token
private. Client-specific registration is a separate setup step.

First test manually from PowerShell after the bridge is selected:

```powershell
$env:ABLETON_MCP_TOKEN = Get-Content -Raw build/token.txt
.\.venv\Scripts\python.exe -c "import os; from ableton_mcp.client import BridgeClient; print(BridgeClient(os.environ['ABLETON_MCP_TOKEN']).call('get_project'))"
```

## Tools and limits

- `get_project`: tempo, playing state, regular track names, volume, pan, mute, solo.
- `get_devices(track)`: top-level devices and parameters; zero-based regular tracks.
- `set_mixer(track, volume, pan)`: native volume 0..1 (not dB), pan -1..1.

Writes are disabled by default. To test changes, open a disposable copy of a
set, edit the **installed** `AbletonMCP/config.py` to set `ALLOW_WRITES = True`,
and restart Live. Each mixer command creates an undo step. Re-read tracks before
writing because indices can change. An undo step is not an atomic transaction:
if Live rejects a later assignment, an earlier assignment can remain changed.

The bridge binds only to 127.0.0.1:17891 and checks a random shared secret. Live
objects are accessed only from scheduled control-surface callbacks. Requests
have a three-second timeout and are not retried: if a write times out, inspect
the current state before trying again because it may already have happened.

This version does not listen to or analyze audio, traverse rack chains, insert
devices, export audio, save sets, or perform automatic mixing/mastering. Next
milestones: verify Live connectivity, add tested device parameter writes and
state snapshots, then analyze manually exported audio for level/loudness and
mix decisions. Rendering and mastering need a separately verified workflow.

## Validation

```powershell
python -m unittest discover -s tests -v
python -m compileall -q ableton_mcp remote_script scripts tests
.\.venv\Scripts\python.exe tests/smoke_mcp.py
```

Mocked tests check write gating, input validation before mutation, and mixer
updates. They do not prove Live API compatibility or MCP client integration.
If the control surface fails to load, inspect Live's Log.txt for AbletonMCP
errors. A port conflict usually means multiple selected bridge slots or Live
instances.

References:
- [Ableton remote script installation](https://help.ableton.com/hc/en-us/articles/209072009-Installing-third-party-remote-scripts)
- [MCP Python SDK v1](https://py.sdk.modelcontextprotocol.io/v1/)
