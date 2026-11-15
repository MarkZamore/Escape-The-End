"""Publish a verified pack stage and retain assets for clients using older manifests."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess


def gh(*args):
    return subprocess.check_output(["gh", *args], text=True)


def api(endpoint, *args):
    return json.loads(gh("api", endpoint, *args))


def fingerprint(path):
    with path.open("rb") as stream:
        return path.stat().st_size, "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest()


def verify_asset(asset, pin):
    if (asset["state"], asset["size"], asset["digest"]) != ("uploaded", *pin):
        raise RuntimeError(f"Release asset has different bytes: {asset['name']}")


def publish(release_dir, notes, notices, repository, revision):
    base = f"repos/{repository}"
    tag = "pack-latest"
    canonical = "pack-manifest.json"
    head = f"{base}/git/refs/tags/{tag}"
    if json.loads((release_dir / canonical).read_bytes())["revision"] != revision:
        raise RuntimeError("Staged manifest differs from the requested source revision")

    def find_release():
        pages = api(f"{base}/releases?per_page=100", "--paginate", "--slurp")
        return next((row for page in pages for row in page if row["tag_name"] == tag), None)

    current = find_release()
    if current is None:
        gh("release", "create", tag, "--repo", repository, "--target", revision,
           "--draft", "--title", "Escape The End", "--notes-file", str(notes))
        current = find_release()
    if current is None or current["immutable"]:
        raise RuntimeError("The release must exist and allow updates")
    if current["draft"] and current["target_commitish"] != revision:
        raise RuntimeError("The draft belongs to a different source revision")
    assets_endpoint = f"{base}/releases/{current['id']}/assets?per_page=100"

    def list_assets():
        return {asset["name"]: asset for page in api(assets_endpoint, "--paginate", "--slurp") for asset in page}

    def rename(asset, name):
        renamed = api(f"{base}/releases/assets/{asset['id']}", "-X", "PATCH", "-f", f"name={name}")
        if renamed["name"] != name:
            raise RuntimeError(f"GitHub did not rename asset to {name}")

    def point_tag(sha):
        ref = api(head, "-X", "PATCH", "-f", f"sha={sha}", "-F", "force=true")["object"]
        if ref["type"] != "commit" or ref["sha"] != sha:
            raise RuntimeError("GitHub did not update the release tag")

    published_revision = None
    existing = list_assets()
    backup = None
    manifest = release_dir / canonical
    manifest_pin = fingerprint(manifest)
    if not current["draft"]:
        ref = api(head)["object"]
        if ref["type"] != "commit":
            raise RuntimeError("Expected the release tag to point directly to a commit")
        published_revision = ref["sha"]
        backup_name = f"pack-manifest-{published_revision}.json"
        backup = existing.get(backup_name)
        if canonical not in existing:
            if backup is None:
                raise RuntimeError("The public release has no manifest")
            rename(backup, canonical)
            existing = list_assets()
            backup = None
        if backup is not None and existing[canonical]["digest"] != manifest_pin[1]:
            recovered_name = f"pack-manifest-recovered-{existing[canonical]['id']}.json"
            rename(existing[canonical], recovered_name)
            rename(backup, canonical)
            existing = list_assets()
            backup = None
        if published_revision == revision:
            verify_asset(existing[canonical], manifest_pin)

    notice_dir = release_dir.parent / "public-notices"
    notice_dir.mkdir(exist_ok=True)
    for notice in notices:
        pin = fingerprint(notice)
        name = notice.name
        if name in existing and existing[name]["digest"] != pin[1]:
            name = f"{pin[1][7:19]}-{name}"
        shutil.copyfile(notice, notice_dir / name)

    pending_name = f"pack-manifest-candidate-{revision}.json"
    if not current["draft"] and existing[canonical]["digest"] != manifest_pin[1]:
        manifest = release_dir.parent / pending_name
        shutil.copyfile(release_dir / canonical, manifest)

    desired = [*sorted((release_dir / "assets").iterdir()), *sorted(notice_dir.iterdir()), manifest]
    desired_pins = {path.name: fingerprint(path) for path in desired}
    missing = []
    for path in desired:
        if path.name in existing:
            verify_asset(existing[path.name], desired_pins[path.name])
        else:
            missing.append(path)
    if missing:
        gh("release", "upload", tag, "--repo", repository, *map(str, missing))
    existing = list_assets()
    for name, pin in desired_pins.items():
        verify_asset(existing[name], pin)
    print(f"Verified {len(desired_pins)} release files ({len(missing)} uploaded).", flush=True)

    if published_revision is not None and published_revision != revision:
        previous = backup or existing[canonical]
        candidate = existing[manifest.name]
        if api(head)["object"]["sha"] != published_revision:
            raise RuntimeError("The public release tag changed during asset preparation")
        try:
            if candidate["name"] != canonical:
                rename(previous, backup_name)
                rename(candidate, canonical)
            point_tag(revision)
        except BaseException:
            after = list_assets()
            if canonical in after and after[canonical]["id"] == candidate["id"] and backup_name in after:
                rename(candidate, pending_name)
            if backup_name in after:
                rename(previous, canonical)
            if api(head)["object"]["sha"] != published_revision:
                point_tag(published_revision)
            raise

    gh("release", "edit", tag, "--repo", repository, "--target", revision,
       "--draft=false", "--notes-file", str(notes))
    print(f"Published {tag} at {revision}.")


def interrupted(signum, _frame):
    raise KeyboardInterrupt(f"Release interrupted by signal {signum}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("release", type=Path)
    parser.add_argument("notes", type=Path)
    parser.add_argument("notices", type=Path, nargs="+")
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, interrupted)
    publish(args.release, args.notes, args.notices, os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_SHA"])


if __name__ == "__main__":
    main()
