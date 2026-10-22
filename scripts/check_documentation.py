"""Fail CI on missing guide assets, untranslated strings or stale catalogs."""

import gettext
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit
import polib

ROOT = Path(__file__).resolve().parents[1]
errors = []
for domain in ("django", "djangojs"):
    path = ROOT / "locale/en/LC_MESSAGES" / f"{domain}.po"
    entries = polib.pofile(path)
    with path.with_suffix(".mo").open("rb") as source:
        compiled = gettext.GNUTranslations(source)
    for entry in entries:
        if entry.obsolete:
            continue
        if not entry.translated():
            errors.append(f"Untranslated or fuzzy: {entry.msgid}")
        translations = (
            entry.msgstr_plural.values() if entry.msgid_plural else [entry.msgstr]
        )
        expected = set(re.findall(r"%\([^)]+\)[sd]", entry.msgid))
        for value in translations:
            if set(re.findall(r"%\([^)]+\)[sd]", value)) != expected:
                errors.append(f"Translation placeholders differ: {entry.msgid}")
        if entry.msgid_plural:
            for number, expected_value in [
                (1, entry.msgstr_plural.get(0)),
                (2, entry.msgstr_plural.get(1)),
            ]:
                if (
                    compiled.ngettext(entry.msgid, entry.msgid_plural, number)
                    != expected_value
                ):
                    errors.append(f"Stale plural catalog: {entry.msgid}")
        elif compiled.gettext(entry.msgid) != entry.msgstr:
            errors.append(f"Stale catalog: {entry.msgid}")

pages = list((ROOT / "docs/guide").rglob("*.md")) + [ROOT / "docs/API.md"]
for path in pages:
    text = path.read_text()
    if (
        path.name != "README.md"
        and path.parent.name in ("en", "fr")
        and "![" not in text
    ):
        errors.append(f"Guide has no screenshot: {path.relative_to(ROOT)}")
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
    f"Documentation verified: {len(pages)} pages, English catalogs and screenshot references."
)
