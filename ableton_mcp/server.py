import os
from mcp.server.fastmcp import FastMCP
from .client import BridgeClient
from .audio import analyze_file, list_exports, compare_files
from .analysis import timeline, compare_reference, compare_stems
from .experiments import (MixingChange, Decision, save_experiment, read_experiment,
                          update_experiment, list_experiments)
from .exports import (ObservedExportSettings, prepare_export, get_export,
                      finish_export, list_exports_jobs)

mcp = FastMCP("Ableton Live local prototype")


@mcp.tool()
def prepare_master_export(render_start: str, render_length: str, sample_rate_hz: int = 48000,
                          expected_duration_seconds: float | None = None,
                          label: str = "Master mix") -> dict:
    """Prepare a unique master WAV destination and settings for agent-operated Live UI export.

    DOES NOT open the dialog or start rendering. The agent must use a functioning
    computer-use skill and the returned workflow guide to operate Live. Initial
    workflow supports fixed 4/4 only: start 1.1.1, duration 8.0.0 = eight bars.
    Requires a user-approved range. Default stereo 24-bit WAV, normalization off.
    UI rendering is unverified on this host because computer-use startup failed.
    """
    return prepare_export(render_start, render_length, sample_rate_hz, expected_duration_seconds, label)


@mcp.tool()
def get_master_export(job_id: str) -> dict:
    """Read an export request/status. File existence alone does not prove rendering completed."""
    return get_export(job_id)


@mcp.tool()
def list_master_exports() -> dict:
    """List up to 100 saved export requests and verified results."""
    return list_exports_jobs()


@mcp.tool()
def finish_master_export(job_id: str, observed_settings: ObservedExportSettings,
                         render_complete_observed: bool) -> dict:
    """Verify an agent-operated UI export and analyze the resulting WAV.

    Supply actual settings observed in Live's export dialog and true only after
    observing render completion. Checks file stability/format/rate/optional
    duration, then performs full audio analysis. Does not initiate or finish UI
    rendering itself. A mismatch leaves the request pending; do not infer facts.
    """
    return finish_export(job_id, observed_settings, render_complete_observed)


@mcp.tool()
def list_audio_exports(folder: str | None = None) -> dict:
    """List up to 100 WAV/AIFF/FLAC exports without recursion.

    Default is this project's audio_exports folder. Otherwise provide an absolute
    local folder path. Requires neither Ableton nor a bridge token.
    """
    return list_exports(folder)


@mcp.tool()
def analyze_audio_file(path: str) -> dict:
    """Measure a local mono/stereo WAV/AIFF/FLAC export without modifying it.

    Provide an absolute path. Reports integrated LUFS, sample peak (not true peak),
    RMS, crest factor, full-scale samples, DC, stereo balance/correlation and
    frequency-band energy, spectral centroid and dominant frequency.
    Native levels are measurements, not mixing prescriptions. Max 10 minutes
    and 128 MiB decoded float32 audio. Requires neither Live nor a bridge token.
    """
    return analyze_file(path)


@mcp.tool()
def analyze_audio_section(path: str, start_seconds: float = 0,
                          end_seconds: float | None = None) -> dict:
    """Measure a selected passage in seconds from an absolute WAV/AIFF/FLAC path.

    Same metrics as analyze_audio_file. Reads only selected samples; hashes the
    whole file. Range must be within the source. No export or Live changes.
    """
    return analyze_file(path, start_seconds, end_seconds)


@mcp.tool()
def analyze_audio_timeline(path: str, start_seconds: float = 0,
                           end_seconds: float | None = None,
                           window_seconds: float = 3, hop_seconds: float = 3) -> dict:
    """Timestamped loudness, spectrum, crest factor, stereo and mono checks.

    Absolute export path; up to 300 windows, minimum window 0.4 seconds.
    Momentary/short-term ungated K-weighted measurements cover final 0.4/3 seconds
    of each window with filter resets. Partial windows retained; null unavailable.
    Mono uses (L+R)/2. Sample peaks only; no true peaks or instrument separation.
    """
    return timeline(path, start_seconds, end_seconds, window_seconds, hop_seconds)


@mcp.tool()
def compare_audio_reference(mix_path: str, reference_path: str,
                            mix_start_seconds: float = 0,
                            mix_end_seconds: float | None = None,
                            reference_start_seconds: float = 0,
                            reference_end_seconds: float | None = None) -> dict:
    """Compare chosen mix/reference passages, including LUFS-matched band deltas.

    Absolute paths; select musically comparable passages. Suggested gain is not
    applied. Different durations permitted; incompatible band coverage flagged.
    No universal tonal target or mix-quality score.
    """
    return compare_reference(mix_path, reference_path, mix_start_seconds,
        mix_end_seconds, reference_start_seconds, reference_end_seconds)


@mcp.tool()
def compare_aligned_stems(first_path: str, second_path: str,
                          alignment_confirmed: bool = False,
                          start_seconds: float = 0, end_seconds: float | None = None,
                          window_seconds: float = 3, hop_seconds: float = 3) -> dict:
    """Compare timestamped band activity of two stems with confirmed common origin.

    Requires alignment_confirmed=true from known export provenance, matching
    source durations and sample rates. No alignment/resampling performed.
    Coactivity requires each band >=1% stem energy and >-70 dBFS; this flags
    possible competition, not proven masking. Up to 300 windows.
    """
    return compare_stems(first_path, second_path, alignment_confirmed,
        start_seconds, end_seconds, window_seconds, hop_seconds)


@mcp.tool()
def compare_audio_files(original_path: str, revised_path: str) -> dict:
    """Compare two local exports: levels, stereo and frequency bands, revised minus original.

    Flags duration/sample-rate/channel/format mismatches. Matching metadata is
    not proof of time alignment. Includes gain-adjusted band changes to separate
    spectral shape from a simple gain change. Listening gain is suggested only;
    files are neither aligned nor modified. Same limits as analyze_audio_file.
    """
    return compare_files(original_path, revised_path)


@mcp.tool()
def save_mixing_experiment(title: str, original_path: str, revised_path: str | None = None,
                          changes: list[MixingChange] | None = None,
                          mixer_snapshot_id: str | None = None,
                          device_snapshot_id: str | None = None,
                          listening_notes: str = "", decision: Decision = "pending") -> dict:
    """Save measured audio comparison/baseline and recorded changes as JSON and Markdown.

    Files go to this project's ignored mixing_reports folder. Measurements and
    SHA-256 identities are computed now; supplied changes/notes are caller records,
    not an automatic Live audit. Record original/proposed/applied native values.
    Snapshot IDs are references, not persistent backups. No Live modifications.
    Use pending/inconclusive until the user supplies a listening decision.
    """
    return save_experiment(title, original_path, revised_path, changes, mixer_snapshot_id,
                           device_snapshot_id, listening_notes, decision)


@mcp.tool()
def list_mixing_experiments() -> dict:
    """List up to 100 saved experiment IDs, newest updated first."""
    return list_experiments()


@mcp.tool()
def read_mixing_experiment(report_id: str) -> dict:
    """Read a saved experiment's measured results and recorded decisions."""
    return read_experiment(report_id)


@mcp.tool()
def update_mixing_experiment(report_id: str, listening_notes: str, decision: Decision) -> dict:
    """Replace listening notes and decision in a saved report; preserves measurements.

    This records a user-supplied decision and does not keep or revert Live changes.
    """
    return update_experiment(report_id, listening_notes, decision)


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
def browse_audio_effects(parent_id: str | None = None, offset: int = 0) -> dict:
    """Browse Live's Audio Effects tree, 50 children per page. Start with no parent_id.

    Traverse folders by passing their item_id. Use next_offset for more children.
    Handles expire on bridge restart. Only items reported loadable can be loaded.
    This lists actual installed content; availability depends on Live edition.
    """
    return call("browse_audio_effects", parent_id=parent_id, offset=offset)


@mcp.tool()
def load_audio_effect(track: int, item_id: str, expected_item_name: str,
                      expected_track_name: str, expected_device_names: list[str]) -> dict:
    """Append one browsed audio effect/preset to a regular track, requiring writes enabled.

    Inspect get_project/get_devices first and supply the current ordered device names.
    Exit hot-swap mode. Check returned load_id with get_device_load before continuing.
    Never retry pending/failed/unverified loads automatically: inspect Live first.
    Loading changes track selection temporarily. Save the set before experimenting;
    existing device snapshots cannot restore changed topology. Use Live Undo to remove
    an addition. Rack internals, instruments and third-party plugin browsing excluded.
    """
    return call("load_audio_effect", track=track, item_id=item_id,
                expected_item_name=expected_item_name, expected_track_name=expected_track_name,
                expected_device_names=expected_device_names)


@mcp.tool()
def get_device_load(load_id: str) -> dict:
    """Check delayed readback for an addition. Only completed + verified proves append.

    Pending means wait and check again. Failed/unverified means inspect the chain,
    do not retry automatically. IDs expire on bridge restart; no automatic rollback.
    """
    return call("get_device_load", load_id=load_id)


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


@mcp.tool()
def create_device_snapshot(track: int | None = None) -> dict:
    """Capture enabled top-level device parameters in Live memory.

    Omit track for regular/return/master devices, or pass a zero-based regular
    track index. Rack chains, mixer settings, automation, presets and plugin
    internals are excluded. Disabled parameters are skipped and counted. Maximum
    20 snapshots, 10000 parameter slots each; IDs expire on bridge/Live restart.
    """
    return call("create_device_snapshot", track=track)


@mcp.tool()
def restore_device_snapshot(snapshot_id: str) -> dict:
    """Restore a device snapshot, validating all targets before writing.

    Requires writes enabled. Rejects changed sets, tracks, devices, parameters or
    ranges. Changed parameters now disabled must be re-enabled manually first.
    Uses one undo step with readback verification and attempted rollback on
    failure. Automation can subsequently override restored values.
    """
    return call("restore_device_snapshot", snapshot_id=snapshot_id)


@mcp.tool()
def list_device_snapshots() -> list:
    """List available device snapshot IDs and scope for the current Live set."""
    return call("list_device_snapshots")


@mcp.tool()
def delete_device_snapshot(snapshot_id: str) -> dict:
    """Discard a device snapshot without changing Live parameters."""
    return call("delete_device_snapshot", snapshot_id=snapshot_id)


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
