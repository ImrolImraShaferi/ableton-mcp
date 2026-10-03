# Agent-operated master WAV exports

Status: shelved at the user's request. Keep the preparation/verification tools
available, but do not resume UI export development until requested.

This workflow uses the existing **computer-use** skill to operate Ableton Live's
normal export dialog. The MCP tools prepare unique export jobs and verify files;
they do not operate the dialog or start a render. It requires functioning desktop
computer use. If the computer-use runtime fails, report the exact failure and
leave the job pending. Do not claim an export happened.

The first implementation supports an arrangement in fixed 4/4, a master stereo
24-bit WAV, no normalization, no loop rendering, no dithering, and an explicit
musical range. Session clip overrides, tempo/meter automation and external
hardware need separate validation before extending this workflow.

## Prepare

1. Read the computer-use skill and its mandatory guidance/confirmation files.
   Use its supported Node REPL/sky APIs for all app interaction.
2. Inspect the current Live set. Confirm the intended arrangement, fixed 4/4,
   intended playback content, and that recording/rendering is not in progress.
   Resolve ambiguous sets or export ranges with the user. Do not launch playback,
   alter routing, dismiss unrelated dialogs or modify the set to guess a range.
3. Call `prepare_master_export` with `render_start`, `render_length` and sample
   rate. Start uses bar.beat.sixteenth (for example `1.1.1`); duration uses the
   same fields as elapsed units (`8.0.0` means eight bars). Use the user-approved
   time range. Supply an expected duration in seconds only if actually known;
   do not derive it from one tempo reading when tempo may vary.
4. Keep the returned job ID and unique absolute `output_path`. The folder is
   already created. Never overwrite an existing export.

## Operate Live

1. Select exactly one returned Ableton main window through the computer-use
   runtime. Observe its screenshot/accessibility state before input.
2. Open File → Export Audio/Video (`Control_L+Shift_L+r` on Windows). Refresh
   immediately. If the dialog is an owned secondary window, list windows and
   select that actual returned dialog. Never reconstruct a guessed window.
3. Observe and set each requested export field from the job. Re-observe after
   each action. Use actual accessible controls or coordinates from the latest
   screenshot; no saved coordinates, tab-count macros or guessed control IDs.
4. Check Rendered Track = Master, Render Start and Length, sample rate, PCM on,
   WAV, bit depth 24, dither None, Normalize off, Convert to Mono off, Render as
   Loop off and MP3 off. Labels can differ by version; use visible equivalents
   only when their meaning is clear. Stop if a requested setting cannot be read
   or controlled. Do not infer normalization or range from a resulting file.
5. Capture the final dialog state and record all settings as
   `ObservedExportSettings`. Click Export, then inspect the resulting Save
   dialog. Verify filename focus before typing the returned absolute WAV path.
   Save only to that unique destination. Never accept an overwrite prompt.
6. Observe Live's progress until rendering finishes. Unexpected warnings, hardware
   prompts or failures require inspection; do not repeatedly press Enter.
   Keep the user informed during longer renders. Do not mark completion from
   file existence or stable size alone: Live may preallocate a file.

## Verify and use

1. Call `finish_master_export` with the job ID, the actual observed export
   settings and `render_complete_observed=true` only after observing completion.
   It checks file stability, WAV/24-bit/stereo/rate, optional duration, finite
   audio samples and a full analysis with SHA-256 identity.
2. If verification fails, report its actual reason. Leave the job pending and
   inspect the state. Use a new job/path for a new export rather than overwriting
   a failed result. `get_master_export` and `list_master_exports` retrieve jobs
   after chat/client restarts, but do not finish UI work automatically.
3. Feed the verified path into audio comparison and experiment report tools.
   They do not alter the WAV or Live set. Export a second version using identical
   settings and range for a controlled before/after comparison.

## Current validation status

Job generation, observed-setting checks and file verification are automated-test
coverage. End-to-end Live UI rendering is **unverified**: during development the
computer-use Node REPL failed at Windows sandbox startup with
`helper_unknown_error: setup refresh had errors`. No live UI export was performed.
Verify this workflow on the installed Live version before calling it unattended
or reliable automated export.
