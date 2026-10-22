"""Presentation only: preserve stored paths and first-appearance order."""


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
        result = []
        for node in branch["children"].values():
            if "children" in node:
                node["children"] = nodes(node)
            result.append(node)
        return result

    return nodes(root)
