# Ableton MCP local prototype

Agent-assisted mixing prototype for Ableton Live 11 Standard: inspect
a set through a Python Remote Script, adjust existing effects, restore snapshots,
and compare manually exported audio. No Max for
Live dependency. This is an experimental bridge; Live compatibility must be
verified on the installed version before relying on it.

User testing on Live 11 Standard has confirmed track listing, device/parameter
inspection, pan changes, device snapshot restoration and the exported-audio
workflow through a connected MCP client. Automated Live tests use mock objects;
this is not a compatibility guarantee for other Live versions or every plugin.

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
- `set_device_parameter`: change a top-level parameter using its native range,
  current track/device/parameter names and current value from fresh inspection.
  Disabled parameters and fractional enum/boolean values are rejected. Names
  and values reduce stale-target mistakes but do not uniquely identify devices
  with identical names. Re-inspect after structural changes.
- `create_mixer_snapshot`: returns an ID for an in-memory snapshot of regular,
  return and master mixer settings: volume, pan, sends, mute and solo where
  available. Maximum 20; snapshots expire when Live/bridge restarts.
- `restore_mixer_snapshot(snapshot_id)`: validate the set, track order, mixer
  objects and sends before restoring in one undo step. Device settings,
  automation, clips and routing are excluded. Automation can override values.
- `delete_mixer_snapshot(snapshot_id)`: release a saved snapshot.
- `create_device_snapshot(track)`: capture enabled parameters of top-level
  devices. Omit track for all regular, return and master tracks; specify a
  zero-based regular track index to capture just that track.
- `restore_device_snapshot(snapshot_id)`: validate track/device/parameter
  identities, names and ranges before restoring changed values in one undo
  step. Readback is checked; failures trigger attempted rollback.
- `list_device_snapshots`: retrieve snapshot IDs, scope and capture counts for
  the current set.
- `delete_device_snapshot(snapshot_id)`: discard a device snapshot.
- `list_audio_exports(folder)`: list WAV/AIFF/FLAC files in a local export folder.
- `analyze_audio_file(path)`: read-only measurements of a mono or stereo export,
  including integrated LUFS, sample peak, RMS, crest factor, full-scale sample
  counts, DC offset, stereo correlation/balance and frequency-band energy.
  Uses pyloudnorm's BS.1770-4
  integrated loudness meter. Requires neither Live nor a bridge token.
- `compare_audio_files(original_path, revised_path)`: before/after measurements,
  export mismatches, raw and gain-adjusted frequency-band deltas, and suggested
  listening gain to match integrated loudness. Files are not modified.
- `save_mixing_experiment`: save an audio baseline or comparison with recorded
  proposed/applied changes, original values, snapshot IDs and listening notes.
  Outputs persistent JSON and Markdown in the ignored `mixing_reports` folder.
- `list_mixing_experiments` / `read_mixing_experiment`: find and reopen saved reports.
- `update_mixing_experiment`: update listening notes and decision without
  re-analyzing audio or changing the saved measurements or Live set.
- `prepare_master_export`: create a unique WAV destination and requested export
  settings. **Does not start rendering**; a desktop agent must operate Live's UI.
- `get_master_export` / `list_master_exports`: retrieve pending or verified jobs.
- `finish_master_export`: validate recorded UI settings and an already rendered
  WAV, then analyze it. Does not operate or finish the export dialog itself.

Writes are disabled by default. To test changes, open a disposable copy of a
set, edit the **installed** `AbletonMCP/config.py` to set `ALLOW_WRITES = True`,
and restart Live. Each write command creates an undo step. Re-read tracks before
writing because indices can change. On assignment failure, the bridge attempts
to restore original values and reports whether rollback completed. Live undo
and rollback are not guarantees of an atomic transaction; inspect after errors.

The bridge binds only to 127.0.0.1:17891 and checks a random shared secret. Live
objects are accessed only from scheduled control-surface callbacks. Requests
have a three-second timeout and are not retried: if a write times out, inspect
the current state before trying again because it may already have happened.

This version does not listen to live audio, traverse rack chains, insert
devices, export audio, save sets, or perform automatic mixing/mastering.
Use manually exported audio measurements to inform mix decisions. Rendering
and mastering need a separately verified workflow.

## Agent-operated exports (UI rendering unverified)

The preparation and file-validation tools are implemented. Actual Live UI export
testing is blocked on this host because the computer-use Node REPL fails at
Windows sandbox startup (`helper_unknown_error: setup refresh had errors`), even
after a reset. There is no automatic dialog driver inside this MCP server.

When computer use is working, an agent can follow
[the export workflow](docs/agent-export-workflow.md): prepare a job, observe and
operate Live's normal Export Audio/Video dialog, save to its unique path, observe
render completion, then call `finish_master_export`. The first workflow covers
fixed 4/4 arrangements, master stereo 24-bit WAV, supported sample rates and an
explicit range. Normalization, MP3, loop rendering and mono conversion are off;
dither is None. Stem export, changing tempo/meters and external hardware are
excluded until separately tested.

For example, request a master export for bars 1 through 8: start `1.1.1`, length
`8.0.0`. The agent must inspect the dialog and record settings rather than guessing
coordinates or replaying an unattended keyboard macro. Unique outputs go to
`audio_exports/<job-id>/master.wav`; manifests persist in the ignored
`export_jobs` folder. No recordings or job records are committed by default.

Verification checks stability, WAV/24-bit/stereo/rate, optional known duration,
finite decoded audio and a full analysis. Stable size is insufficient proof of
completion because Live may preallocate files; the caller must observe Live
finishing. Master/range/normalization are UI observations and cannot be proved
from a WAV header. This is a prepared agent workflow, not a verified unattended
rendering feature. No new dependencies or bridge update are needed for the job
tools; restart the MCP client to load them.

## Analyze your recordings

Close the MCP client before updating dependencies on Windows (a running server
locks `ableton-mcp.exe`). Run this from a separate PowerShell in the project
folder, then reopen the client:

```powershell
.\.venv\Scripts\python.exe -m pip install -e .
```

No Remote Script update is needed for the audio tools. Export your mix or stems
from Live manually as WAV, AIFF or FLAC. Use the same time range for stems and
disable normalization if you want to inspect their current level relationships.
Put exports in `audio_exports` inside this project (create the folder), or tell
the agent an absolute folder path. Exports are excluded from Git in the default
folder; do not commit recordings placed elsewhere accidentally.

Example request:

> List my audio exports, analyze the mix WAV, and explain its loudness, sample
> peaks and stereo balance. Suggest what to inspect next without changing Live.

Files are read locally and left unchanged. Tools return numeric summaries, not
audio payloads. This is measurement, not a substitute for listening. Peaks are
sample peaks, not certified true peaks; full-scale samples indicate risk but
cannot prove whether clipping occurred earlier. No universal loudness target
is prescribed. Null logarithmic levels mean silence or an unavailable result;
shorter than 400 ms cannot produce integrated LUFS. Correlation is unavailable
for constant/silent channels. Limits: mono/stereo, 8..192 kHz, at most 10 minutes
and 128 MiB of decoded float32 audio; export a shorter section for larger files.

## Compare and record a mixing pass

1. Save a copy of your set. Create mixer and device snapshots before changes;
   keep both IDs. Record the original, proposed and actual native parameter values.
2. Export an original and revised version with identical sample rate, channel
   count, bit depth and time range. Disable export normalization.
3. Ask `compare_audio_files` to compare them. Mismatched duration, sample rate,
   channels, frame count or format are flagged, not silently corrected.
4. Ask `save_mixing_experiment` to record the comparison, changes and IDs.
   Leave its decision `pending` until you listen at matched loudness.
5. After listening, record notes and a decision using `update_mixing_experiment`:
   `kept`, `reverted` or `inconclusive`. This records your decision only; restore
   actual Live settings with the snapshot tools when appropriate.

Example request:

> Compare TestMCP.wav with TestMCP_01.wav, including frequency-band changes.
> Save a mixing experiment report. Record only change values we actually know
> and leave the listening decision pending. Do not change anything in Live.

All comparison deltas are revised minus original. Band energy percentage changes
are percentage points. Gain-adjusted changes subtract overall spectral level
change from each band's raw dB change; a uniform gain adjustment should therefore
show approximately zero tonal-shape change. The listening gain is the original
integrated LUFS minus revised LUFS and is a suggestion only. Matching headers
does not verify alignment, arrangement or content; these tools do not resample,
time-align or render loudness-matched files. File SHA-256 identities are saved to
identify the exact bytes measured, even if a later export overwrites the filename.

Frequency analysis averages channel power rather than summing stereo to mono,
so opposite-phase channels do not disappear. It uses Welch PSD with a Hann
window, 50% overlap and up to 8192 samples per window, processed in bounded
batches. Frequency resolution is returned in the result. DC is removed per
window; trailing incomplete windows are excluded. Bands span infra (0–20 Hz),
sub (20–60), bass (60–250), low mid (250–500), mid (500–2000), presence (2–6 kHz),
treble (6–12 kHz), air (12–20 kHz) and ultrasonic (above 20 kHz), bounded by
Nyquist. Unavailable bands return null; different coverage is flagged during
comparison. Whole-file band energy, dominant frequency and centroid are
descriptive: they do not diagnose masking, identify instruments or prescribe EQ.
Bands differ in width, and whole-file averages can hide short-section changes.

Reports preserve measured summaries and caller-supplied change records, not an
automatic audit of every Live action. They do not store audio or durable snapshot
state. Snapshot references expire on bridge restart; a saved report remains.
The JSON file is the source of truth; Markdown is a generated readable preview.
Reports and exports stay out of Git by default. New analysis/report tools only
require a client restart once dependencies are installed, not a bridge update.

## Validation

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m compileall -q ableton_mcp remote_script scripts tests
.\.venv\Scripts\python.exe tests/smoke_mcp.py
.\.venv\Scripts\python.exe tests/smoke_mcp.py --installed
.\.venv\Scripts\python.exe tests/smoke_experiments.py
```

Mocked tests check write gating, input validation, snapshot identity checks,
readback and rollback. Synthetic audio checks known peaks, RMS, LUFS, band energy,
gain comparisons and stereo cancellation. Report tests check saved records and
updates. Stdio smoke tests exercise MCP discovery and audio analysis/comparison.
These do not prove compatibility with every Live device or client.
If the control surface fails to load, inspect Live's Log.txt for AbletonMCP
errors. A port conflict usually means multiple selected bridge slots or Live
instances.

## Updating an already installed bridge

Close Live. Run the following using your actual installed bridge folder:

```powershell
python scripts/update_bridge.py "C:\path\to\User Library\Remote Scripts\AbletonMCP"
```

This copies bridge code and preserves the existing token and `ALLOW_WRITES`
setting. The MCP server uses an editable installation, so restart its client to
load the new tools. Restart Live too. Do not regenerate the token for an update.

Try on a copy of a set:

1. Inspect a device, record a parameter's native value, change it slightly with
   `set_device_parameter`, read it back, then restore the recorded value.
2. Create a mixer snapshot and keep its ID. Change a track's volume and pan,
   restore the snapshot, then read the mixer settings back to verify restoration.
3. Delete the snapshot when finished. Mixer snapshots do not restore device
   parameters; use a device snapshot for those.

## Restore effect settings after experiments

Update the installed bridge using `scripts/update_bridge.py` as above, preserving
`config.py`. Restart Live and the MCP client. Then ask on a copy of your set:

> Create both a mixer snapshot and a device snapshot and keep their IDs. Inspect
> a parameter on an existing effect, change it slightly, then restore the device
> snapshot and inspect the parameter again to confirm its original value.

Use the mixer snapshot separately for volume/pan/send changes. Device snapshots
capture only parameters exposed by Live for top-level devices, including rack
macros. They do not traverse rack chains or capture plugin presets, hidden
plugin state, automation envelopes, routing or mixer settings. Disabled
parameters at capture are skipped and counted in the result. If a captured
parameter changes and becomes disabled, restoration fails before any writes;
re-enable it manually and retry. Unchanged disabled parameters are not written.
Switches can affect availability of other controls, so restoration is deliberately
conservative. Automation may override restored values; readback checks only the
immediate result. Discrete values and ranges are validated in native units.

Snapshots stay in the bridge's memory and expire on Live/bridge restart. Maximum
20 device snapshots, each with at most 10000 parameter slots. Adding, deleting,
replacing or reordering tracks/devices/parameters blocks restoration even if
their names match; parameter names/ranges changing also blocks it. If you lose
an ID, ask the agent to list device snapshots. Delete snapshots when finished.
This is not a persistent backup of your Ableton set; save a set copy too.

## Add audio effects to a track

Automated UI exports are shelved pending working computer access. Device loading
uses the bridge's Live browser API and does not require computer-use automation.
This implementation needs a Live 11 test; mocked checks alone do not prove that
Live loads devices successfully.

Update the installed bridge with `scripts/update_bridge.py`, preserving config.py,
then restart Live and the MCP client. Work on a saved copy of the set first.
Ask the agent:

> Inspect my tracks and their devices. Browse Audio Effects to find EQ Eight.
> Append one EQ Eight to the guitar track, check the device load status, and
> inspect its parameters. Leave its settings at their defaults.

The tools are `browse_audio_effects`, `load_audio_effect`, and `get_device_load`.
Browser folders are traversed using returned item IDs, with 50 children per page.
Loading requires the current track name, ordered device names and browser item
name from fresh inspection. Only regular tracks and content under Audio Effects
are supported; no rack-chain insertion, instrument or plugin-category browsing.
Live must be out of hot-swap mode. The effect is appended at the end of the chain.
The bridge temporarily selects the track, restores selection, and groups the load
into an undo step. A later scheduled check verifies exactly one device was appended
and the old chain stayed intact. A pending status must be checked again; a failed
or unverified load must be inspected before any retry to avoid duplicates.
Handles and load statuses expire on bridge restart.

Use Live Undo to remove a test addition. Device snapshots capture parameter values;
they do not remove new devices, and adding a device invalidates old device snapshots.
Capture a new snapshot after adding an effect, before changing its parameters.

References:
- [Ableton remote script installation](https://help.ableton.com/hc/en-us/articles/209072009-Installing-third-party-remote-scripts)
- [MCP Python SDK v1](https://py.sdk.modelcontextprotocol.io/v1/)
- [Live parameter semantics](https://docs.cycling74.com/legacy/max8/vignettes/live_object_model)
- [pyloudnorm implementation](https://github.com/csteinmetz1/pyloudnorm)
- [SciPy Welch PSD documentation](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.welch.html)
