#!/usr/bin/env python3
from pathlib import Path
import os
import re
import subprocess
import tomllib

root = Path(__file__).resolve().parent.parent
version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
source = (
    os.environ.get("GITHUB_SHA")
    or subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True
    ).strip()
)
if not re.fullmatch(r"[0-9a-f]{40}", source):
    raise ValueError("Expected an immutable source commit")
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

GitHub artifact attestations accompany the archives. Obtain the expected tag and commit from the release you intend to install, then verify the downloaded archive **before** loading it:

```sh
gh attestation verify linkvault-{version}-linux-amd64.tar.gz \\
  --repo fmacfa/linkvault \\
  --signer-workflow fmacfa/linkvault/.github/workflows/release.yml \\
  --source-ref refs/tags/v{version} \\
  --source-digest {source} \\
  --signer-digest {source}
```

Verification binds the archive's calculated SHA-256 digest to its provenance, the release workflow, tag and source commit. SHA256SUMS alone is only an integrity check. For registry installation, use the immutable reference from `IMAGE-DIGEST.txt` and verify it with the same restrictions (`gh attestation verify oci://<image>@sha256:<digest> ...`). Each architecture is tested and exported by build digest; the multi-architecture index is assembled from those exact digests and attested separately.
""")
