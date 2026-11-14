"""Create an isolated stock Forge view using the official profile and unchanged shared libraries."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile

VERSION = "1.20.1"
LOADER = "47.4.10"
PROFILE = f"{VERSION}-forge-{LOADER}"
INSTALLER_SHA = "1912760b4cb6b803d8a826de603c9076b1da71ec2765e9a1f8c1ca78f65278e3"
JAVA_SHA = "1977f302375adbb920d41dac65c7e22eb9c2ed8e1e8d6258964154ff16f14406"


def digest(path, algorithm="sha1"):
    with path.open("rb") as f:
        return hashlib.file_digest(f, algorithm).hexdigest()


def maven_path(gav):
    coord, _, ext = gav.partition("@")
    group, artifact, version, *classifiers = coord.split(":")
    basename = f"{artifact}-{version}" + (f"-{classifiers[0]}" if classifiers else "")
    return "/".join((group.replace(".", "/"), artifact, version, basename + "." + (ext or "jar")))


def stage(runtime: Path, shared: Path, java: Path):
    run = runtime.parent.resolve()
    if not (run / "RUN.json").is_file() or run.parent.name != "agent-runs" or runtime.is_junction() or runtime.is_symlink():
        raise ValueError("Expected the marked task directory")
    installer = runtime / "forge-installer.jar"
    if digest(installer, "sha256") != INSTALLER_SHA or digest(java, "sha256") != JAVA_SHA:
        raise ValueError("Unexpected Forge installer or Java bytes")

    with zipfile.ZipFile(installer) as z:
        profile_text = z.read("version.json")
        profile = json.loads(profile_text)
        metadata = json.loads(z.read("install_profile.json"))
        bin_patch = z.read(metadata["data"]["BINPATCH"]["client"].lstrip("/"))
    if profile["id"] != PROFILE or profile["inheritsFrom"] != VERSION:
        raise ValueError("Unexpected Forge client profile")
    base = json.loads((shared / f"versions/{VERSION}/{VERSION}.json").read_text(encoding="utf-8"))
    lib = runtime / "libraries"
    lib.mkdir(exist_ok=False)
    downloaded = []
    imported = []

    def put(info):
        relative = info["path"]
        if "\\" in relative or any(x in ("", ".", "..") for x in relative.split("/")):
            raise ValueError(f"Unsafe library path {relative}")
        target = lib / relative
        existing = shared / "libraries" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_file() and target.stat().st_size == info["size"] and digest(target) == info["sha1"]:
            return
        if (existing.is_file() and not existing.is_symlink() and
                existing.stat().st_size == info["size"] and digest(existing) == info["sha1"]):
            os.link(existing, target)
            imported.append(relative)
        else:
            if not info["url"].startswith(("https://maven.minecraftforge.net/", "https://libraries.minecraft.net/")):
                raise ValueError("Unexpected downloadable library URL")
            request = urllib.request.Request(info["url"], headers={"User-Agent": "LANMinecraft Native QA"})
            with urllib.request.urlopen(request, timeout=30) as stream:
                data = stream.read(info["size"] + 1)
            if len(data) != info["size"] or hashlib.sha1(data).hexdigest() != info["sha1"]:
                raise ValueError(f"Forge artifact did not match official checksum: {relative}")
            target.write_bytes(data)
            downloaded.append(relative)

    for row in base["libraries"]:
        downloads = row.get("downloads", {})
        if "artifact" in downloads:
            put(downloads["artifact"])
        for classifier, info in downloads.get("classifiers", {}).items():
            if classifier == "natives-windows":
                put(info)
    for row in [*metadata["libraries"], *profile["libraries"]]:
        put(row["downloads"]["artifact"])

    shared_client = shared / "libraries/net/minecraft/client/1.20.1-20230612.114412"
    names = {}
    for key in ("MC_SLIM", "MC_EXTRA", "MC_SRG", "PATCHED"):
        raw = metadata["data"][key]["client"].strip("[]")
        names[key] = maven_path(raw)
    for key in ("MC_SLIM", "MC_EXTRA", "MC_SRG"):
        path = lib / names[key]
        original = shared_client / path.name
        expected = metadata["data"].get(key + "_SHA", {}).get("client", "").strip("'")
        if expected and digest(original) != expected:
            raise ValueError(f"Invalid official shared generated {key}")
        path.parent.mkdir(parents=True, exist_ok=True)
        os.link(original, path)

    # The launcher's real adapter also preflights the mappings scoped to this version.
    for key in ("MOJMAPS", "MERGED_MAPPINGS"):
        relative = maven_path(metadata["data"][key]["client"].strip("[]"))
        original = shared / "libraries" / relative
        (lib / relative).parent.mkdir(parents=True, exist_ok=True)
        os.link(original, lib / relative)

    patch = runtime / "client.lzma"
    patch.write_bytes(bin_patch)
    binary_patch, = [p for p in metadata["processors"] if p["jar"] == "net.minecraftforge:binarypatcher:1.1.1"]
    classpath = os.pathsep.join(str(lib / maven_path(x)) for x in [binary_patch["jar"], *binary_patch["classpath"]])
    patched = lib / names["PATCHED"]
    with (runtime / "binarypatcher.log").open("wb") as console:
        subprocess.run([str(java), "-Xmx512m", f"-Djava.io.tmpdir={runtime}", "-cp", classpath,
                        "net.minecraftforge.binarypatcher.ConsoleTool", "--clean", str(lib / names["MC_SRG"]),
                        "--output", str(patched), "--apply", str(patch)],
                       cwd=runtime, stdout=console, stderr=subprocess.STDOUT, timeout=120, check=True,
                       creationflags=subprocess.BELOW_NORMAL_PRIORITY_CLASS | subprocess.CREATE_NO_WINDOW)
    expected = metadata["data"]["PATCHED_SHA"]["client"].strip("'")
    if digest(patched) != expected:
        raise ValueError("Binarypatched Forge client differs from its official SHA1")

    versions = runtime / "versions"
    base_dest = versions / VERSION
    base_dest.mkdir(parents=True)
    base_jar = shared / f"versions/{VERSION}/{VERSION}.jar"
    if digest(base_jar) != base["downloads"]["client"]["sha1"]:
        raise ValueError("Shared Mojang client checksum mismatch")
    os.link(base_jar, base_dest / base_jar.name)
    shutil.copyfile(shared / f"versions/{VERSION}/{VERSION}.json", base_dest / f"{VERSION}.json")
    profile_path = versions / PROFILE / (PROFILE + ".json")
    profile_path.parent.mkdir()
    profile_path.write_bytes(profile_text)
    print(json.dumps({"profile": PROFILE, "installerSha256": INSTALLER_SHA, "patchedSha1": expected,
                      "sharedLibraryFiles": len(imported), "downloadedLibraryFiles": downloaded}, indent=2))


if __name__ == "__main__":
    stage(*map(lambda x: Path(x).resolve(), sys.argv[1:]))
