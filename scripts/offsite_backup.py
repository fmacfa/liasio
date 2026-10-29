#!/usr/bin/env python3
"""Encrypted restic backups with retention and a non-destructive restore workflow."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def run(argv, **kwargs):
    result = subprocess.run(argv, check=False, capture_output=True, text=True, **kwargs)
    if result.returncode:
        # Backend errors may contain credentials embedded in repository URLs.
        raise RuntimeError(
            f"{Path(argv[0]).name} failed (exit {result.returncode}); no credentials were printed."
        )
    return result.stdout


def verify(directory):
    run(["python3", str(ROOT / "scripts/backup_manifest.py"), "verify", str(directory)])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("init", "backup", "check", "restore"))
    parser.add_argument(
        "--source",
        type=Path,
        help="Already verified backup directory; otherwise create and test a new backup.",
    )
    parser.add_argument("--config", type=Path, default=ROOT / ".env")
    parser.add_argument(
        "--snapshot",
        help="Exact restic snapshot ID for restore (never defaults to latest).",
    )
    parser.add_argument(
        "--target",
        type=Path,
        help="An empty absolute directory; live data is never replaced.",
    )
    parser.add_argument("--keep-daily", type=int, default=7)
    parser.add_argument("--keep-weekly", type=int, default=4)
    parser.add_argument("--keep-monthly", type=int, default=6)
    parser.add_argument(
        "--allow-local-test",
        action="store_true",
        help="Allow a disposable local repository for integration tests.",
    )
    args = parser.parse_args()
    os.umask(0o077)
    repository = os.environ.get("RESTIC_REPOSITORY", "")
    password_file = Path(os.environ.get("RESTIC_PASSWORD_FILE", "/nonexistent"))
    if not repository or not shutil.which("restic"):
        parser.error(
            "Install restic and set RESTIC_REPOSITORY and RESTIC_PASSWORD_FILE."
        )
    if not args.allow_local_test and not repository.startswith(
        ("s3:", "sftp:", "rest:", "azure:", "gs:", "b2:")
    ):
        parser.error(
            "Choose a remote encrypted repository. Local repositories are allowed only with --allow-local-test."
        )
    if (
        password_file.is_symlink()
        or not password_file.is_file()
        or stat.S_IMODE(password_file.stat().st_mode) & 0o077
        or password_file.stat().st_size < 16
    ):
        parser.error(
            "RESTIC_PASSWORD_FILE must be a regular private file (0600) with a password of at least 16 bytes."
        )
    if os.environ.get("RESTIC_PASSWORD") or os.environ.get("RESTIC_PASSWORD_COMMAND"):
        parser.error("Use only RESTIC_PASSWORD_FILE for the repository password.")
    if any(
        n < 1 or n > 365 for n in (args.keep_daily, args.keep_weekly, args.keep_monthly)
    ):
        parser.error("Retention values must be between 1 and 365.")
    hostname = os.environ.get("RESTIC_HOST", socket.gethostname())
    if args.action == "init":
        run(["restic", "init"])
        print(
            "Encrypted repository initialized. Keep its password separately from the server."
        )
    elif args.action == "check":
        run(["restic", "check", "--read-data"])
        print("Repository integrity verified, including stored data.")
    elif args.action == "backup":
        source = args.source
        if source is None:
            output = run([str(ROOT / "scripts/backup-and-verify.sh")], cwd=ROOT)
            match = re.search(r"Backup created in (backups/[^ ]+)\. Encrypt", output)
            if not match:
                raise RuntimeError("Could not identify the verified snapshot.")
            source = ROOT / match.group(1)
        verify(source)
        if not args.config.is_file() or args.config.is_symlink():
            raise RuntimeError(
                "The original private .env file is required for a recoverable backup."
            )
        with tempfile.TemporaryDirectory(
            prefix="liasio-encrypted-backup-"
        ) as temporary:
            directory = Path(temporary)
            # Hard links avoid a second full local copy of a large verified archive.
            for name in ("database.sql", "files.tar.gz", "checksums.json"):
                try:
                    os.link(source / name, directory / name)
                except OSError:
                    shutil.copyfile(source / name, directory / name)
            shutil.copyfile(args.config, directory / "config.env")
            (directory / "config.sha256").write_text(
                hashlib.sha256((directory / "config.env").read_bytes()).hexdigest()
            )
            output = run(
                [
                    "restic",
                    "backup",
                    ".",
                    "--json",
                    "--host",
                    hostname,
                    "--tag",
                    "liasio",
                ],
                cwd=directory,
            )
            summaries = [
                json.loads(line) for line in output.splitlines() if line.startswith("{")
            ]
            snapshot = next(
                row["snapshot_id"]
                for row in summaries
                if row.get("message_type") == "summary"
            )
        run(["restic", "check"])
        run(
            [
                "restic",
                "forget",
                "--host",
                hostname,
                "--tag",
                "liasio",
                "--group-by",
                "host,tags",
                "--keep-daily",
                str(args.keep_daily),
                "--keep-weekly",
                str(args.keep_weekly),
                "--keep-monthly",
                str(args.keep_monthly),
                "--prune",
            ]
        )
        print(
            json.dumps(
                {
                    "encrypted_snapshot": snapshot,
                    "verified_archive": True,
                    "retention_applied": True,
                }
            )
        )
    else:
        if not args.snapshot or not re.fullmatch(r"[0-9a-f]{8,64}", args.snapshot):
            parser.error("Provide an exact hexadecimal --snapshot ID.")
        target = args.target
        if (
            not target
            or not target.is_absolute()
            or target.is_symlink()
            or target.exists()
            and (not target.is_dir() or any(target.iterdir()))
        ):
            parser.error("--target must be an empty absolute directory.")
        target.mkdir(mode=0o700, parents=True, exist_ok=True)
        run(["restic", "restore", args.snapshot, "--target", str(target), "--verify"])
        verify(target)
        if (
            hashlib.sha256((target / "config.env").read_bytes()).hexdigest()
            != (target / "config.sha256").read_text().strip()
        ):
            raise RuntimeError("Restored configuration checksum mismatch.")
        print(
            "Restored and verified into the empty target. Live services were not changed. Follow docs/OFFSITE_BACKUPS.md to start a separate instance."
        )


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError, StopIteration) as exc:
        raise SystemExit(str(exc)) from None
