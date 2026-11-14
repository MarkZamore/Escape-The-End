"""Bind the reviewed Forge runtime's actual ModList output to the staged pack bytes."""

import argparse
import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from urllib.parse import unquote
from zipfile import ZipFile
import io


FORGE = {"type": "forge", "version": "47.4.10"}
STEAM_FILE = "e4steam-forge-mc1.18.2-1.20.2-v0.3.4-lanmc-native-ui.2.jar"


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_rows(receipt):
    rows = {}
    for line in receipt.read_text(encoding="ascii").splitlines():
        mod_id, name, version, filename, owner = (bytes.fromhex(col).decode() for col in line.split("\t"))
        if mod_id in rows or not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", mod_id) or not name or not version:
            raise ValueError(f"Invalid native inventory ID {mod_id}")
        rows[mod_id] = (name, version, filename, unquote(owner))
    if (rows["minecraft"][1] != "1.20.1" or rows["forge"][1] != FORGE["version"] or
            rows["e4steam"][2:] != (STEAM_FILE, f"mods/{STEAM_FILE}") or
            {"e4mc", "lanserverproperties"} & rows.keys()):
        raise ValueError("The actual runtime does not match the Steam candidate")
    return rows


def verified_jars(stage):
    lock = json.loads((stage / "intake.json").read_text(encoding="utf-8"))
    if lock["descriptor"]["loader"] != FORGE or lock["descriptor"]["minecraftVersion"] != "1.20.1":
        raise ValueError("The intake lock does not pin the reviewed Forge runtime")
    jars = {p.relative_to(stage / "game").as_posix(): digest(p)
            for p in (stage / "game/mods").glob("*.jar")}
    excluded = {item["path"] for item in lock["overlay"]["excludedDependencies"]}
    expected = {item["path"]: item["sha256"] for item in lock["files"]
                if item["path"].endswith(".jar") and item["path"] not in excluded}
    if jars != expected:
        raise ValueError("The staged JAR hashes differ from the intake lock")
    return jars


def owner_source(stage, jars, filename, owner):
    if owner in jars and PurePosixPath(owner).name == filename:
        return owner, ()
    parts = re.split(r"#\d+!/", owner)
    if len(parts) < 2 or parts[0] not in jars or PurePosixPath(parts[-1]).name != filename:
        raise ValueError(f"The native owner of {filename} does not identify a shipped JAR")
    with ZipFile(stage / "game" / parts[0]) as parent:
        data = parent.read(parts[1])
    for path in parts[2:]:
        with ZipFile(io.BytesIO(data)) as archive:
            data = archive.read(path)
    with ZipFile(io.BytesIO(data)) as archive:
        if "META-INF/mods.toml" not in archive.namelist():
            raise ValueError(f"The selected nested JAR {filename} does not declare a Forge mod")
    return parts[0], parts[1:]


def build_inventory(stage, receipt):
    jars = verified_jars(stage)
    mods = []
    for mod_id, (name, version, filename, owner) in sorted(read_rows(receipt).items()):
        if mod_id in ("minecraft", "forge", "e4steam"):
            continue
        source, nested = owner_source(stage, jars, filename, owner)
        row = {"id": mod_id, "name": name, "version": version, "source": source, "nativeSourceFile": filename}
        if nested:
            row["nativeNestedPaths"] = list(nested)
        mods.append(row)
    return {"schemaVersion": 1, "minecraftVersion": "1.20.1", "loader": FORGE,
            "upstreamLockSha256": digest(stage / "intake.json"), "sourceJars": jars, "mods": mods}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", type=Path)
    parser.add_argument("receipt", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    inventory = build_inventory(args.stage, args.receipt)
    args.output.write_text(json.dumps(inventory, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(f"Verified {len(inventory['mods'])} actual pack mod IDs against {len(inventory['sourceJars'])} shipped JARs")


if __name__ == "__main__":
    main()
