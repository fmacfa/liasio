#!/usr/bin/env python3
"""Create/verify checksums before loading a locally trusted backup."""

import argparse
import hashlib
from datetime import datetime, timezone
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("action", choices=["create", "verify"])
parser.add_argument("directory", type=Path)
args = parser.parse_args()
manifest = {}
for name in ("database.sql", "files.tar.gz"):
    path = args.directory / name
    if not path.is_file() or not path.stat().st_size:
        raise SystemExit(f"Missing or empty {name}")
    with path.open("rb") as source:
        manifest[name] = hashlib.file_digest(source, "sha256").hexdigest()
path = args.directory / "checksums.json"
if args.action == "create":
    path.write_text(
        json.dumps(
            {"created_at": datetime.now(timezone.utc).isoformat(), "files": manifest},
            indent=2,
        )
        + "\n"
    )
else:
    if json.loads(path.read_text())["files"] != manifest:
        raise SystemExit("Backup checksums do not match. Restore aborted.")
    print("Backup checksums verified.")
