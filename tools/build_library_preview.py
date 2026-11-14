"""Build the library preview from the verified author title-screen panorama."""
import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageOps, __version__ as pillow_version


PANORAMA = "config/fancymenu/panoramas/mypanorama/panorama/panorama_3.png"
TITLE = "config/fancymenu/customization/title_screen_layout.txt"
PROJECT = "https://www.curseforge.com/minecraft/modpacks/escape-the-end"
OUTPUT_SIZE = (1280, 720)


def pin(data):
    return {"sha256": hashlib.sha256(data).hexdigest(), "sizeBytes": len(data)}


def build(pack_source, verified_game):
    lock = json.loads((pack_source / "tools/upstream-client-lock.json").read_bytes())
    client = lock["client"]
    rows = {row["path"]: row for row in lock["overrides"]}
    source = verified_game / PANORAMA
    layout = verified_game / TITLE
    for relative, path in [(PANORAMA, source), (TITLE, layout)]:
        row = rows[relative]
        if pin(path.read_bytes()) != {key: row[key] for key in ("sha256", "sizeBytes")}:
            raise ValueError(f"File differs from the locked author archive: {relative}")
    title = layout.read_text(encoding="utf-8")
    if "background_type = panorama" not in title or "panorama_name = mypanorama" not in title:
        raise ValueError("Review the panorama after an author title-screen change")
    library = pack_source / "library"
    library.mkdir(exist_ok=True)
    output = library / "preview.jpg"
    with Image.open(source) as image:
        source_width, source_height = image.size
        preview = ImageOps.fit(image.convert("RGB"), OUTPUT_SIZE, Image.Resampling.LANCZOS)
        preview.save(output, "JPEG", quality=92, subsampling=0, optimize=True)
    receipt = {
        "schemaVersion": 1,
        "author": "Rainline",
        "authorArchiveSha256": client["sha256"],
        "projectUrl": PROJECT,
        "fileUrl": f"{PROJECT}/files/{client['fileId']}",
        "source": {
            "archivePath": rows[PANORAMA]["archivePath"],
            "references": [rows[TITLE]["archivePath"]],
            "width": source_width,
            "height": source_height,
            **pin(source.read_bytes()),
        },
        "preview": {
            "width": OUTPUT_SIZE[0],
            "height": OUTPUT_SIZE[1],
            **pin(output.read_bytes()),
        },
        "transformation": {
            "format": "JPEG",
            "method": "center crop and Lanczos resize",
            "optimize": True,
            "pillowVersion": pillow_version,
            "quality": 92,
            "subsampling": 0,
        },
    }
    (library / "preview-source.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n",
                                               encoding="utf-8", newline="\n")


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--pack-source", default=Path(__file__).resolve().parents[1], type=Path)
    cli.add_argument("--verified-game", required=True, type=Path)
    options = cli.parse_args()
    build(options.pack_source.resolve(strict=True), options.verified_game.resolve(strict=True))
