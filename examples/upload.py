#!/usr/bin/env python3
"""Upload a file or directory and create a LinkVault link (Python 3.12+, stdlib).

Secrets: LINKVAULT_API_KEY and optional LINKVAULT_LINK_PASSWORD environment variables.
An explicit --state file enables resuming the same operation after interruption.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward a Bearer key to another origin.


class APIError(Exception):
    def __init__(self, status, data):
        self.status, self.data = status, data
        super().__init__(
            f"API request failed (HTTP {status}); inspect the request and account permissions."
        )


class API:
    def __init__(self, origin, token):
        parsed = urlsplit(origin)
        if (
            (
                parsed.scheme != "https"
                and not (
                    parsed.scheme == "http"
                    and parsed.hostname in ("127.0.0.1", "localhost")
                )
            )
            or parsed.path not in ("", "/")
            or parsed.username
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "Use an HTTPS origin, or HTTP on localhost for development."
            )
        self.origin, self.token = origin.rstrip("/"), token
        self.opener = build_opener(NoRedirect())

    def request(self, path, data=None, key=None, headers=None):
        if not path.startswith("/api/v1/"):
            raise ValueError("Invalid API path")
        raw = (
            data
            if isinstance(data, bytes)
            else json.dumps(data).encode()
            if data is not None
            else None
        )
        values = {
            "Authorization": "Bearer " + self.token,
            "Content-Type": "application/json",
            "Accept-Language": "en",
        }
        if key:
            values["Idempotency-Key"] = key
        values.update(headers or {})
        for attempt in range(5):
            try:
                with self.opener.open(
                    Request(self.origin + path, data=raw, headers=values), timeout=300
                ) as response:
                    return json.loads(response.read(1024 * 1024))
            except HTTPError as exc:
                try:
                    body = json.loads(exc.read(1024 * 1024))
                except (ValueError, UnicodeError):
                    body = {}
                if exc.code not in (429, 502, 503, 504) or attempt == 4:
                    raise APIError(exc.code, body) from None
            except URLError:
                if attempt == 4:
                    raise RuntimeError(
                        "Connection failed. Run again with the same --state file to resume."
                    ) from None
            time.sleep(min(60, 2**attempt * 5))
        raise RuntimeError("Retry limit reached")


def upload(
    api, source, title, state_path, *, previews=False, max_downloads=None, hours=168
):
    source_path = Path(source)
    if source_path.is_symlink():
        raise ValueError("Symbolic links are not supported")
    if not title.strip() or len(title) > 120:
        raise ValueError("Choose a title of 1 to 120 characters")
    root = source_path.resolve()
    candidates = sorted(root.rglob("*")) if root.is_dir() else [root]
    if any(p.is_symlink() for p in candidates):
        raise ValueError("Symbolic links are not supported")
    files = [p for p in candidates if p.is_file()]
    if not 1 <= len(files) <= 100:
        raise ValueError("Select between 1 and 100 files")
    manifest = []
    for path in files:
        with path.open("rb") as handle:
            checksum = hashlib.file_digest(handle, "sha256").hexdigest()
        name = path.relative_to(root).as_posix() if root.is_dir() else path.name
        manifest.append(
            {"path": name, "size": path.stat().st_size, "fingerprint": checksum}
        )
    state_path = Path(state_path)
    if state_path.exists():
        state = json.loads(state_path.read_text())
        if state["files"] != manifest or state["origin"] != api.origin:
            raise ValueError(
                "Files or server changed. Use a new state file for a new operation."
            )
    else:
        state = {
            "run": uuid.uuid4().hex,
            "origin": api.origin,
            "files": manifest,
            "title": title,
            "expires_at": (
                datetime.now(timezone.utc) + timedelta(hours=hours)
            ).isoformat(),
            "previews": previews,
            "max_downloads": max_downloads,
        }
        descriptor = os.open(state_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as output:
            json.dump(state, output)

    # Preserve IDs immediately: receipts expire after 24 h, stored IDs never replay creates.
    def save():
        temporary = state_path.with_name(state_path.name + ".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as output:
            json.dump(state, output)
        temporary.replace(state_path)

    if "collection" not in state:
        collection = api.request(
            "/api/v1/collections/create/",
            {"title": state["title"]},
            state["run"] + "-collection",
        )
        state["collection"] = collection["id"]
        save()
    if "upload" not in state:
        session = api.request(
            f"/api/v1/collections/{state['collection']}/uploads/start/",
            {"files": manifest},
            state["run"] + "-upload",
        )
        state["upload"] = session["id"]
        save()
    if not state.get("complete"):
        session = api.request(f"/api/v1/uploads/{state['upload']}/status/")
        if session["state"] == "pending":
            for part in session["parts"]:
                path = files[
                    next(
                        i
                        for i, entry in enumerate(manifest)
                        if entry["path"] == part["path"]
                    )
                ]
                with path.open("rb") as handle:
                    while part["offset"] < part["size"]:
                        handle.seek(part["offset"])
                        block = handle.read(
                            min(session["chunk_bytes"], part["size"] - part["offset"])
                        )
                        if not block:
                            raise ValueError("File changed during upload")
                        try:
                            result = api.request(
                                f"/api/v1/uploads/{session['id']}/parts/{part['id']}/chunk/",
                                block,
                                headers={
                                    "Content-Type": "application/octet-stream",
                                    "Upload-Offset": str(part["offset"]),
                                    "Upload-Checksum": hashlib.sha256(
                                        block
                                    ).hexdigest(),
                                },
                            )
                        except APIError as exc:
                            if exc.status != 409 or "offset" not in exc.data:
                                raise
                            result = exc.data
                        part["offset"] = result["offset"]
                        print(
                            f"{part['path']}: {part['offset']}/{part['size']} bytes",
                            file=sys.stderr,
                        )
        elif session["state"] != "complete":
            raise ValueError(
                "Upload expired or canceled. Start a new operation with a new state file."
            )
        api.request(f"/api/v1/uploads/{session['id']}/finish/", {})
        state["complete"] = True
        save()
    if "link" not in state:
        data = {
            "label": state["title"][:80],
            "expires_at": state["expires_at"],
            "allow_previews": state["previews"],
            "max_downloads": state["max_downloads"],
        }
        if os.environ.get("LINKVAULT_LINK_PASSWORD"):
            data["password"] = os.environ["LINKVAULT_LINK_PASSWORD"]
        link = api.request(
            f"/api/v1/collections/{state['collection']}/links/",
            data,
            state["run"] + "-link",
        )
        state["link"] = link["id"]
        save()
    return api.request(f"/api/v1/links/{state['link']}/")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source")
    parser.add_argument("--url", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument(
        "--state",
        required=True,
        help="Private JSON state file, outside the uploaded directory",
    )
    parser.add_argument("--previews", action="store_true")
    parser.add_argument("--max-downloads", type=int)
    parser.add_argument("--hours", type=int, default=168)
    args = parser.parse_args()
    token = os.environ.get("LINKVAULT_API_KEY")
    if not token:
        parser.error("Set LINKVAULT_API_KEY in the environment")
    if Path(args.source).resolve().is_dir() and Path(
        args.state
    ).resolve().is_relative_to(Path(args.source).resolve()):
        parser.error("--state must be outside the uploaded directory")
    try:
        result = upload(
            API(args.url, token),
            args.source,
            args.title,
            args.state,
            previews=args.previews,
            max_downloads=args.max_downloads,
            hours=args.hours,
        )
        print(json.dumps(result, indent=2))
    except (APIError, OSError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
