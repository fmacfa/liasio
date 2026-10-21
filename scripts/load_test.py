#!/usr/bin/env python3
"""Real HTTP load probe on an isolated, disposable loopback instance.

Never targets an existing server or user database. PostgreSQL mode creates and
removes its own randomly named database. No user credentials enter the report.
"""

import argparse
import concurrent.futures
import hashlib
import http.cookiejar
import json
import os
from pathlib import Path
import platform
import re
import secrets
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent.parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--workers", type=int, default=4)
parser.add_argument(
    "--mib", type=int, default=256, help="MiB uploaded per concurrent user"
)
parser.add_argument("--database", choices=["sqlite", "postgres"], default="sqlite")
parser.add_argument("--output", type=Path, default=ROOT / ".local/load-report.json")
args = parser.parse_args()
if not 1 <= args.workers <= 8 or not 1 <= args.mib <= 1024:
    parser.error("Use 1–8 workers and 1–1024 MiB per worker.")
with socket.socket() as reserved_socket:
    reserved_socket.bind(("127.0.0.1", 0))
    port = reserved_socket.getsockname()[1]
origin = f"http://127.0.0.1:{port}"
password = secrets.token_urlsafe(32)
env = dict(
    os.environ,
    DEBUG="true",
    APP_URL=origin,
    TRUST_PROXY="false",
    SECRET_KEY=secrets.token_urlsafe(64),
    MFA_ENCRYPTION_KEY="",
    SETUP_TOKEN=secrets.token_urlsafe(32),
    MONITOR_EMAIL="",
    MIN_FREE_DISK_BYTES="0",
)
env.setdefault("ANTIVIRUS_REQUIRED", "false")
env.setdefault("CLAMAV_HOST", "")
database_name = "linkvault_probe_" + secrets.token_hex(8)
admin = None
if args.database == "postgres":
    import psycopg
    from psycopg import sql

    admin = psycopg.connect(
        host=env["POSTGRES_HOST"],
        dbname=env.get("POSTGRES_DB", "linkvault"),
        user=env.get("POSTGRES_USER", "linkvault"),
        password=env["POSTGRES_PASSWORD"],
        autocommit=True,
    )
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
    env["POSTGRES_DB"] = database_name
else:
    env.pop("POSTGRES_HOST", None)
latencies = []
lock = threading.Lock()
size = args.mib * 1024**2
block = (b"LinkVault repeatable load fixture.\n" * 150000)[: 4 * 1024**2]


def exercise(index):
    jar = http.cookiejar.CookieJar()
    client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    def request(path, data=None, headers=None, expected=200):
        headers = headers or {}
        if data is not None:
            headers["X-CSRFToken"] = next(
                cookie.value for cookie in jar if cookie.name == "csrftoken"
            )
            headers["Referer"] = origin + "/"
        before = time.monotonic()
        req = urllib.request.Request(origin + path, data=data, headers=headers)
        try:
            response = client.open(req, timeout=300)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            result = response.read()
            if response.status != expected:
                raise RuntimeError(
                    f"Request failed with {response.status}, expected {expected}: {result[:180]!r}"
                )
            url = response.url
        with lock:
            latencies.append(time.monotonic() - before)
        return result, url

    request("/login/")
    request(
        "/login/",
        urllib.parse.urlencode(
            {"email": f"probe{index}@example.test", "password": password}
        ).encode(),
        {"Content-Type": "application/x-www-form-urlencoded"},
    )
    _, collection_url = request(
        "/collections/new/",
        urllib.parse.urlencode(
            {"title": "Load probe", "description": "Disposable test"}
        ).encode(),
        {"Content-Type": "application/x-www-form-urlencoded"},
    )
    collection_path = urllib.parse.urlparse(collection_url).path
    result, _ = request(
        collection_path + "uploads/start/",
        json.dumps(
            {
                "files": [
                    {
                        "path": "probe.txt",
                        "size": size,
                        "fingerprint": hashlib.sha256(str(index).encode()).hexdigest(),
                    }
                ]
            }
        ).encode(),
        {"Content-Type": "application/json"},
        expected=201,
    )
    session = json.loads(result)
    chunk_url = f"/uploads/{session['id']}/parts/{session['parts'][0]['id']}/chunk/"
    digest = hashlib.sha256()
    offset = 0
    while offset < size:
        payload = block[: min(len(block), size - offset)]
        headers = {
            "Content-Type": "application/octet-stream",
            "Upload-Offset": str(offset),
            "Upload-Checksum": hashlib.sha256(payload).hexdigest(),
        }
        response, _ = request(chunk_url, payload, dict(headers))
        if offset == 0:
            # Simulate a lost response: retry the committed block and reconcile.
            retry, _ = request(chunk_url, payload, dict(headers), expected=409)
            if json.loads(retry)["offset"] != len(payload):
                raise RuntimeError("Offset reconciliation failed")
            status, _ = request(f"/uploads/{session['id']}/status/")
            if json.loads(status)["parts"][0]["offset"] != len(payload):
                raise RuntimeError("Resume status mismatch")
        digest.update(payload)
        offset = json.loads(response)["offset"]
    request(
        f"/uploads/{session['id']}/finish/", b"{}", {"Content-Type": "application/json"}
    )
    links, _ = request(
        collection_path + "link/",
        urllib.parse.urlencode({"label": "Probe", "max_downloads": "1"}).encode(),
        {"Content-Type": "application/x-www-form-urlencoded"},
    )
    match = re.search(rb'data-copy="(http[^"]+/s/[^"]+/)"', links)
    if not match:
        raise RuntimeError("Share link was not created")
    share_path = urllib.parse.urlparse(match.group(1).decode()).path
    request(share_path)
    # Stream ZIP to disk: neither the probe nor the server needs a file-sized buffer.
    req = urllib.request.Request(
        origin + share_path + "download/",
        data=b"",
        headers={
            "X-CSRFToken": next(c.value for c in jar if c.name == "csrftoken"),
            "Referer": origin + share_path,
        },
    )
    with (
        client.open(req, timeout=300) as response,
        tempfile.TemporaryFile() as archive_file,
    ):
        while chunk := response.read(1024**2):
            archive_file.write(chunk)
        archive_file.seek(0)
        with (
            zipfile.ZipFile(archive_file) as archive,
            archive.open("probe.txt") as source,
        ):
            if hashlib.file_digest(source, "sha256").hexdigest() != digest.hexdigest():
                raise RuntimeError("Downloaded checksum mismatch")
    request(share_path, expected=410)
    return {
        "user": index,
        "bytes": size,
        "sha256_verified": True,
        "resume_verified": True,
        "one_use_verified": True,
    }


server = None
try:
    with tempfile.TemporaryDirectory(prefix="linkvault-load-") as directory:
        env["DATA_DIR"] = directory

        def manage(*arguments, input=None):
            return subprocess.run(
                [sys.executable, "manage.py", *arguments],
                cwd=ROOT,
                env=env,
                input=input,
                text=True,
                check=True,
                stdout=subprocess.DEVNULL,
            )

        manage("migrate", "--noinput")
        code = "from vault.models import User, Branding\n"
        for i in range(args.workers):
            code += f"u=User.objects.create_user('probe{i}@example.test', {password!r}, display_name='Load probe')\nBranding.objects.create(user=u)\n"
        manage("shell", input=code)
        with open(Path(directory) / "server.log", "w+") as log:
            server = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "gunicorn",
                    "linkvault.wsgi:application",
                    "--bind",
                    f"127.0.0.1:{port}",
                    "--workers",
                    "2",
                    "--threads",
                    "2",
                    "--timeout",
                    "300",
                ],
                cwd=ROOT,
                env=env,
                stdout=log,
                stderr=log,
            )
            for _ in range(100):
                try:
                    with urllib.request.urlopen(origin + "/health/", timeout=2):
                        break
                except (OSError, urllib.error.URLError):
                    if server.poll() is not None:
                        raise RuntimeError("Probe server failed to start")
                    time.sleep(0.1)
            started = time.monotonic()
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=args.workers
            ) as pool:
                results = list(pool.map(exercise, range(args.workers)))
            elapsed = time.monotonic() - started
            manage("verify_storage")
            report = {
                "version": "0.3.0",
                "platform": platform.system(),
                "python": platform.python_version(),
                "database": args.database,
                "antivirus": bool(env.get("CLAMAV_HOST")),
                "concurrent_users": args.workers,
                "mib_per_user": args.mib,
                "total_upload_bytes": size * args.workers,
                "total_download_bytes": size * args.workers,
                "elapsed_seconds": round(elapsed, 2),
                "upload_plus_download_mib_per_second": round(
                    2 * args.mib * args.workers / elapsed, 2
                ),
                "http_requests_measured": len(latencies),
                "request_median_ms": round(statistics.median(latencies) * 1000, 2),
                "request_p95_ms": round(
                    sorted(latencies)[int(len(latencies) * 0.95)] * 1000, 2
                ),
                "results": results,
                "limitations": "Loopback generated text fixture; not an Internet bandwidth, proxy, malware efficacy or maximum-capacity certification. HTTP latency excludes streamed ZIP downloads.",
            }
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, indent=2) + "\n")
            print(json.dumps(report, indent=2))
            server.terminate()
            server.wait(timeout=30)
            server = None
finally:
    if server is not None:
        server.terminate()
        try:
            server.wait(timeout=30)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()
    if admin is not None:
        admin.execute(
            sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                sql.Identifier(database_name)
            )
        )
        admin.close()
