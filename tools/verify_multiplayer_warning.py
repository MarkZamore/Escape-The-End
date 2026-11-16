"""Verify the packaged opt-out independently of the author's options file."""

import hashlib
import json
from pathlib import Path
import sys


PACK = Path(__file__).resolve().parents[1]
SCRIPT = "kubejs/client_scripts/lanmc_multiplayer_warning.js"
ASSIGNMENT = "Client.options.skipMultiplayerWarning = true"


def pin(data):
    return {"sizeBytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def verify(game):
    raw = (game / SCRIPT).read_bytes()
    statements = [line.strip() for line in raw.decode("utf-8").splitlines()
                  if line.strip() and not line.lstrip().startswith("//")]
    if statements != [ASSIGNMENT]:
        raise ValueError("Expected exactly the Minecraft multiplayer warning opt-out")
    lock = json.loads((PACK / "tools/upstream-lock.json").read_bytes())
    if {"path": SCRIPT, **pin(raw)} not in lock["overlay"]["clientScripts"]["files"]:
        raise ValueError("Packaged opt-out does not match its permanent overlay pin")
    if SCRIPT not in (game / "launcher/pack-owned.txt").read_text(encoding="utf-8").splitlines():
        raise ValueError("The pack must install the warning policy on existing profiles")
    options = game / "options.txt"
    original = next((row for row in lock["overrides"] if row["path"] == "options.txt"), None)
    actual = pin(options.read_bytes()) if options.is_file() else None
    expected = {key: original[key] for key in ("sizeBytes", "sha256")} if original else None
    if actual != expected:
        raise ValueError("The warning policy must preserve all author options byte for byte")
    print("Multiplayer warning opt-out verified in the staged pack")


if __name__ == "__main__":
    verify(Path(sys.argv[1]))
