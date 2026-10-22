#!/usr/bin/env python3
"""Upload a file or directory and create a LinkVault link (Python 3.12+, stdlib).

Secrets: LINKVAULT_API_KEY and optional LINKVAULT_LINK_PASSWORD environment variables.
An explicit --state file enables resuming the same operation after interruption.
"""

import argparse
from contextlib import ExitStack
import fcntl
import hashlib
import json
import os
import secrets
import stat
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


class PrivateState:
    """Keep state beneath a locked, caller-owned 0700 directory descriptor."""

    def __init__(self, path):
        self.path = Path(path)
        self.directory = None

    def __enter__(self):
        self.directory = os.open(
            self.path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        )
        try:
            info = os.fstat(self.directory)
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
                raise ValueError(
                    "The state directory must belong to you and have mode 0700"
                )
            fcntl.flock(self.directory, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return self
        except BaseException:
            os.close(self.directory)
            self.directory = None
            raise

    def __exit__(self, *_args):
        os.close(self.directory)

    def read(self):
        try:
            descriptor = os.open(
                self.path.name,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=self.directory,
            )
        except FileNotFoundError:
            return None
        with os.fdopen(descriptor, "r") as handle:
            info = os.fstat(handle.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) & 0o077
                or info.st_size > 1024 * 1024
            ):
                raise ValueError("State must be a private regular file, at most 1 MiB")
            return json.load(handle)

    def save(self, state):
        name = ".linkvault-" + secrets.token_hex(16) + ".tmp"
        descriptor = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=self.directory,
        )
        try:
            with os.fdopen(descriptor, "w") as output:
                json.dump(state, output)
                output.flush()
                os.fsync(output.fileno())
            os.replace(
                name,
                self.path.name,
                src_dir_fd=self.directory,
                dst_dir_fd=self.directory,
            )
            os.fsync(self.directory)
        finally:
            try:
                os.unlink(name, dir_fd=self.directory)
            except FileNotFoundError:
                pass


def file_identity(handle):
    info = os.fstat(handle.fileno())
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def upload(
    api, source, title, state_path, *, previews=False, max_downloads=None, hours=168
):
    with ExitStack() as resources:
        state_store = resources.enter_context(PrivateState(state_path))
        return _upload(
            api,
            source,
            title,
            state_store,
            resources,
            previews=previews,
            max_downloads=max_downloads,
            hours=hours,
        )


def _upload(
    api, source, title, state_store, resources, *, previews, max_downloads, hours
):
    source_path = Path(source)
    if source_path.is_symlink():
        raise ValueError("Symbolic links are not supported")
    if not title.strip() or len(title) > 120:
        raise ValueError("Choose a title of 1 to 120 characters")
    root = source_path.resolve()
    if root.is_dir() and state_store.path.resolve().is_relative_to(root):
        raise ValueError("The state file must be outside the uploaded directory")
    candidates = sorted(root.rglob("*")) if root.is_dir() else [root]
    if any(p.is_symlink() for p in candidates):
        raise ValueError("Symbolic links are not supported")
    files = [p for p in candidates if p.is_file()]
    if not 1 <= len(files) <= 100:
        raise ValueError("Select between 1 and 100 files")
    manifest = []
    opened = []
    for path in files:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        handle = resources.enter_context(os.fdopen(descriptor, "rb"))
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise ValueError("Only regular files are supported")
        identity = file_identity(handle)
        checksum = hashlib.file_digest(handle, "sha256").hexdigest()
        if file_identity(handle) != identity:
            raise ValueError("File changed during upload")
        opened.append((handle, identity))
        name = path.relative_to(root).as_posix() if root.is_dir() else path.name
        manifest.append({"path": name, "size": identity[2], "fingerprint": checksum})
    state = state_store.read()
    if state is not None:
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
        state_store.save(state)

    # Preserve IDs immediately: receipts expire after 24 h, stored IDs never replay creates.
    def save():
        state_store.save(state)

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
                handle, identity = opened[
                    next(
                        i
                        for i, entry in enumerate(manifest)
                        if entry["path"] == part["path"]
                    )
                ]
                if (
                    part["size"] != identity[2]
                    or not 0 <= part["offset"] <= identity[2]
                ):
                    raise ValueError("Server returned an invalid file size or offset")
                while part["offset"] < part["size"]:
                    if file_identity(handle) != identity:
                        raise ValueError("File changed during upload")
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
                                "Upload-Checksum": hashlib.sha256(block).hexdigest(),
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
        if any(file_identity(handle) != identity for handle, identity in opened):
            raise ValueError("File changed during upload")
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
        help="Private JSON state file in an owned 0700 directory, outside the uploaded directory",
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
