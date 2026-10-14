# Add an installed VST3 using the agent tools

This workflow adds a plug-in already installed, enabled and scanned in Ableton
Live. It does not download/install plug-ins, manage licenses, or rescan sources.
It uses the Remote Script browser API and requires no desktop UI automation.

## Setup

Update the installed bridge while Live is closed:

```powershell
python scripts/update_bridge.py "C:\path\to\User Library\Remote Scripts\AbletonMCP"
```

The updater preserves `config.py`, including its token and `ALLOW_WRITES`
setting. Loading requires `ALLOW_WRITES = True`. Start Live and reconnect the
MCP client. Save the set before testing device additions; parameter snapshots
cannot remove devices or restore the previous device chain.

## Agent sequence

1. Read `get_project` and `get_devices(track)` for the target's current name,
   ordered device names, input flags and device kinds. Only regular tracks are
   supported; track indices are zero-based.
2. Call `browse_plugins()` with no parent. In Live 11, enter the returned VST3
   folder, then a vendor folder, using `parent_id=item_id`. Use `next_offset` for
   additional pages. Each response contains at most 50 children. Newer Live
   versions may expose flat items with a VST3 URI instead. Only items reported
   `loadable: true` and `plugin_format: "VST3"` can be loaded. Unknown format
   means unsupported, even if the name resembles an installed VST3. Format and
   vendor paths distinguish duplicate plug-in names. Handles expire on restart.
3. Determine whether the chosen plug-in is an `audio_effect` or `instrument`
   from its documented function. Live's browser may not expose this type before
   loading. Effects need an audio track or a MIDI track with an instrument.
   Instruments need a MIDI track with no devices or only MIDI effects. The
   loader rejects instrument replacement and insertion into rack chains. Stop
   recording and leave hot-swap mode. Capture must also be inactive.
4. Call `load_vst3_plugin` using fresh values. For example:

   ```json
   {
     "track": 2,
     "item_id": "<actual returned VST3 item_id>",
     "expected_item_name": "<actual plug-in name>",
     "expected_track_name": "Guitar",
     "expected_device_names": ["Amp", "Tuner"],
     "plugin_kind": "audio_effect"
   }
   ```

5. Save the returned `load_id`. Poll `get_device_load(load_id)` until terminal.
   The chain must contain exactly one appended device with the original devices
   intact, and the device must have the requested instrument/effect type, before
   status becomes `completed` with `verified: true`. The initialization window
   is 15 seconds after Live's load call returns. This verifies topology and type;
   it does not prove licensing, audio quality, or plug-in GUI readiness.
6. Re-read `get_devices` before changing parameters. `set_device_parameter` can
   control enabled parameters exposed to Live using their actual native ranges.
   Some plug-ins require configuring parameters in Live first; their own GUI
   controls, internal preset browsers and unpublished parameters are excluded.

## Failures and recovery

- The bridge records the job before calling Live's loader. If the MCP request
  times out, call `list_device_loads`, then inspect `get_devices` and recover the
  job ID. Do not automatically repeat the load: the addition may have happened.
- A pending job blocks another device load. Slow initialization is polled without
  issuing the load again. A terminal `failed` or `unverified` job must be inspected
  before deciding what to do; there is no automatic topology rollback.
- Loading can open a plug-in window, license prompt or error dialog. Resolve such
  prompts in Live. A blocked Live thread may also prevent status calls returning.
- `view_restored: false` and `restoration_errors` report failures restoring the
  track/device selection, insertion mode or undo boundary. Inspect Live even if
  the chain itself was verified. A view failure does not trigger another load.
- Browser handles and jobs expire on bridge restart. Do not reuse old IDs or mix
  handles from `browse_audio_effects` with plug-in handles.
- Use Live Undo to remove an unwanted addition. An addition invalidates older
  device snapshots; make a fresh snapshot before changing the new parameters.

Example request:

> Inspect my tracks and browse my installed VST3s. Append one instance of the
> named compressor to the guitar track, verify the load and inspect its exposed
> parameters. Leave the settings at their defaults.

Live verification on Windows with Live 11.3.43 passed on 2026-10-14: vendor-folder
traversal identified installed VST3s, AgentMeter was appended to an empty audio
track, and sforzando was appended to an empty MIDI track. Both jobs returned
`completed`, `verified: true` and `view_restored: true`; readback reported the
correct device type and exposed parameters. Tests used the unsaved default set
and left the two plug-in instances there. This verifies insertion, not audio
processing, internal presets, or compatibility with every installed plug-in.

Mock tests additionally cover delayed initialization, timeout recovery, incompatible
tracks, duplicate names across formats, changed chains and restoration failures.
Real MCP stdio checks confirm discovery and missing-token errors for the new tools.
