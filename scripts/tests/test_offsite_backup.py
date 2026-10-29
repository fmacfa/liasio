"""Real restic round-trip using only a disposable local repository."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(shutil.which("restic"), "restic CLI required")
class EncryptedBackupTests(unittest.TestCase):
    def test_encrypted_roundtrip_and_refusal_to_overwrite_a_target(self):
        with tempfile.TemporaryDirectory(prefix="liasio-restic-test-") as temporary:
            root = Path(temporary)
            password = root / "password"
            password.write_text("Disposable-test-only-restic-password")
            password.chmod(0o600)
            env = {
                **os.environ,
                "RESTIC_REPOSITORY": str(root / "repository"),
                "RESTIC_PASSWORD_FILE": str(password),
            }
            env.pop("RESTIC_PASSWORD", None)
            env.pop("RESTIC_PASSWORD_COMMAND", None)
            source = root / "source"
            source.mkdir()
            (source / "database.sql").write_text(
                "-- Fictional database fixture, no personal data\n"
            )
            (source / "files.tar.gz").write_bytes(b"fictional-backup-bytes")
            (source / "config.env").write_text(
                "SECRET_KEY=private-fixture-never-printed\n"
            )
            subprocess.run(
                [
                    "python3",
                    str(ROOT / "scripts/backup_manifest.py"),
                    "create",
                    str(source),
                ],
                check=True,
            )
            command = ["python3", str(ROOT / "scripts/offsite_backup.py")]

            def run(action, *args):
                return subprocess.run(
                    [*command, action, "--allow-local-test", *args],
                    env=env,
                    capture_output=True,
                    text=True,
                )

            self.assertEqual(run("init").returncode, 0)
            result = run(
                "backup",
                "--source",
                str(source),
                "--config",
                str(source / "config.env"),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("private-fixture", result.stdout + result.stderr)
            snapshot = json.loads(result.stdout)["encrypted_snapshot"]
            target = root / "restored"
            result = run("restore", "--snapshot", snapshot, "--target", str(target))
            self.assertEqual(result.returncode, 0, result.stderr)
            for name in ("database.sql", "files.tar.gz", "config.env"):
                self.assertEqual(
                    (source / name).read_bytes(), (target / name).read_bytes()
                )
            self.assertNotEqual(
                run(
                    "restore", "--snapshot", snapshot, "--target", str(target)
                ).returncode,
                0,
            )
            self.assertEqual(run("check").returncode, 0)


if __name__ == "__main__":
    unittest.main()
