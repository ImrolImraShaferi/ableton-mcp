# Automated Master audition capture

Implemented in the Remote Script and MCP server without computer use, Max for
Live, additional dependencies, or the Export Audio/Video menu. Mocked lifecycle, file validation, and real MCP discovery are tested. On-host
validation passed on Live 11.3.43: two non-silent stereo captures were cropped,
archived and analyzed, followed by cancellation and verified state restoration.

## Installation

Update the installed bridge with `scripts/update_bridge.py`, preserving the token
and `ALLOW_WRITES` preference, then restart Live and the MCP client. Writes must
be enabled. Save a copy of the set before the first compatibility test.

## Agent workflow

1. Inspect the project. Stop transport and recording, disarm all tracks, and
   return tracks to Arrangement playback. This version captures Arrangement
   content only; it rejects existing Session overrides rather than changing them.
   Keep the stopped playhead at or before the Arrangement length; Live refuses
   to assign positions beyond `song_length`, although playback can run beyond it.
2. Call `prepare_resampling_track`. It creates one muted audio track named
   **Agent Capture**, routes its input to Resampling, switches monitoring Off,
   and returns its current index/name. Calls reuse tracks created in this bridge
   session. Creating the track invalidates earlier topology-sensitive snapshots;
   create new mixer/device snapshots afterward.
3. Call `start_resampling_capture` with that index/name and quarter-note beats
   from the Arrangement origin. In fixed 4/4, bar 1 starts at 0, bar 9 starts at
   32, and eight bars lasts 32 beats. Example:

   ```json
   {"track": 40, "expected_track_name": "Agent Capture", "start_beats": 32, "length_beats": 32}
   ```

   The track index is an example; always use the returned current index. Recording
   lasts 1..120 seconds at the current tempo. Metronome and Arrangement loop are temporarily disabled. The
   count-in preference is retained; transport starts before Session recording. All other tracks must be disarmed. The capture
   track must remain muted, unsoloed, without devices, with monitoring Off and
   Resampling input. Live's fixed-length clip recording controls the record boundary.
4. Poll `get_resampling_capture` using the returned ID every 1–2 seconds until
   completed, failed, or cancelled. Other bridge mutations are blocked during
   recording, cleaning, and finalizing. `cancel_resampling_capture` starts cleanup;
   poll until cancelled. Cleanup checks the restored stopped state on three
   successive callbacks, retrying stop if a deferred launch arrives late.
   A 150-second watchdog also attempts cleanup. If a start request times out,
   recover the ID with `list_live_resampling_captures`; do not start again blindly.
5. When completed, call `finish_resampling_capture`. It copies the completed
   stereo recording to `audio_exports/capture-ID/master.wav` (or AIFF), validates
   the file, and returns level/stereo/frequency measurements plus provenance.
   Live's source recording is retained. Completion is repeatable and archived
   results survive restart. `list_resampling_captures` lists archived results.
   On Windows, Live may keep its original recording locked while the set is open.
   If copying fails for that reason, the tool requests native audio cropping of
   only the agent-created capture clip. Poll through finalizing, then call finish
   again to read the new cropped sample. Audio crop replaces the clip object in
   its existing owned slot; the bridge reacquires the replacement. On-host native
   crop produced immediately readable stereo audio;
   a crop failure is reported rather than claiming the audio is archived.
6. Repeat after a parameter adjustment and use `compare_audio_files` or the
   timeline/reference tools. Save the decision in a mixing experiment.

The bridge stops transport, disarms the capture track, returns that track to
Arrangement, restores the previous stopped position and selected track, and
restores metronome/loop/session-record settings. Count-in is read-only and is
left unchanged. It reports cleanup
errors as failure. Partial recordings and clips remain for inspection; the
implementation never deletes clips or audio automatically. Each capture uses
an empty Session slot; when slots fill, retain or clear recordings manually.
Capture IDs and track ownership expire on bridge restart. Preparing after a
restart creates a fresh track rather than trusting an existing track by name.

## Limits and first Live test

- Real-time capture includes Master processing. Recording format and sample
  rate follow Live's preferences, rather than offline export settings.
- Fixed tempo is required; a detected tempo or track-topology change aborts.
  No sample-accurate alignment claim: archiving accepts at most 100 ms duration
  discrepancy. Native start-position and latency behavior need real Live testing.
- Playback starts at the requested position with current plugin state. Reverb,
  delay, random modulation, external inputs, and hardware can make repeated
  recordings differ. For tails, include additional beats in the requested range.
- This produces audio files and numerical analysis; perceptual listening needs
  a separately supported audio-model/client path.
- No saved-set changes, normalization, dithering, offline rendering, or automatic
  mixing decisions. The new capture track/clips remain in the open set.

First test: record four beats from a known audible passage. Confirm completed
status, non-silent stereo audio, correct musical passage and restored transport
state. Repeat the same range and compare durations/content. Test cancellation
before using this for A/B mix decisions. Mocked tests do not establish those facts.

On-host testing created repeated fixed-length recorded clips on Live 11.3.43. It exposed
two cleanup issues: restoring an original position beyond the Arrangement length
and a quantized stop leaving a Session override. The bridge now rejects the former
before recording and requests immediate clip stop plus Song-wide return to
Arrangement. Recording completion and immediate restoration succeeded after
these changes. The earlier file became readable after Live restart: stereo
48 kHz, 3.42858 seconds for four beats at 70 BPM, RMS -19.91 dBFS. New recordings
remain locked while Live is open. Cancellation exposed a deferred-launch race;
cleanup now verifies subsequent callbacks. Native recording, repeated recording,
cancellation and restored transport passed on-host. Audio crop produced an
immediately readable stereo 48 kHz sample, 3.42858 seconds and RMS -19.92 dBFS.
The final automatic crop-path handoff passed end to end. Both archived WAVs
were stereo 48 kHz and 3.428583 seconds long. Cancellation passed, with original
track mixer/arming states and stopped transport settings restored. This verifies
this host/version and range, not sample-accurate alignment or every Live set.

References: [Ableton Live 11 Resampling](https://www.ableton.com/en/live-manual/11/routing-and-i-o/),
[fixed-length clip recording API](https://docs.cycling74.com/apiref/lom/clipslot/),
[clip file-path API](https://docs.cycling74.com/apiref/lom/clip/).
