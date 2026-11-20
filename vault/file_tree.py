"""Folder-first display order for collections, share pages and their previews."""

import re
import unicodedata


def _display_key(node):
    name = node["name"]
    folded = unicodedata.normalize("NFD", name.casefold())
    letters = "".join(c for c in folded if not unicodedata.combining(c))
    # Keep numbered siblings in reading order, e.g. "Plan 2" before "Plan 10".
    parts = re.split(r"([0-9]+)", letters)
    natural = tuple(int(part) if i % 2 else part for i, part in enumerate(parts))
    return "file" in node, natural, folded, name


def file_tree(assets):
    root = {"children": {}, "count": 0, "size": 0}
    for asset in assets:
        branch = root
        parts = asset.path.split("/")
        for name in parts[:-1]:
            branch = branch["children"].setdefault(
                name, {"name": name, "children": {}, "count": 0, "size": 0}
            )
            branch["count"] += 1
            branch["size"] += asset.size
        branch["children"][parts[-1]] = {"name": parts[-1], "file": asset}

    def nodes(branch):
        result = sorted(branch["children"].values(), key=_display_key)
        for node in result:
            if "children" in node:
                node["children"] = nodes(node)
        return result

    return nodes(root)


def files_in_tree(nodes):
    """Yield the gallery's originals in the same order as their file rows."""
    for node in nodes:
        if "file" in node:
            yield node["file"]
        else:
            yield from files_in_tree(node["children"])
