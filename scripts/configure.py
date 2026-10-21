#!/usr/bin/env python3
"""Create an ignored .env with independent random secrets; never overwrite it."""

import argparse
import base64
import os
import secrets
from pathlib import Path
from urllib.parse import urlparse

parser = argparse.ArgumentParser(
    description="Préparer LinkVault (sans écraser de configuration existante)."
)
parser.add_argument("--domain", help="Nom de domaine, par exemple fichiers.example.com")
parser.add_argument("--dev", action="store_true", help="Développement local uniquement")
args = parser.parse_args()
if bool(args.domain) == bool(args.dev):
    parser.error("Choisissez --domain fichiers.example.com OU --dev.")
if args.domain:
    parsed = urlparse("https://" + args.domain)
    if (
        parsed.netloc != args.domain
        or parsed.path
        or parsed.username
        or parsed.port
        or not parsed.hostname
        or "." not in parsed.hostname
    ):
        parser.error("Indiquez uniquement un nom de domaine valide.")
path = Path(__file__).resolve().parent.parent / ".env"
if path.exists():
    parser.error(
        ".env existe déjà. Modifiez-le directement ; les secrets ne seront pas remplacés."
    )
content = {
    "DEBUG": "true" if args.dev else "false",
    "APP_URL": "http://127.0.0.1:8000" if args.dev else f"https://{args.domain}",
    "DOMAIN": "localhost" if args.dev else args.domain,
    "SECRET_KEY": secrets.token_urlsafe(64),
    "MFA_ENCRYPTION_KEY": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
    "MONITORING_TOKEN": secrets.token_urlsafe(32),
    "SETUP_TOKEN": secrets.token_urlsafe(32),
    "POSTGRES_PASSWORD": secrets.token_urlsafe(32),
    "ANTIVIRUS_REQUIRED": "false" if args.dev else "true",
    "CLAMAV_HOST": "" if args.dev else "clamav",
    "TRUST_PROXY": "false" if args.dev else "true",
    "TIME_ZONE": "Europe/Berlin",
}
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as config:
    config.write("# Configuration privée. Ne jamais publier ce fichier.\n")
    config.writelines(f"{key}={value}\n" for key, value in content.items())
print("Configuration créée dans .env (permissions 0600).")
print(
    "Au premier accès, renseignez la valeur SETUP_TOKEN de ce fichier dans l’assistant."
)
if args.dev:
    print(
        "Mode local : l’antivirus est désactivé. N’y déposez pas de fichiers sensibles."
    )
