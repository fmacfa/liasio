"""Stable releases from the official Liasio repository. Also used by the host installer."""

import json
import re
from urllib.error import URLError
from urllib.request import Request, urlopen

REPOSITORY = "fmacfa/liasio"
API = f"https://api.github.com/repos/{REPOSITORY}"
TAG = re.compile(r"v(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})")


def version(tag):
    match = TAG.fullmatch(tag) if isinstance(tag, str) else None
    if not match:
        raise ValueError("Expected a stable vMAJOR.MINOR.PATCH tag.")
    return tuple(map(int, match.groups()))


def metadata(url):
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "Liasio-release-check",
        },
    )
    try:
        with urlopen(request, timeout=8) as response:  # nosec B310 -- internal callers use fixed GitHub HTTPS endpoints
            payload = response.read(1024 * 1024 + 1)
        if len(payload) > 1024 * 1024:
            raise ValueError("Release metadata too large.")
        return json.loads(payload)
    except (URLError, OSError, ValueError) as exc:
        raise ValueError("GitHub release metadata unavailable.") from exc


def latest_release():
    release = metadata(API + "/releases/latest")
    if (
        not isinstance(release, dict)
        or release.get("draft") is not False
        or release.get("prerelease") is not False
    ):
        raise ValueError("Expected a published stable release.")
    version(release.get("tag_name"))
    return release


def automatic_patch(current, target):
    before, after = version(current), version(target)
    return before[:2] == after[:2] and after > before
