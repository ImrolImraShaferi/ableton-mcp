"""Verify native capture, repeated capture, cancellation and state restoration in Live.

Run on a saved copy of a set, with the updated bridge loaded. Uses localhost UDP;
does not operate the UI or save the Live set. Retains capture track/clips/files.
"""
import argparse
import ast
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ableton_mcp.client import BridgeClient
from ableton_mcp.resampling import finish_capture


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("installed_folder", type=Path)
    parser.add_argument("--start-beats", type=float, default=32)
    parser.add_argument("--length-beats", type=float, default=4)
    parser.add_argument("--native-only", action="store_true", help="Test Live lifecycle without archiving locked recordings")
    args = parser.parse_args()
    # Read the configured literal token without executing config or printing it.
    tree = ast.parse((args.installed_folder / "config.py").read_text(encoding="utf-8"))
    token = next(ast.literal_eval(node.value) for node in tree.body
                 if isinstance(node, ast.Assign) and any(
                     isinstance(target, ast.Name) and target.id == "TOKEN" for target in node.targets))
    client = BridgeClient(token)
    def call(method, **params):
        return client.call(method, **params)
    baseline = call("get_project")
    track = call("prepare_resampling_track")
    print("Capture track:", json.dumps(track), flush=True)
    captures = []
    active = None
    try:
        for _ in range(2):
            job = call("start_resampling_capture", track=track["track"],
                       expected_track_name=track["track_name"], start_beats=args.start_beats,
                       length_beats=args.length_beats)
            active = job["capture_id"]
            print("Started:", json.dumps(job), flush=True)
            deadline = time.monotonic() + 135
            while job["status"] in ("recording", "cleaning") and time.monotonic() < deadline:
                time.sleep(0.5)
                job = call("get_resampling_capture", capture_id=active)
            print("Capture result:", json.dumps(job), flush=True)
            if job["status"] != "completed":
                raise RuntimeError("Capture failed: " + json.dumps(job))
            if args.native_only:
                active = None
                continue
            try:
                result = finish_capture(active, call)
            except ValueError as exc:
                if "Native audio crop requested" not in str(exc):
                    raise
                deadline = time.monotonic() + 35
                while time.monotonic() < deadline:
                    job = call("get_resampling_capture", capture_id=active)
                    if job["status"] != "finalizing":
                        break
                    time.sleep(0.5)
                result = finish_capture(active, call)
            active = None
            captures.append(result)
            print("Archived:", json.dumps({"output_path": result["output_path"],
                "duration_seconds": result["analysis"]["duration_seconds"],
                "sample_rate_hz": result["analysis"]["sample_rate_hz"],
                "rms_dbfs": result["analysis"]["rms_dbfs"]}), flush=True)
            if result["analysis"]["rms_dbfs"] is None:
                raise RuntimeError("Captured passage is silent; choose an audible range")
        job = call("start_resampling_capture", track=track["track"],
                   expected_track_name=track["track_name"], start_beats=args.start_beats,
                   length_beats=args.length_beats * 2)
        active = job["capture_id"]
        time.sleep(0.5)
        cancelled = call("cancel_resampling_capture", capture_id=active)
        deadline = time.monotonic() + 10
        while cancelled["status"] == "cleaning" and time.monotonic() < deadline:
            time.sleep(0.5)
            cancelled = call("get_resampling_capture", capture_id=active)
        if cancelled["status"] != "cancelled" or cancelled["cleanup_errors"]:
            raise RuntimeError("Cancellation failed: " + json.dumps(cancelled))
        active = None
        after = call("get_project")
        before_transport = dict(baseline["transport"])
        after_transport = dict(after["transport"])
        before_position = before_transport.pop("position_beats")
        after_position = after_transport.pop("position_beats")
        if before_transport != after_transport or abs(before_position - after_position) > 0.0001 or after["playing"]:
            raise RuntimeError("Transport state was not restored")
        if baseline["tracks"] != after["tracks"][:len(baseline["tracks"])]:
            raise RuntimeError("Original track mixer/arming state changed")
        if captures and abs(captures[0]["analysis"]["duration_seconds"] - captures[1]["analysis"]["duration_seconds"]) > 0.1:
            raise RuntimeError("Repeated capture durations differ")
        print("PASS: native recording, cancellation and original state restored" if args.native_only else
              "PASS: two non-silent stereo captures, repeat duration, cancellation, and original state restored", flush=True)
    finally:
        if active:
            print("Cleanup:", json.dumps(call("cancel_resampling_capture", capture_id=active)), flush=True)


if __name__ == "__main__":
    main()
