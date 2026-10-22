#!/usr/bin/env python3
"""Create, read and export Liasio batches from a terminal or a Codex command.

Keep LINKVAULT_API_KEY in the environment. JSON output contains private share URLs.
Use the same request file and idempotency key to retry an interrupted creation.
"""

import argparse
import json
import os
from pathlib import Path
import sys
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request

if __package__:
    from .upload import API, APIError
else:
    from upload import API, APIError


def export_file(client, batch_id, format, output):
    mime = (
        "text/csv"
        if format == "csv"
        else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    request = Request(
        client.origin + f"/api/v1/link-batches/{batch_id}/export/{format}/",
        headers={"Authorization": "Bearer " + client.token, "Accept": mime},
    )
    # Reuse the same no-redirect opener as the upload client: never forward keys.
    with client.opener.open(request, timeout=60) as response:
        if response.headers.get_content_type() != mime:
            raise ValueError("Unexpected export content type.")
        contents = response.read(4 * 1024 * 1024 + 1)
    if len(contents) > 4 * 1024 * 1024:
        raise ValueError("Export exceeds the client download limit.")
    # Do not overwrite files or follow an existing symlink. Exports contain URLs.
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as target:
        target.write(contents)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="Liasio HTTPS origin")
    commands = parser.add_subparsers(dest="action", required=True)
    create = commands.add_parser("create", help="Create all links in one saved batch")
    create.add_argument("--collection", required=True, type=uuid.UUID)
    create.add_argument("--request", required=True, type=Path, help="JSON request file")
    create.add_argument("--idempotency-key", required=True)
    listing = commands.add_parser("list", help="List batches, 50 per page")
    listing.add_argument("--offset", type=int, default=0)
    listing.add_argument("--collection", type=uuid.UUID)
    get = commands.add_parser("get", help="Read current names, links and counters")
    get.add_argument("batch", type=uuid.UUID)
    export = commands.add_parser("export", help="Download a private Excel or CSV file")
    export.add_argument("batch", type=uuid.UUID)
    export.add_argument("--format", choices=("xlsx", "csv"), default="xlsx")
    export.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    token = os.environ.get("LINKVAULT_API_KEY")
    if not token:
        parser.error("Set LINKVAULT_API_KEY privately in your environment.")
    client = API(args.url, token)
    if args.action == "create":
        with args.request.open("rb") as source:
            raw = source.read(256 * 1024 + 1)
        if len(raw) > 256 * 1024:
            raise ValueError("The JSON request must not exceed 256 KiB.")
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("The request must be a JSON object.")
        # Preserve exact request bytes, including whitespace, on retries.
        result = client.request(
            f"/api/v1/collections/{args.collection}/link-batches/",
            raw,
            key=args.idempotency_key,
        )
    elif args.action == "list":
        if not 0 <= args.offset <= 100000:
            raise ValueError("Offset must be between 0 and 100000.")
        path = f"/api/v1/link-batches/?offset={args.offset}"
        if args.collection:
            path += f"&collection_id={args.collection}"
        result = client.request(path)
    elif args.action == "get":
        result = client.request(f"/api/v1/link-batches/{args.batch}/")
    else:
        export_file(client, args.batch, args.format, args.output)
        print(f"Saved {args.output}", file=sys.stderr)
        return
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (APIError, HTTPError, URLError, OSError, ValueError, RuntimeError) as exc:
        if isinstance(exc, HTTPError):
            message = f"Export failed (HTTP {exc.code})."
        elif isinstance(exc, URLError):
            message = (
                "Connection failed. Retry with the same request and idempotency key."
            )
        else:
            message = str(exc)
        print(message, file=sys.stderr)
        raise SystemExit(1) from None
