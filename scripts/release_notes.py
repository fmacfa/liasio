#!/usr/bin/env python3
from pathlib import Path
import tomllib

root = Path(__file__).resolve().parent.parent
version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
notes = (
    (root / "CHANGELOG.md")
    .read_text()
    .split(f"## {version}", 1)[1]
    .split("\n## ", 1)[0]
)
print(f"## LinkVault {version}\n" + notes.split("\n", 1)[1])
print(f"""
### Container installation

Registry: `ghcr.io/fmacfa/linkvault:{version}` (Linux amd64 and arm64).

Portable images are attached below. Verify `SHA256SUMS`, select your architecture, then:

```sh
gunzip -c linkvault-{version}-linux-amd64.tar.gz | docker load
```

Set `LINKVAULT_IMAGE=linkvault:{version}` in `.env` and run `docker compose up -d --no-build`.
Use the included Compose configuration for PostgreSQL, ClamAV and HTTPS. See the README and upgrade guide before replacing an existing installation.

GitHub artifact attestations accompany the archives; verify with `gh attestation verify <archive> --repo fmacfa/linkvault`.
""")
