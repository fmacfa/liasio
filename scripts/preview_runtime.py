"""Shared bounded decoder invocation, usable without application configuration."""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

MAX_SOURCE = 20 * 1024**2
MAX_OUTPUT = 2 * 1024**2
WORKER = Path(__file__).with_name("preview_worker.py")


def render(source, kind, directory):
    if (
        kind not in {"image", "pdf"}
        or not 0 < Path(source).stat().st_size <= MAX_SOURCE
    ):
        return None
    descriptor, name = tempfile.mkstemp(suffix=".jpg", dir=directory)
    os.close(descriptor)
    try:
        result = subprocess.run(
            [sys.executable, "-I", str(WORKER), str(source), name, kind],
            env={"LANG": "C.UTF-8"},
            cwd=directory,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=8,
            check=False,
        )
        if result.returncode or not 0 < Path(name).stat().st_size <= MAX_OUTPUT:
            return None
        data = Path(name).read_bytes()
        return data if data.startswith(b"\xff\xd8\xff") else None
    except subprocess.TimeoutExpired:
        return None
    finally:
        Path(name).unlink(missing_ok=True)
