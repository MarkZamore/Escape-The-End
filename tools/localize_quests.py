"""Generate and verify the Escape The End FTB Quest localization overlay."""

import argparse
import hashlib
import json
from pathlib import Path
import re
from zipfile import ZipFile


PACK = Path(__file__).resolve().parents[1]
TOOLS = PACK / "tools"
QUESTS = "config/ftbquests/quests/"
LANG = "kubejs/assets/escape_end_quests/lang/"
QUEST_FILE = re.compile(r"(?:data|chapter_groups|chapters/[A-Za-z0-9_.-]+)\.snbt")
STRING = re.compile(r'"(?:[^"\\\r\n]|\\.)*"')
QUOTED = STRING.pattern
FIELDS = re.compile(
    rf'{QUOTED}|(?<![\w.-])(?:title|subtitle|description|text)\s*:\s*'
    rf'(?P<values>{QUOTED}|\[\s*(?:{QUOTED}\s*,?\s*)*\])?'
)
MARKUP = re.compile(r"[&§][0-9a-fk-or]|https?://[^\s\"<>]+|\{[^{}]+\}", re.IGNORECASE)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def load(path):
    return json.loads(path.read_bytes(), object_pairs_hook=unique_object)


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def fingerprint(data):
    return {"sizeBytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def pin(row):
    return {key: row[key] for key in ("sizeBytes", "sha256")}


def quest_sources(archive):
    prefix = "overrides/" + QUESTS
    sources = {}
    with ZipFile(archive) as client:
        for member in client.infolist():
            if not member.filename.startswith(prefix) or member.is_dir():
                continue
            path = member.filename.removeprefix(prefix)
            if not QUEST_FILE.fullmatch(path) or path in sources:
                raise ValueError(f"Review unsupported or repeated quest input: {path}")
            sources[path] = client.read(member)
    if not {"data.snbt", "chapter_groups.snbt"} <= sources.keys() or len(sources) < 3:
        raise ValueError("Expected the complete author FTB questbook")
    return dict(sorted(sources.items()))


def text_tokens(source):
    for field in FIELDS.finditer(source):
        if field.group().startswith('"'):
            continue
        if field.group("values") is None:
            raise ValueError(f"Unsupported quest text field at character {field.start()}")
        for token in STRING.finditer(source, *field.span("values")):
            english = json.loads(token.group())
            if english and english != "{@pagebreak}":
                yield token.start(), token.end(), english


def translation_tables():
    tables = {}
    for path in sorted((TOOLS / "quest-localization").glob("*.json")):
        for source, entries in load(path).items():
            if source in tables:
                raise ValueError(f"More than one translation table for {source}")
            tables[source] = entries
    return tables


def rewrite(path, data, table, locales):
    source = data.decode("utf-8")
    tokens = list(text_tokens(source))
    required = {english for _, _, english in tokens}
    if required != table.keys():
        raise ValueError(f"{path}: missing {sorted(required - table.keys())}, "
                         f"obsolete {sorted(table.keys() - required)}")

    # Apply from the end, so indices into the untouched prefix stay valid.
    # Expand a repeated literal leftward by complete lines until it is unique.
    replacements = []
    for start, end, english in reversed(tokens):
        russian = table[english]
        if not isinstance(russian, str) or not russian.strip() or MARKUP.findall(russian) != MARKUP.findall(english):
            raise ValueError(f"Invalid translation or changed markup in {path}: {english}")
        if english.lstrip().startswith(('{"', '["', '[{')):
            raise ValueError(f"Review structured FTB component in {path}: {english}")
        chapter = Path(path).stem.lower()
        key = f"lanminecraft.escape_end_quests.{chapter}.{hashlib.sha256(english.encode()).hexdigest()[:20]}"
        for locale, value in zip(locales, (english, russian), strict=True):
            # FTB Library expands {key} via I18n, which interprets percent signs.
            value = value.replace("%", "%%")
            if locale.setdefault(key, value) != value:
                raise ValueError(f"Translation key collision: {key}")
        context = start
        while source.count(source[context:end]) != 1:
            if context == 0:
                raise ValueError(f"Could not identify a unique patch in {path}")
            context = source.rfind("\n", 0, context - 1) + 1
        old = source[context:end]
        new = source[context:start] + json.dumps("{" + key + "}")
        replacements.append({"from": old, "to": new})
        source = source[:context] + new + source[end:]
    return source.encode("utf-8"), replacements


def generate(archive, sources):
    lock_path = TOOLS / "upstream-lock.json"
    recipe_path = TOOLS / "overlay.json"
    owner_path = PACK / "launcher/pack-owned.txt"
    lock, recipe = load(lock_path), load(recipe_path)
    if fingerprint(archive.read_bytes()) != pin(lock["client"]) or (
        recipe["authorArchiveSha256"] != lock["client"]["sha256"]
    ):
        raise ValueError("Pin the exact author archive in the author lock and overlay before building")
    inputs = {QUESTS + path: fingerprint(data) for path, data in sources.items()}
    if inputs != {row["path"]: pin(row) for row in lock["overrides"] if row["path"].startswith(QUESTS)}:
        raise ValueError("Author quest inventory differs from the complete lock")
    tables = translation_tables()
    if tables.keys() != sources.keys():
        raise ValueError(f"Assign chapter translations: missing {sorted(sources.keys() - tables.keys())}, "
                         f"obsolete {sorted(tables.keys() - sources.keys())}")
    item_names = load(TOOLS / "quest-item-names.json")
    runtime_dependencies = {
        row["path"] for row in lock["files"]
        if re.fullmatch(r"mods/(?:ftb-quests|ftb-library|kubejs)-forge-.*\.jar", row["path"])
    }
    dependencies = {
        row["path"]: {**pin(row), "projectId": row["projectId"], "fileId": row["fileId"]}
        for row in lock["files"]
        if row["path"] in runtime_dependencies or row["path"] in item_names
    }
    if len(runtime_dependencies) != 3 or not item_names.keys() <= dependencies.keys():
        raise ValueError("Expected exact FTB Quests, FTB Library, KubeJS and item mod dependency pins")

    locales = {}, {}
    quest_outputs = {}
    patches = [row for row in recipe["patches"] if not row["path"].startswith(QUESTS)]
    owned = set(owner_path.read_text(encoding="utf-8").splitlines())
    owned = {path for path in owned if not path.startswith((QUESTS, LANG))}
    for path, data in sources.items():
        target = QUESTS + path
        rewritten, replacements = rewrite(path, data, tables[path], locales)
        if replacements:
            patches.append({"path": target, "before": inputs[target], "after": fingerprint(rewritten),
                            "replacements": replacements})
            quest_outputs[target] = rewritten
            owned.add(target)
    recipe["patches"] = patches
    count = sum(len(row["replacements"]) for row in patches if row["path"] in quest_outputs)
    print(f"{len(sources) - 2} chapters, {count} text fields, {len(locales[1])} unique quest locale entries")
    for path, names in item_names.items():
        for key, value in names.items():
            if (not re.fullmatch(r"(?:item|block)\.[a-z0-9_.]+", key) or key in locales[1] or
                    not isinstance(value, str) or not value.strip()):
                raise ValueError(f"Invalid item translation in {path}: {key}")
            locales[1][key] = value
    entries = [row for row in recipe.get("bookLocalization", []) if not row["path"].startswith(LANG)]
    outputs = {}
    for locale, translations in zip(("en_us", "ru_ru"), locales, strict=True):
        path = LANG + locale + ".json"
        data = json_bytes(translations)
        outputs[path] = data
        entries.append({"path": path, "inputs": inputs | dependencies, "output": fingerprint(data)})
        owned.add(path)
    recipe["bookLocalization"] = entries
    recipe_bytes = json_bytes(recipe)
    ownership = ("\n".join(sorted(owned)) + "\n").encode()
    lock["overlay"].update(
        patches=[{key: row[key] for key in ("path", "before", "after")} for row in patches],
        bookLocalization=entries,
        ownership=fingerprint(ownership),
        recipeSha256=fingerprint(recipe_bytes)["sha256"],
    )
    outputs.update({
        recipe_path.relative_to(PACK).as_posix(): recipe_bytes,
        owner_path.relative_to(PACK).as_posix(): ownership,
        lock_path.relative_to(PACK).as_posix(): json_bytes(lock),
    })
    print(f"{len(locales[1]) - len(locales[0])} extra item names used by quest tasks")
    return outputs, quest_outputs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("review", "build", "verify", "render"))
    parser.add_argument("archive", type=Path)
    parser.add_argument("--game", type=Path, help="Existing disposable QA game directory for render")
    args = parser.parse_args()
    sources = quest_sources(args.archive)
    if args.action == "review":
        tables = translation_tables()
        changes = {}
        for path in sorted(sources.keys() | tables.keys()):
            new = {value for _, _, value in text_tokens(sources.get(path, b"").decode("utf-8"))}
            old = tables.get(path, {})
            if new != old.keys() or path not in sources or path not in tables:
                changes[path] = {"missing": sorted(new - old.keys()), "obsolete": sorted(old.keys() - new)}
        print(json_bytes(changes).decode(), end="")
        return
    outputs, quests = generate(args.archive, sources)
    for path, data in outputs.items():
        target = PACK / path
        if args.action == "build":
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        elif target.read_bytes() != data:
            raise ValueError(f"{path} differs from generated localization. Run build and review the changes.")
    if args.action == "render":
        if args.game is None:
            parser.error("render requires --game for a disposable QA instance")
        run_root = (PACK.parents[1] / "artifacts/agent-runs").resolve(strict=True)
        game = args.game.resolve(strict=True)
        parts = game.relative_to(run_root).parts
        if len(parts) < 2 or not (run_root / parts[0] / "RUN.json").is_file():
            raise ValueError("Use a marked disposable QA game directory")
        for path, data in quests.items():
            if (game / path).read_bytes() != sources[path.removeprefix(QUESTS)]:
                raise ValueError(f"QA source differs from exact author quest: {path}")
        for path, data in (quests | {p: d for p, d in outputs.items() if p.startswith(LANG)}).items():
            target = game / path
            if (not target.resolve().is_relative_to(game) or target.is_symlink() or
                    (target.exists() and target.stat().st_nlink != 1)):
                raise ValueError(f"QA output is outside the disposable game or linked: {target}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)


if __name__ == "__main__":
    main()
