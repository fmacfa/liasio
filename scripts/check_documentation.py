"""Fail CI on missing guide assets, untranslated strings or stale catalogs."""

import ast
import gettext
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit
import polib

ROOT = Path(__file__).resolve().parents[1]
errors = []
# Read the declared language list without importing runtime settings or secrets.
settings_tree = ast.parse((ROOT / "linkvault/settings.py").read_text())
languages = next(
    [code for code, _ in ast.literal_eval(node.value)]
    for node in settings_tree.body
    if isinstance(node, ast.Assign)
    and any(
        isinstance(target, ast.Name) and target.id == "LANGUAGES"
        for target in node.targets
    )
)
for domain in ("django", "djangojs"):
    reference = None
    for language in languages:
        if language == "fr":  # Source strings.
            continue
        path = ROOT / "locale" / language / "LC_MESSAGES" / f"{domain}.po"
        if not path.exists() or not path.with_suffix(".mo").exists():
            errors.append(f"Missing catalog: {path.relative_to(ROOT)}")
            continue
        entries = polib.pofile(path)
        keys = {
            (e.msgctxt, e.msgid, e.msgid_plural) for e in entries if not e.obsolete
        }
        if reference is not None and keys != reference:
            errors.append(f"Catalog coverage differs: {language}/{domain}")
        reference = keys
        with path.with_suffix(".mo").open("rb") as source:
            compiled = gettext.GNUTranslations(source)
        for entry in entries:
            if entry.obsolete:
                continue
            prefix = f"{language}/{domain}: {entry.msgid}"
            if not entry.translated():
                errors.append(f"Untranslated or fuzzy: {prefix}")
            translations = (
                entry.msgstr_plural.values() if entry.msgid_plural else [entry.msgstr]
            )
            expected = set(re.findall(r"%\([^)]+\)[sd]", entry.msgid))
            for value in translations:
                if set(re.findall(r"%\([^)]+\)[sd]", value)) != expected:
                    errors.append(f"Translation placeholders differ: {prefix}")
            if entry.msgid_plural:
                for number in (0, 1, 2):
                    if compiled.ngettext(
                        entry.msgid, entry.msgid_plural, number
                    ) != entry.msgstr_plural.get(compiled.plural(number)):
                        errors.append(f"Stale plural catalog: {prefix}")
            elif compiled.gettext(entry.msgid) != entry.msgstr:
                errors.append(f"Stale catalog: {prefix}")

source_guides = {p.name for p in (ROOT / "docs/guide/fr").glob("*.md")}
for language in languages:
    translated_guides = {
        p.name for p in (ROOT / "docs/guide" / language).glob("*.md")
    }
    if translated_guides != source_guides:
        errors.append(f"Guide coverage differs: {language}")

pages = list((ROOT / "docs/guide").rglob("*.md")) + [ROOT / "docs/API.md"]
for path in pages:
    text = path.read_text()
    if (
        path.name != "README.md"
        and path.parent.name in languages
        and "![" not in text
    ):
        errors.append(f"Guide has no screenshot: {path.relative_to(ROOT)}")
    for href in re.findall(r"!\[[^\]]*\]\(([^)]+)\)", text):
        if path.parent.name in languages and "/user-guide/" in href:
            if f"/user-guide/{path.parent.name}/" not in href:
                errors.append(
                    f"Screenshot language differs: {path.relative_to(ROOT)} -> {href}"
                )
    for href in re.findall(r"\]\(([^)]+)\)", text):
        if urlsplit(href).scheme or href.startswith("#"):
            continue
        destination = (path.parent / unquote(href.split("#")[0])).resolve()
        if not destination.is_relative_to(ROOT) or not destination.exists():
            errors.append(
                f"Missing local reference: {path.relative_to(ROOT)} -> {href}"
            )
if errors:
    raise SystemExit("\n".join(errors))
print(
    f"Documentation verified: {len(pages)} pages, {', '.join(languages)} coverage, catalogs and screenshot references."
)
