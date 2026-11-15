"""Verify public CurseForge pack assets with one bounded temporary archive."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import stat
import sys
from tempfile import TemporaryFile
from urllib.error import URLError
from urllib.parse import quote
import zipfile

import prepare

sys.path.insert(0, str(prepare.ROOT / "tools"))
from pack_library import is_https_project_page


ASSET_ORIGINS = {
    "github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com",
}
SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
SHA256 = re.compile(r"[0-9a-f]{64}")
DESCRIPTOR = "portable-pack.json"


def pin(row, limit=prepare.MAX_FILE, *, minimum=1):
    size, digest = row["sizeBytes"], row["sha256"]
    prepare.require(type(size) is int and minimum <= size <= limit and
                    isinstance(digest, str) and SHA256.fullmatch(digest), "Invalid asset or file pin")
    return {"sizeBytes": size, "sha256": digest}


def asset_name(value):
    prepare.require(isinstance(value, str) and SLUG.fullmatch(value), f"Invalid asset name: {value!r}")
    return value


def exact(source, expected, output=None):
    prepare.exact_bytes(source, expected["sizeBytes"], output, {"sha256": expected["sha256"]})


def pinned_json(source, expected):
    prepare.require(expected["sizeBytes"] <= prepare.MAX_JSON, "Oversized pinned JSON")
    data = source.read(expected["sizeBytes"] + 1)
    prepare.require(prepare.fingerprint(data) == expected, "Pinned JSON has different bytes")
    return prepare.decode_json(data)


def members(archive, expected):
    expected = set(expected)
    entries = archive.infolist()
    prepare.require(len(entries) == len(expected) and {entry.filename for entry in entries} == expected,
                    "ZIP members differ from the approved inventory")
    for entry in entries:
        prepare.safe_path(entry.filename)
        mode = stat.S_IFMT(entry.external_attr >> 16)
        prepare.require(not entry.is_dir() and mode in (0, stat.S_IFREG) and
                        (entry.flag_bits & 1) == 0 and entry.file_size <= prepare.MAX_FILE,
                        f"Invalid ZIP member: {entry.filename}")
    return entries


def verify_archive(archive, covers, files):
    descriptor = None
    for entry in members(archive, covers):
        expected = files[entry.filename]
        prepare.require(entry.file_size == expected["sizeBytes"], f"ZIP member length: {entry.filename}")
        with archive.open(entry) as stream:
            if entry.filename == DESCRIPTOR:
                descriptor = pinned_json(stream, expected)
            else:
                try:
                    exact(stream, expected)
                except ValueError as error:
                    raise ValueError(f"{entry.filename}: {error}") from error
    return descriptor


def verify_library(archive, manifest, pack_path):
    for entry in members(archive, {"library.json", "preview.jpg"}):
        prepare.require(entry.file_size <= prepare.MAX_JSON, f"Oversized library member: {entry.filename}")
    card = prepare.decode_json(archive.read("library.json"))
    preview = archive.read("preview.jpg")
    prepare.require(preview.startswith(b"\xff\xd8\xff") and preview.endswith(b"\xff\xd9"), "Invalid preview JPEG")
    prepare.require(card["id"] == manifest["packId"] and card["revision"] == manifest["revision"] and
                    card["relativePath"] == pack_path and card["minecraftVersion"] == manifest["minecraftVersion"] and
                    card["loader"] == manifest["loader"]["type"], "Library metadata differs from the approved pack")
    mods = card["mods"]
    prepare.require(len({mod["id"] for mod in mods}) == len(mods) and
                    sum(mod["isLoadedMod"] is True for mod in mods) == manifest["modCount"] and
                    all(type(mod["isLoadedMod"]) is bool and is_https_project_page(mod.get("websiteUrl"))
                        for mod in mods), "Library mods, loaded count or links differ")
    return prepare.fingerprint(preview)


def public_api(endpoint):
    with prepare.open_official(f"https://api.github.com/{endpoint}", {"api.github.com"},
                               "application/vnd.github+json") as response:
        return prepare.decode_json(response.read(prepare.MAX_JSON + 1))


def release_assets(repository, tag, revision):
    base = f"repos/{repository}"
    release = public_api(f"{base}/releases/tags/{tag}")
    ref = public_api(f"{base}/git/ref/tags/{tag}")["object"]
    prepare.require(release["draft"] is False and release["tag_name"] == tag and
                    ref["type"] == "commit" and ref["sha"] == revision, "Public release tag differs from approved commit")
    assets = {}
    for page in range(1, 41):
        rows = public_api(f"{base}/releases/{release['id']}/assets?per_page=100&page={page}")
        for row in rows:
            name = asset_name(row["name"])
            prepare.require(name.casefold() not in assets and row["state"] == "uploaded", "Invalid public asset inventory")
            assets[name.casefold()] = row
        if len(rows) < 100:
            return {row["name"]: row for row in assets.values()}
    raise ValueError("Release has more than 3999 assets")


def verify(manifest, manifest_name, manifest_pin, extras, public, receive, scratch, pack_path, limit,
           *, allow_previous_assets=False):
    descriptor = None
    files = {}
    paths = {}
    for row in manifest["files"]:
        path = prepare.safe_path(row["path"])
        prepare.reserve_path(paths, path)
        files[path.as_posix()] = pin(row, minimum=0)
    prepare.require(DESCRIPTOR in files, "Release does not carry its portable runtime descriptor")
    pending = set(files)
    game_assets = {}
    for row in manifest["assets"]:
        name = asset_name(row["name"])
        covers = row["covers"]
        prepare.require(isinstance(covers, list) and covers and len(set(covers)) == len(covers) and
                        set(covers) <= pending and name not in game_assets and row["kind"] in {"jar", "zipRoot"},
                        "Invalid, duplicate or uncovered game asset")
        pending.difference_update(covers)
        game_assets[name] = pin(row)
        if row["kind"] == "jar":
            prepare.require(len(covers) == 1 and game_assets[name] == files[covers[0]],
                            f"Direct asset differs from its covered payload: {name}")
    prepare.require(not pending, "Game asset inventory does not cover all approved files")

    library = manifest["library"]
    library_name = asset_name(library["name"])
    expected = {manifest_name: manifest_pin, library_name: pin(library, prepare.MAX_JSON), **game_assets, **extras}
    if allow_previous_assets:
        # Clients holding an earlier manifest can still fetch its content-addressed assets.
        public = {name: row for name, row in public.items() if name in expected}
    prepare.require(len({name.casefold() for name in expected}) == len(game_assets) + len(extras) + 2 and
                    set(public) == set(expected), "Public release asset set differs from the approved inventory")
    for name, expected_pin in expected.items():
        info = public[name]
        prepare.require(info["size"] == expected_pin["sizeBytes"] and
                        info.get("digest") == "sha256:" + expected_pin["sha256"], f"Public asset metadata differs: {name}")
    largest = max([expected[library_name]["sizeBytes"],
                   *(row["sizeBytes"] for row in manifest["assets"] if row["kind"] == "zipRoot")])
    prepare.require(largest <= limit, f"An archive requires {largest} temporary bytes; limit is {limit}")

    with receive(manifest_name) as stream:
        exact(stream, manifest_pin)
    for name, expected_pin in extras.items():
        with receive(name) as stream:
            exact(stream, expected_pin)
    with TemporaryFile(dir=scratch) as temporary, receive(library_name) as stream:
        exact(stream, expected[library_name], temporary)
        temporary.seek(0)
        with zipfile.ZipFile(temporary) as archive:
            preview = verify_library(archive, manifest, pack_path)
    for row in manifest["assets"]:
        name = row["name"]
        try:
            with receive(name) as stream:
                if row["kind"] == "zipRoot":
                    with TemporaryFile(dir=scratch) as temporary:
                        exact(stream, game_assets[name], temporary)
                        temporary.seek(0)
                        with zipfile.ZipFile(temporary) as archive:
                            descriptor = verify_archive(archive, row["covers"], files) or descriptor
                elif row["covers"] == [DESCRIPTOR]:
                    descriptor = pinned_json(stream, game_assets[name])
                else:
                    exact(stream, game_assets[name])
        except (ValueError, zipfile.BadZipFile) as error:
            raise ValueError(f"{name}: {error}") from error
    prepare.require(descriptor is not None and descriptor["minecraftVersion"] == manifest["minecraftVersion"] and
                    descriptor["loader"] == manifest["loader"], "Public portable runtime differs from the approved runtime")
    return {"outcome": "PASS", "revision": manifest["revision"], "manifestSha256": manifest_pin["sha256"],
            "librarySha256": library["sha256"], "previewSha256": preview["sha256"],
            "gameAssets": len(game_assets), "gameAssetBytes": sum(row["sizeBytes"] for row in game_assets.values()),
            "verifiedFiles": len(files), "verifiedFileBytes": sum(row["sizeBytes"] for row in files.values()),
            "additionalAssets": extras, "maximumTemporaryArchiveBytes": largest, "runtime": descriptor}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-manifest", type=Path, required=True)
    parser.add_argument("--repository", required=True, metavar="OWNER/REPOSITORY")
    parser.add_argument("--tag", default="pack-latest")
    parser.add_argument("--pack-path", required=True)
    parser.add_argument("--scratch", type=Path, required=True, help="Owned run directory for one temporary archive")
    parser.add_argument("--max-temp-bytes", type=int, default=128 * 1024 * 1024)
    parser.add_argument("--extra-asset", type=Path, action="append", default=[], help="Approved extra public asset")
    parser.add_argument("--allow-previous-assets", action="store_true",
                        help="Verify every asset for the selected revision while retaining previous assets")
    args = parser.parse_args()
    try:
        repository = "/".join(asset_name(part) for part in args.repository.split("/"))
        prepare.require(repository.count("/") == 1, "Use an OWNER/REPOSITORY pair")
        tag = asset_name(args.tag)
        pack_path = prepare.safe_path(args.pack_path)
        prepare.require(len(pack_path.parts) == 1, "Expected a single pack folder")
        scratch = args.scratch.absolute()
        prepare.no_links(scratch)
        run = next((path for path in (scratch, *scratch.parents)
                    if path.parent.name == "agent-runs" and path.parent.parent.name == "artifacts"), None)
        prepare.require(run is not None and scratch.is_dir(), "Scratch must be inside an existing agent run")
        ownership = prepare.read_local_json(run / "RUN.json")
        prepare.require(isinstance(ownership.get("owner"), str) and ownership["owner"].strip(), "Scratch has no owner")
        raw_manifest = prepare.overlay_bytes(args.expected_manifest)
        manifest = prepare.decode_json(raw_manifest)
        prepare.require(manifest["schemaVersion"] == 2 and re.fullmatch(r"[0-9a-f]{40}", manifest["revision"]),
                        "Expected a v2 manifest with a full source revision")
        extras = {}
        for path in args.extra_asset:
            name = asset_name(path.name)
            prepare.require(name not in extras, f"Duplicate approved extra: {name}")
            extras[name] = prepare.fingerprint(prepare.overlay_bytes(path))
        public = release_assets(repository, tag, manifest["revision"])

        def receive(name):
            url = f"https://github.com/{repository}/releases/download/{tag}/{quote(name, safe='')}"
            return prepare.open_official(url, ASSET_ORIGINS, "application/octet-stream")

        receipt = verify(manifest, "pack-manifest.json", prepare.fingerprint(raw_manifest), extras,
                         public, receive, scratch, pack_path.as_posix(), args.max_temp_bytes,
                         allow_previous_assets=args.allow_previous_assets)
        print(json.dumps(receipt, ensure_ascii=False, indent=2))
    except (OSError, URLError, ValueError, KeyError, TypeError, RuntimeError, zipfile.BadZipFile) as error:
        parser.exit(1, f"{parser.prog}: {error}\n")


if __name__ == "__main__":
    main()
