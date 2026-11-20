from datetime import UTC, datetime, timedelta
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.management import call_command
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from scripts import managed_update
from vault import release_channel as channel, updates
from vault.models import ServiceState
from .test_security import IsolatedFiles


class ReleaseRulesTests(SimpleTestCase):
    def test_only_published_stable_numeric_versions(self):
        self.assertTrue(channel.automatic_patch("v0.5.9", "v0.5.10"))
        for tag in ("v0.5.8", "v0.5.9", "v0.6.0", "v1.0.0"):
            self.assertFalse(channel.automatic_patch("v0.5.9", tag))
        for tag in ("main", "v01.2.3", "v0.5.2-rc1", "v1.2.3/else", None):
            with self.assertRaises(ValueError):
                channel.version(tag)
        for change in ({"draft": True}, {"prerelease": True}, {"tag_name": "main"}):
            release = {
                "tag_name": "v2.0.0",
                "draft": False,
                "prerelease": False,
                **change,
            }
            with (
                patch.object(channel, "metadata", return_value=release),
                self.assertRaises(ValueError),
            ):
                channel.latest_release()


@override_settings(APP_VERSION="0.5.1")
class UpdateFlowTests(IsolatedFiles, TestCase):
    def setUp(self):
        super().setUp()
        self.admin = self.account(admin=True)
        self.client.force_login(self.admin)
        self.action = reverse("update_action")
        self.url = reverse("updates")
        self.latest = patch.object(
            channel, "latest_release", return_value={"tag_name": "v0.5.2"}
        ).start()
        self.addCleanup(patch.stopall)

    def post(self, action, **values):
        return self.client.post(self.action, {"action": action, **values})

    def test_access_csrf_and_locales(self):
        self.assertContains(self.client.get(reverse("links")), self.url)
        for lang, title in (
            ("fr", "Mises à jour de Liasio"),
            ("en", "Liasio updates"),
            ("de", "Liasio-Updates"),
        ):
            self.assertContains(
                self.client.get(self.url, HTTP_ACCEPT_LANGUAGE=lang), title
            )
        self.assertEqual(self.client.get(self.action).status_code, 405)
        guarded = Client(enforce_csrf_checks=True)
        guarded.force_login(self.admin)
        self.assertEqual(
            guarded.post(self.action, {"action": "enable"}).status_code, 403
        )
        self.client.force_login(self.account("member@example.test"))
        self.assertNotContains(self.client.get(reverse("links")), self.url)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.post("check").status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.latest.assert_not_called()

    def test_check_failure_retains_release_without_install_and_checks_debounce(self):
        self.post("check")
        self.post("check")
        self.latest.assert_called_once()
        self.post("install", tag="v0.5.2")
        self.assertFalse(updates.pending(updates.data(updates.JOB)))
        row = ServiceState.objects.get(pk=updates.CHECK)
        row.data["attempt"] = (timezone.now() - timedelta(minutes=2)).isoformat()
        row.save()
        self.latest.side_effect = ValueError("offline")
        self.post("check")
        info = updates.data(updates.CHECK)
        self.assertTrue(info["error"])
        self.assertEqual(info["tag"], "v0.5.2")
        self.assertNotContains(self.client.get(self.url), 'value="install"')

    def test_queue_exact_displayed_release_and_recover_same_job(self):
        updates.poll()
        self.post("check")
        self.post("install", tag="v999.0.0")
        self.assertFalse(updates.pending(updates.data(updates.JOB)))
        self.post("install", tag="v0.5.2")
        job = updates.poll()
        self.assertEqual(
            (job["action"], job["tag"], job["automatic"]), ("install", "v0.5.2", False)
        )
        self.post("install", tag="v0.5.2")
        out = StringIO()
        call_command("update_agent", "poll", stdout=out)
        self.assertEqual(json.loads(out.getvalue()), job)
        updates.finish("another-id", "installed")
        self.assertTrue(updates.pending(updates.data(updates.JOB)))
        call_command(
            "update_agent", "finish", "--job", job["id"], "--state", "installed"
        )
        self.assertEqual(updates.poll(), {"action": "idle"})

    def test_daily_patch_opt_in_and_minor_stays_manual(self):
        initial = datetime(2026, 11, 20, 2, 58, tzinfo=UTC)
        with patch.object(updates, "utcnow", return_value=initial):
            self.assertEqual(updates.poll(), {"action": "idle"})
            self.latest.assert_not_called()
            self.post("enable")
        with patch.object(
            updates, "utcnow", return_value=initial + timedelta(minutes=3)
        ):
            first = updates.poll()
            self.assertTrue(first["automatic"])
            updates.finish(first["id"], "failed")
            self.assertEqual(updates.poll(), {"action": "idle"})
            self.latest.assert_called_once()
        with patch.object(
            updates, "utcnow", return_value=initial + timedelta(days=1, minutes=3)
        ):
            self.assertEqual(updates.poll(), {"action": "idle"})
        self.latest.return_value = {"tag_name": "v0.6.0"}
        with patch.object(
            updates, "utcnow", return_value=initial + timedelta(days=2, minutes=3)
        ):
            self.assertEqual(updates.poll(), {"action": "idle"})
            self.post("install", tag="v0.6.0")
            self.assertFalse(updates.poll()["automatic"])

    def test_gate_holds_public_requests_and_preserves_health(self):
        with self.settings(DATA_DIR=Path(self.directory.name)):
            (Path(self.directory.name) / ".update-maintenance").touch()
            response = self.client.get(reverse("links"))
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response["Cache-Control"], "no-store")
            self.assertEqual(self.client.get("/health/").status_code, 200)


class HostUpdaterTests(SimpleTestCase):
    def test_signature_failure_precedes_loading_image(self):
        import hashlib

        release = {
            "tag_name": "v0.5.2",
            "assets": [
                {
                    "name": "linkvault-0.5.2-linux-amd64.tar.gz",
                    "size": 3,
                    "digest": "sha256:" + hashlib.sha256(b"abc").hexdigest(),
                }
            ],
        }
        commands = []

        def exercise(args, **kwargs):
            commands.append(args[0])
            if args[0] == "curl":
                Path(args[args.index("-o") + 1]).write_bytes(b"abc")
            if args[0] == "gh":
                raise ValueError("signature verification failed")

        with (
            TemporaryDirectory() as tmp,
            patch.object(managed_update.platform, "machine", return_value="x86_64"),
            patch.object(managed_update, "run", side_effect=exercise),
            patch.object(
                channel,
                "metadata",
                return_value={
                    "attestations": [{"bundle": {"verificationMaterial": {}}}]
                },
            ),
        ):
            with self.assertRaisesRegex(ValueError, "signature"):
                managed_update.download_image(release, "a" * 40, Path(tmp))
        self.assertEqual(commands, ["curl", "gh"])

    def test_swap_uses_one_postgresql_transaction_and_fixed_identifiers(self):
        with TemporaryDirectory() as tmp:
            host = managed_update.Host(Path(tmp), "linkvault")
            with patch.object(host, "db") as db:
                with self.assertRaises(ValueError):
                    host.swap("linkvault", "wrong", "also-wrong")
                db.assert_not_called()
                host.swap(
                    "linkvault", "liasio_stage_" + "a" * 32, "liasio_before_" + "a" * 32
                )
                self.assertTrue(
                    db.call_args.args[0].startswith("BEGIN; ALTER DATABASE linkvault")
                )
                self.assertTrue(db.call_args.args[0].endswith("COMMIT;"))


class DatabaseCutoverTests(SimpleTestCase):
    databases = {"default"}

    def test_postgres_rename_and_failure_leave_the_right_database_live(self):
        from uuid import uuid4
        from django.db import connection
        import psycopg
        from psycopg import sql

        if connection.vendor != "postgresql":
            self.skipTest("Requires PostgreSQL database rename semantics")
        names = [
            "liasio_stage_" + uuid4().hex,
            "liasio_stage_" + uuid4().hex,
            "liasio_before_" + uuid4().hex,
        ]
        params = connection.get_connection_params()
        params["dbname"] = "postgres"
        with (
            psycopg.connect(**params, autocommit=True) as pg,
            TemporaryDirectory() as tmp,
        ):
            host = managed_update.Host(Path(tmp), "linkvault")

            def oid(name):
                row = pg.execute(
                    "SELECT oid FROM pg_database WHERE datname=%s", (name,)
                ).fetchone()
                return row[0] if row else None

            try:
                for name in names[:2]:
                    pg.execute(
                        sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name))
                    )
                a, b = map(oid, names[:2])

                def swap_sql(statement):
                    try:
                        pg.execute(statement)
                    except psycopg.Error:
                        pg.execute("ROLLBACK")
                        raise

                with patch.object(host, "db", side_effect=swap_sql):
                    with self.assertRaises(psycopg.errors.InvalidCatalogName):
                        host.swap(names[0], "liasio_stage_" + uuid4().hex, names[2])
                    self.assertEqual(oid(names[0]), a)
                    self.assertIsNone(oid(names[2]))
                    host.swap(*names)
                    self.assertEqual(oid(names[0]), b)
                    self.assertEqual(oid(names[2]), a)
            finally:
                for name in names:
                    pg.execute(
                        sql.SQL("DROP DATABASE IF EXISTS {}").format(
                            sql.Identifier(name)
                        )
                    )


class HostIsolationTests(SimpleTestCase):
    def test_restore_refuses_volume_shared_across_projects_before_running(self):
        live = {
            "volumes": {"files": {"name": "production_files"}},
            "networks": {"backend": {"name": "production_backend"}},
        }
        isolated = {
            "volumes": {"files": {"name": "production_files"}},
            "networks": {"backend": {"name": "isolated_backend"}},
            "services": {"db": {}, "app": {}},
        }
        with TemporaryDirectory() as tmp:
            host = managed_update.Host(Path(tmp), "linkvault")
            with (
                patch.object(host, "dc", return_value=json.dumps(isolated)) as dc,
                self.assertRaisesRegex(ValueError, "production volume"),
            ):
                host.check_restore_isolation(Path(tmp), "fixture", live)
            self.assertEqual(dc.call_count, 1)

    def test_before_opening_failure_reinstates_database_and_original_services(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            cfg = root / "config"
            cfg.mkdir()
            (root / "new").mkdir()
            (root / "old").mkdir()
            (root / "current").symlink_to(root / "new")
            host = managed_update.Host(root, "linkvault")
            txn = {
                "old": str(root / "old"),
                "new": str(root / "new"),
                "old_image": "linkvault:old",
                "stage": "liasio_stage_" + "1" * 32,
                "before": "liasio_before_" + "1" * 32,
                "phase": "closed",
                "old_services": ["app", "webhooks"],
                "new_services": ["app", "mail", "webhooks"],
            }
            host.note(txn)
            with (
                patch.object(host, "db", return_value=txn["before"]),
                patch.object(host, "dc") as dc,
                patch.object(host, "swap") as swap,
                patch.object(host, "use") as use,
                patch.object(host, "gate") as gate,
                patch.object(host, "start") as start,
            ):
                self.assertEqual(host.recover(txn), "failed")
                dc.assert_called_once_with(
                    "stop", "-t", "330", "app", "mail", "webhooks"
                )
                swap.assert_called_once_with("linkvault", txn["before"], txn["stage"])
                use.assert_called_once_with(root / "old", "linkvault:old")
                gate.assert_called_once_with(False)
                self.assertEqual(start.call_args_list[-1].args, (["webhooks"],))
                self.assertFalse(host.journal.exists())
