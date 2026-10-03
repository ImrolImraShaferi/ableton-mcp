"""Prepare an installable bridge inside this workspace, without modifying Live."""
from pathlib import Path
import secrets
import shutil

root = Path(__file__).resolve().parents[1]
target = root / "build" / "AbletonMCP"
if target.exists():
    raise SystemExit("build/AbletonMCP already exists; preserve its token or remove it manually before regenerating")
shutil.copytree(root / "remote_script" / "AbletonMCP", target)
token = secrets.token_hex(32)
(target / "config.py").write_text('TOKEN = "' + token + '"\nALLOW_WRITES = False\n', encoding="utf-8")
(root / "build" / "token.txt").write_text(token, encoding="utf-8")
print("Prepared build/AbletonMCP. Secret stored in build/token.txt; do not share it.")
