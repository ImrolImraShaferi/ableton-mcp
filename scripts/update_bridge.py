"""Update an existing bridge without replacing its token or write preference."""
import argparse
from pathlib import Path
import shutil

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("installed_folder", type=Path, help="Full path to the installed AbletonMCP folder")
args = parser.parse_args()
target = args.installed_folder.resolve()
if target.name != "AbletonMCP" or not (target / "config.py").is_file():
    raise SystemExit("Expected an existing AbletonMCP folder containing config.py")
source = Path(__file__).resolve().parents[1] / "remote_script" / "AbletonMCP"
if target == source:
    raise SystemExit("Target must be the installed or prepared bridge, not source")
for name in ("bridge.py", "__init__.py"):
    shutil.copy2(source / name, target / name)
print("Bridge updated; config.py preserved. Restart Ableton Live.")
