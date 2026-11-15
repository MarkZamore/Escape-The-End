"""Check coverage and physical key uniqueness without launching Minecraft."""

import argparse
from pathlib import Path
import re
from zipfile import ZipFile

from build_preset import HERE, PRESET, preset_bytes, read_mappings


def check(author_options=None):
    author = read_mappings(HERE / "author-keys.txt")
    if author_options is not None:
        lines = author_options.decode("utf-8").splitlines()
        options = [line.split(":", 1) for line in lines if line.startswith("key_")]
        if len(options) != len(author) or dict(options) != author:
            raise ValueError("The pinned author options no longer match the reviewed key mappings")
    if PRESET.read_bytes() != preset_bytes():
        raise ValueError("Controls preset was not rebuilt from reviewed inputs")
    mappings = read_mappings(PRESET)
    registered = [
        line for line in (HERE / "registered-keys.txt").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]
    if len(registered) != len(mappings) or mappings.keys() != set(registered):
        raise ValueError("The preset differs from the recorded game registration")
    physical_keys = {}
    for name, value in mappings.items():
        match = re.fullmatch(r"(key\.(?:keyboard|mouse)\.[a-z0-9.]+)(?::(ALT|CONTROL|SHIFT))?", value)
        if not match:
            raise ValueError(f"Invalid Forge key mapping: {name} = {value}")
        physical, modifier = match.groups()
        if physical == "key.keyboard.unknown" and modifier is None:
            continue
        # Forbid a shared physical key even when contexts or modifiers differ.
        if physical in physical_keys or physical == "key.keyboard.unknown":
            raise ValueError(f"Conflicting binding: {name} and {physical_keys.get(physical)} on {physical}")
        physical_keys[physical] = name
    print(f"{len(mappings)} registered mappings, {len(physical_keys)} unique physical bindings")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--author-options", type=Path, help="Options extracted from the pinned author ZIP")
    source.add_argument("--author-archive", type=Path, help="Pinned author ZIP")
    args = parser.parse_args()
    if args.author_archive:
        with ZipFile(args.author_archive) as archive:
            check(archive.read("overrides/options.txt"))
    else:
        check(args.author_options.read_bytes() if args.author_options else None)
