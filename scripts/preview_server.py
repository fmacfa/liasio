"""Single-request renderer on a private Unix socket, without application data."""

import json
import os
import socketserver
import sys
import tempfile
from pathlib import Path

from preview_runtime import MAX_SOURCE, render


class Handler(socketserver.StreamRequestHandler):
    timeout = 15

    def handle(self):
        try:
            header = self.rfile.readline(129)
            if len(header) > 128 or not header.endswith(b"\n"):
                return
            request = json.loads(header)
            if not isinstance(request, dict):
                return
            size, kind = request.get("size"), request.get("kind")
            if (
                type(size) is not int
                or not 0 < size <= MAX_SOURCE
                or kind not in {"image", "pdf"}
            ):
                return
            with tempfile.TemporaryDirectory(prefix="preview-") as directory:
                source = Path(directory) / "source"
                with source.open("wb") as output:
                    remaining = size
                    while remaining:
                        block = self.rfile.read(min(65536, remaining))
                        if not block:
                            return
                        output.write(block)
                        remaining -= len(block)
                data = render(source, kind, directory)
            self.wfile.write(
                json.dumps({"size": len(data) if data else 0}).encode() + b"\n"
            )
            if data:
                self.wfile.write(data)
        except (OSError, ValueError, TypeError):
            return  # No source names, contents or native parser diagnostics in logs.


if __name__ == "__main__":
    os.umask(0o077)
    path = Path(sys.argv[1])
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    with socketserver.UnixStreamServer(str(path), Handler) as server:
        server.serve_forever()
