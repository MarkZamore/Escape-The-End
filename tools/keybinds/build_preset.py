"""Build the Escape The End controls preset from Rainline's pinned mappings."""

from pathlib import Path

HERE = Path(__file__).resolve().parent
PRESET = HERE.parents[1] / "launcher/controls-preset.txt"
CHANGES = {
    "key_key.inventorysorter.sort": "key.keyboard.semicolon",
    "key_key.advancements": "key.keyboard.l",
    "key_key.socialInteractions": "key.keyboard.p",
    "key_key.saveToolbarActivator": "key.keyboard.f7",
    "key_key.loadToolbarActivator": "key.keyboard.f8",
}


def read_mappings(path):
    mappings = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        name, value = line.split(":", 1)
        if not name.startswith("key_") or name in mappings:
            raise ValueError(f"Invalid or repeated mapping in {path}: {name}")
        mappings[name] = value
    return mappings


def preset_bytes():
    author = read_mappings(HERE / "author-keys.txt")
    if not CHANGES.keys() <= author.keys():
        raise ValueError("An audited action is absent from the author mapping list")
    lines = ["# Escape The End controls preset"] + [
        f"{name}:{value}" for name, value in sorted((author | CHANGES).items())
    ]
    return ("\n".join(lines) + "\n").encode("utf-8")


if __name__ == "__main__":
    PRESET.write_bytes(preset_bytes())
