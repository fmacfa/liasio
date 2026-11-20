#!/usr/bin/env python3
"""Host-only updater for the documented single-host Docker Compose installation."""

import argparse
import contextlib
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from uuid import uuid4

try:
    import release_channel as channel  # installed next to the host helper
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from vault import release_channel as channel


class Deferred(Exception):
    """An existing transfer or backup takes priority."""


def run(args, *, capture=False, **kwargs):
    return subprocess.run(
        list(map(str, args)),
        check=True,
        stdout=subprocess.PIPE if capture else None,
        **kwargs,
    )


def output(args, **kwargs):
    return run(args, capture=True, **kwargs).stdout.decode().strip()


def atomic(path, content):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
        tmp.write(content)
        tmp.flush()
        os.fsync(tmp.fileno())
    os.replace(tmp.name, path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


@contextlib.contextmanager
def exclusive(path):
    with path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Deferred("Another operation is in progress.") from exc
        yield


def download_image(release, source, directory):
    tag = release["tag_name"]
    channel.version(tag)
    arch = {"x86_64": "amd64", "aarch64": "arm64"}[platform.machine()]
    filename = f"linkvault-{tag[1:]}-linux-{arch}.tar.gz"
    asset = next(a for a in release["assets"] if a["name"] == filename)
    digest = asset["digest"]
    if (
        not re.fullmatch("sha256:[a-f0-9]{64}", digest)
        or not 0 < asset["size"] <= 1024**3
    ):
        raise ValueError("Invalid release asset metadata.")
    archive = directory / filename
    url = f"https://github.com/{channel.REPOSITORY}/releases/download/{tag}/{filename}"
    run(
        [
            "curl",
            "--fail",
            "--silent",
            "--show-error",
            "--location",
            "--proto",
            "=https",
            "--proto-redir",
            "=https",
            "--max-time",
            "600",
            "--retry",
            "2",
            "--max-filesize",
            str(asset["size"]),
            "-o",
            archive,
            url,
        ]
    )
    with archive.open("rb") as src:
        if (
            archive.stat().st_size != asset["size"]
            or "sha256:" + hashlib.file_digest(src, "sha256").hexdigest() != digest
        ):
            raise ValueError("Release asset checksum mismatch.")
    bundle_name = filename.removesuffix(".tar.gz") + ".sigstore.json"
    bundles = directory / "attestations.jsonl"
    if any(a["name"] == bundle_name for a in release["assets"]):
        bundle = channel.metadata(
            f"https://github.com/{channel.REPOSITORY}/releases/download/{tag}/{bundle_name}"
        )
        if not isinstance(bundle, dict) or not bundle.get("verificationMaterial"):
            raise ValueError("Incomplete build attestation.")
        bundles.write_text(json.dumps(bundle) + "\n")
    else:
        attestations = channel.metadata(f"{channel.API}/attestations/{digest}")[
            "attestations"
        ]
        hydrated = [
            a["bundle"] for a in attestations if isinstance(a.get("bundle"), dict)
        ]
        if not hydrated:
            raise ValueError("A published build attestation is required.")
        bundles.write_text("".join(json.dumps(b) + "\n" for b in hydrated))
    private_gh = Path(__file__).resolve().with_name("gh")
    run(
        [
            str(private_gh) if private_gh.is_file() else "gh",
            "attestation",
            "verify",
            archive,
            "--bundle",
            bundles,
            "--repo",
            channel.REPOSITORY,
            "--signer-workflow",
            f"{channel.REPOSITORY}/.github/workflows/release.yml",
            "--source-ref",
            f"refs/tags/{tag}",
            "--source-digest",
            source,
            "--signer-digest",
            source,
        ]
    )
    run(["docker", "load", "-i", archive])
    exported = "linkvault:" + tag[1:]
    revision = output(
        [
            "docker",
            "image",
            "inspect",
            "--format",
            '{{index .Config.Labels "org.opencontainers.image.revision"}}',
            exported,
        ]
    )
    if revision != source:
        raise ValueError("Unexpected image source revision.")
    pinned = "linkvault:release-" + source
    run(["docker", "tag", exported, pinned])
    return pinned


class Host:
    def __init__(self, root, project):
        self.root = root.resolve()
        self.project = project
        self.cfg = self.root / "config"
        self.env = self.cfg / ".env"
        self.current = self.root / "current"
        self.journal = self.cfg / "update-transaction.json"

    def compose(self, src=None, image=None):
        src = src or self.current.resolve()
        files = [src / "compose.yaml"]
        override = self.cfg / "compose.cloudpanel.yaml"
        if override.is_file():
            files.append(override)
        env = dict(
            os.environ,
            COMPOSE_FILE=os.pathsep.join(map(str, files)),
            COMPOSE_PROJECT_NAME=self.project,
        )
        if image:
            env["LINKVAULT_IMAGE"] = image
        return env

    def dc(self, *args, src=None, image=None, overlay=None, **kwargs):
        src = src or self.current.resolve()
        env = self.compose(src, image)
        if overlay:
            env["COMPOSE_FILE"] += os.pathsep + str(overlay)
        return output(
            ["docker", "compose", *args],
            env=env,
            cwd=src,
            **kwargs,
        )

    def app(self, *args):
        return self.dc("exec", "-T", "app", "python", "manage.py", *args)

    def note(self, txn):
        atomic(self.journal, json.dumps(txn).encode())

    def use(self, src, image):
        original = self.env.read_bytes()
        pattern = rb"(?m)^LINKVAULT_IMAGE=[^\r\n]*$"
        if len(re.findall(pattern, original)) != 1:
            raise ValueError(
                "Expected one LINKVAULT_IMAGE setting in the host configuration."
            )
        atomic(
            self.env, re.sub(pattern, b"LINKVAULT_IMAGE=" + image.encode(), original)
        )
        staged = self.root / ("current-" + uuid4().hex)
        staged.symlink_to(src)
        os.replace(staged, self.current)

    def checkout(self, tag):
        with tempfile.TemporaryDirectory(dir=self.root / "releases") as tmp:
            checkout = Path(tmp) / "source"
            run(
                [
                    "git",
                    "clone",
                    "--quiet",
                    "--depth",
                    "1",
                    "--branch",
                    tag,
                    f"https://github.com/{channel.REPOSITORY}.git",
                    checkout,
                ]
            )
            sha = output(["git", "-C", checkout, "rev-parse", "HEAD"])
            if not re.fullmatch("[0-9a-f]{40}", sha):
                raise ValueError("Invalid source revision.")
            dest = self.root / "releases" / sha
            if not dest.exists():
                checkout.rename(dest)
            if output(["git", "-C", dest, "rev-parse", "HEAD"]) != sha or output(
                ["git", "-C", dest, "status", "--porcelain", "--untracked-files=no"]
            ):
                raise ValueError("Release directory differs from its source.")
            for name, target in (
                (".env", self.env),
                ("backups", self.root / "backups"),
            ):
                link = dest / name
                if not link.exists():
                    link.symlink_to(target)
                if link.resolve() != target:
                    raise ValueError("Unexpected release configuration path.")
            return sha, dest

    def db(self, sql, database="postgres"):
        return self.dc(
            "exec",
            "-T",
            "db",
            "psql",
            "-XAtq",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            "linkvault",
            "-d",
            database,
            "-c",
            sql,
        )

    def records(self, database):
        # Compare original sharing capabilities and counts without exporting account data.
        return self.db(
            """
            SELECT (SELECT count(*) FROM vault_user), (SELECT count(*) FROM vault_collection),
               (SELECT count(*) FROM vault_asset), (SELECT coalesce(sum(size),0) FROM vault_asset),
               (SELECT count(*) FROM vault_collectionaccess), (SELECT count(*) FROM vault_linkbatch),
               (SELECT count(*) FROM vault_apitoken), (SELECT count(*) FROM vault_webhook),
               (SELECT md5(coalesce(string_agg(md5(ROW(id,collection_id,short_code_digest,password_hash,
                    max_downloads,downloads,expires_at,revoked_at)::text), '' ORDER BY id),'')) FROM vault_sharelink)
        """,
            database,
        )

    def swap(self, current, replacement, stash):
        # All identifiers come from our private journal and a generated UUID.
        for name in (current, replacement, stash):
            if not re.fullmatch(r"linkvault|liasio_(stage|before)_[a-f0-9]{32}", name):
                raise ValueError("Invalid database identifier.")
        self.db(
            f"BEGIN; ALTER DATABASE {current} RENAME TO {stash}; ALTER DATABASE {replacement} RENAME TO {current}; COMMIT;"
        )

    def gate(self, enabled, running=True):
        code = (
            "import os,pathlib; p=pathlib.Path(os.environ['DATA_DIR'])/'.update-maintenance'; "
            + ("p.touch()" if enabled else "p.unlink(missing_ok=True)")
        )
        prefix = (
            ("exec", "-T", "app")
            if running
            else ("run", "-T", "--rm", "--no-deps", "app")
        )
        self.dc(*prefix, "python", "-c", code)

    def start(self, services):
        if services:
            self.dc(
                "up",
                "--detach",
                "--no-deps",
                "--no-build",
                "--wait",
                "--wait-timeout",
                "210",
                *services,
            )

    def snapshot(self, old):
        log = output(["bash", "scripts/backup.sh"], cwd=old, env=self.compose(old))
        match = re.search(
            r"^Backup created in (backups/[0-9]{8}T[0-9]{6}Z-[0-9]+)\. ", log, re.M
        )
        if not match:
            raise ValueError("Could not identify the new backup.")
        backup = (old / match[1]).resolve()
        if backup.parent != self.root / "backups":
            raise ValueError("Unexpected backup directory.")
        run(["python3", old / "scripts/backup_manifest.py", "verify", backup])
        with tarfile.open(backup / "configuration.tar.gz", "w:gz") as tar:
            for name in (".env", "compose.cloudpanel.yaml", "nginx.conf", "clamd.conf"):
                if (self.cfg / name).is_file():
                    tar.add(self.cfg / name, arcname="config/" + name)
            for name in ("compose", "backup.py"):
                if (self.root / name).is_file():
                    tar.add(self.root / name, arcname=name)
        atomic(
            backup / "release.json",
            json.dumps(
                {
                    "commit": output(["git", "-C", old, "rev-parse", "HEAD"]),
                    "source": str(old),
                }
            ).encode(),
        )
        return backup

    def check_restore_isolation(self, src, image, live):
        isolated = json.loads(
            self.dc(
                "-p",
                "liasio-restore-" + uuid4().hex,
                "config",
                "--format",
                "json",
                src=src,
                image=image,
            )
        )
        for kind in ("volumes", "networks"):
            used = {resource["name"] for resource in live[kind].values()}
            if any(
                resource["name"] in used or resource.get("external")
                for resource in isolated[kind].values()
            ):
                raise ValueError(
                    "A restore would reuse a production volume or network."
                )
        for service in ("db", "app"):
            svc = isolated["services"][service]
            if svc.get("container_name") or any(
                mount["type"] == "bind" and not mount.get("read_only")
                for mount in svc.get("volumes", [])
            ):
                raise ValueError(
                    "A restore requires isolated application and database resources."
                )

    def rehearse(self, src, image, backup):
        run(
            ["bash", "scripts/restore-test.sh", "--check-only", backup],
            cwd=src,
            env=self.compose(src, image),
        )

    def recover(self, txn):
        old, new = Path(txn["old"]), Path(txn["new"])
        if txn["phase"] == "opening":
            # Public writes may have resumed. Only finish forward from this point.
            self.use(new, txn["image"])
            self.start(["app"])
            self.gate(False)
            self.start([s for s in txn["new_services"] if s != "app"])
            meta_path = self.cfg / "deployment.json"
            meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
            image_id = output(
                ["docker", "image", "inspect", txn["image"], "--format", "{{.Id}}"]
            )
            meta.update(
                commit=new.name,
                image=image_id,
                image_tag=txn["image"],
                deployed_at=datetime.now(timezone.utc).isoformat(),
                latest_verified_backup=txn["backup"],
            )
            atomic(meta_path, (json.dumps(meta, indent=2) + "\n").encode())
            result = "installed"
        else:
            # No public writers are admitted before the opening phase.
            if self.current.resolve() == new:
                self.dc("stop", "-t", "330", *txn["new_services"])
            if (
                self.db(
                    f"SELECT datname FROM pg_database WHERE datname='{txn['before']}'"
                )
                == txn["before"]
            ):
                self.swap("linkvault", txn["before"], txn["stage"])
            self.use(old, txn["old_image"])
            self.start(["app"])
            self.gate(False)
            self.start([s for s in txn["old_services"] if s != "app"])
            result = "failed"
        if txn.get("job"):
            self.app("update_agent", "finish", "--job", txn["job"], "--state", result)
        self.journal.unlink()
        return result

    def install(self, tag, automatic=False, job=None):
        installed = (
            "v"
            + self.app(
                "shell",
                "-c",
                "from django.conf import settings; print(settings.APP_VERSION)",
            ).splitlines()[-1]
        )
        if job and tag == installed:
            self.app("update_agent", "finish", "--job", job, "--state", "installed")
            return
        if channel.version(tag) <= channel.version(installed) or (
            automatic and not channel.automatic_patch(installed, tag)
        ):
            raise ValueError("Update must be newer and match the requested channel.")
        release = channel.latest_release()
        if tag != release["tag_name"]:
            raise ValueError("The published release has changed. Check again.")
        if shutil.disk_usage(self.root).free < 5 * 1024**3:
            raise ValueError("At least 5 GiB of available space is required.")
        if (
            self.app(
                "shell",
                "-c",
                "from vault.capacity import capacity_lock,active_leases\nwith capacity_lock(): print(len(active_leases()))",
            ).splitlines()[-1]
            != "0"
        ):
            raise Deferred("Transfers are active.")
        sha, new = self.checkout(tag)
        with tempfile.TemporaryDirectory(dir=self.cfg) as tmp:
            image = download_image(release, sha, Path(tmp))
        old = self.current.resolve()
        old_cfg = json.loads(self.dc("config", "--format", "json"))
        new_cfg = json.loads(
            self.dc("config", "--format", "json", src=new, image=image)
        )
        self.check_restore_isolation(new, image, old_cfg)
        for service in ("db", "clamav", "caddy"):
            if old_cfg["services"].get(service, {}).get("image") != new_cfg[
                "services"
            ].get(service, {}).get("image"):
                raise ValueError(
                    "Infrastructure image change requires the operator upgrade guide."
                )
        svc = old_cfg["services"]["app"]
        if svc.get("environment", {}).get("STORAGE_BACKEND", "local") != "local":
            raise ValueError("Managed installation requires local original storage.")
        app_id = self.dc("ps", "-q", "app")
        mounts = json.loads(
            output(["docker", "inspect", app_id, "--format", "{{json .Mounts}}"])
        )
        volume = next(
            m["Name"]
            for m in mounts
            if m["Destination"] == "/data" and m["Type"] == "volume"
        )
        volumes = new_cfg["volumes"]
        mount = next(
            m for m in new_cfg["services"]["app"]["volumes"] if m["target"] == "/data"
        )
        if mount["type"] != "volume" or volumes[mount["source"]]["name"] != volume:
            raise ValueError("The candidate must use the same file volume.")
        running = set(self.dc("ps", "--status", "running", "--services").split())
        writers = sorted(
            running & {"app", "webhooks", "maintenance", "mail", "preview"}
        )
        if "app" not in writers:
            raise ValueError("Application not running.")
        next_services = sorted(set(writers) | ({"mail"} & new_cfg["services"].keys()))
        backups = sorted((self.root / "backups").glob("*/checksums.json"))
        if backups:
            self.rehearse(new, image, backups[-1].parent)
        uid = uuid4().hex
        txn = {
            "stage": "liasio_stage_" + uid,
            "before": "liasio_before_" + uid,
            "old": str(old),
            "new": str(new),
            "job": job,
            "old_image": svc["image"],
            "image": image,
            "old_services": writers,
            "new_services": next_services,
            "phase": "closed",
        }
        if (
            self.app(
                "shell",
                "-c",
                "from vault.capacity import capacity_lock,active_leases\nwith capacity_lock(): print(len(active_leases()))",
            ).splitlines()[-1]
            != "0"
        ):
            raise Deferred("Transfers are active.")
        self.note(txn)
        try:
            self.gate(True)
            self.dc("stop", "-t", "330", *writers)
            backup = self.snapshot(old)
            txn["backup"] = str(backup)
            if not backups:
                self.rehearse(new, image, backup)
            self.db("CREATE DATABASE " + txn["stage"])
            with (backup / "database.sql").open("rb") as sql:
                self.dc(
                    "exec",
                    "-T",
                    "db",
                    "psql",
                    "-Xq",
                    "-v",
                    "ON_ERROR_STOP=1",
                    "-U",
                    "linkvault",
                    "-d",
                    txn["stage"],
                    stdin=sql,
                )
            with tempfile.NamedTemporaryFile(
                mode="w", dir=self.cfg, suffix=".json"
            ) as overlay:
                readonly = {
                    "type": "volume",
                    "source": mount["source"],
                    "target": "/data",
                    "read_only": True,
                }
                json.dump({"services": {"app": {"volumes": [readonly]}}}, overlay)
                overlay.flush()
                probe = (
                    "run",
                    "-T",
                    "--rm",
                    "--no-deps",
                    "-e",
                    "POSTGRES_DB=" + txn["stage"],
                    "app",
                    "python",
                )
                target = {"src": new, "image": image, "overlay": overlay.name}
                self.dc(
                    *probe,
                    "-c",
                    "import os; assert os.statvfs('/data').f_flag & os.ST_RDONLY",
                    **target,
                )
                for command in (
                    ("migrate", "--noinput"),
                    ("verify_storage",),
                    ("check", "--deploy", "--fail-level", "ERROR"),
                ):
                    self.dc(*probe, "manage.py", *command, **target)
            if self.records("linkvault") != self.records(txn["stage"]):
                raise ValueError(
                    "Original file, membership or sharing records differ after migration."
                )
            self.swap("linkvault", txn["stage"], txn["before"])
            self.use(new, image)
            self.start(["app"])
            self.app("migrate", "--check")
            smoke = "from django.conf import settings; from django.test import Client; client=Client(HTTP_HOST=settings.ALLOWED_HOSTS[0],secure=True); assert client.get('/health/',secure=True).status_code==200 and client.get('/links/',secure=True).status_code==503; print(settings.APP_VERSION)"
            if self.app("shell", "-c", smoke).splitlines()[-1] != tag[1:]:
                raise ValueError("Candidate smoke check failed.")
            txn["phase"] = "opening"
            self.note(txn)
        except BaseException:
            self.recover(txn)
            raise
        self.recover(txn)

    def tick(self):
        # Poll and recovery are serialized on the host. A pending mailbox job can
        # survive a process restart between claiming it and writing the journal.
        receipt = self.cfg / "update-request.json"
        if self.journal.exists():
            with exclusive(self.cfg / "backup.lock"):
                self.recover(json.loads(self.journal.read_text()))
                receipt.unlink(missing_ok=True)
        job = (
            json.loads(receipt.read_text())
            if receipt.exists()
            else json.loads(self.app("update_agent", "poll"))
        )
        if job.get("action") != "install":
            return
        if (
            not re.fullmatch("[0-9a-f]{32}", job["id"])
            or type(job["automatic"]) is not bool
        ):
            raise ValueError("Invalid update request.")
        atomic(receipt, json.dumps(job).encode())
        try:
            with exclusive(self.cfg / "backup.lock"):
                self.install(job["tag"], job["automatic"], job["id"])
        except Deferred:
            self.app("update_agent", "finish", "--job", job["id"], "--state", "queued")
        except Exception:
            if self.journal.exists():
                raise
            self.app("update_agent", "finish", "--job", job["id"], "--state", "failed")
            receipt.unlink(missing_ok=True)
            raise
        receipt.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/opt/linkvault"))
    parser.add_argument("--project", default="linkvault")
    parser.add_argument("command", choices=("tick", "install"))
    parser.add_argument("tag", nargs="?")
    args = parser.parse_args()
    os.umask(0o077)
    host = Host(args.root, args.project)
    try:
        with exclusive(host.cfg / "update.lock"):
            if args.command == "tick":
                host.tick()
            elif args.tag:
                with exclusive(host.cfg / "backup.lock"):
                    if host.journal.exists():
                        raise ValueError(
                            "Recover the interrupted update with tick first."
                        )
                    host.install(args.tag)
            else:
                parser.error("install requires a release tag")
    except Deferred as exc:
        print(exc, file=sys.stderr)
        if args.command != "tick":
            raise SystemExit(75)


if __name__ == "__main__":
    main()
