import hashlib
import io
import json
import zipfile
from unittest.mock import patch
import threading
from concurrent.futures import ThreadPoolExecutor

from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import Http404
from django.test import Client, TestCase, TransactionTestCase, skipUnlessDBFeature
from django.db import connections, transaction, close_old_connections
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook

from vault.models import (
    Audit,
    Collection,
    CollectionAccess,
    Event,
    ShareLink,
    UploadSession,
)
from vault.storage import member_bundle
from vault.team_access import set_access
from .test_batch_api import api_key
from .test_security import IsolatedFiles


class TeamAccessTests(IsolatedFiles, TestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.account(admin=True)
        self.member = self.account("member@example.com")
        self.outsider = self.account("other-admin@example.com", admin=True)
        self.folder = self.collection(self.owner)
        self.folder.title = "Shared dossier"
        self.folder.save()
        self.private = Collection.objects.create(
            owner=self.owner, title="Unshared dossier"
        )
        self.grant = CollectionAccess.objects.create(
            collection=self.folder, user=self.member, role="reader"
        )
        self.client.force_login(self.member)
        self.token, raw = api_key(self.member, ["read", "links:write", "files:write"])
        self.auth = {"HTTP_AUTHORIZATION": "Bearer " + raw}

    def collaborate(self):
        self.grant.role = "collaborator"
        self.grant.save()

    def post_api(self, path, data, key="team-request-001"):
        return self.client.post(
            "/api/v1/" + path,
            json.dumps(data),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY=key,
            **self.auth,
        )

    def test_reader_can_find_download_but_cannot_discover_links_or_write(self):
        link = ShareLink.objects.create(
            collection=self.folder, label="Private recipient", max_downloads=1
        )
        for route in ["/", "/collections/"]:
            page = self.client.get(route)
            self.assertContains(page, self.folder.title)
            self.assertNotContains(page, self.private.title)
        page = self.client.get(f"/collections/{self.folder.pk}/")
        self.assertContains(page, "Lecteur")
        self.assertContains(page, "Dossier/note.txt")
        self.assertNotContains(page, link.public_url)
        self.assertNotContains(page, "Private recipient")
        self.assertNotContains(page, "Créer un lien")
        self.assertNotContains(page, "Supprimer ce dossier")
        response = self.client.post(f"/collections/{self.folder.pk}/download/")
        self.assertEqual(response.status_code, 200)
        with zipfile.ZipFile(
            io.BytesIO(b"".join(response.streaming_content))
        ) as archive:
            self.assertEqual(archive.read("Dossier/note.txt"), b"hello")
        link.refresh_from_db()
        self.assertEqual(link.downloads, 0)
        self.assertFalse(Event.objects.filter(kind="download_started").exists())
        self.assertTrue(
            Audit.objects.filter(
                actor=self.member, action="collection_downloaded"
            ).exists()
        )
        for path, data in [
            (f"/collections/{self.folder.pk}/edit/", {"title": "Hacked"}),
            (f"/collections/{self.folder.pk}/link/", {"label": "Bad"}),
            (
                f"/collections/{self.folder.pk}/links/batch/",
                {"mode": "count", "count": 1, "name": "Bad"},
            ),
            (f"/collections/{self.folder.pk}/uploads/start/", {}),
            (
                f"/collections/{self.folder.pk}/upload/",
                {"files": SimpleUploadedFile("new.txt", b"new")},
            ),
            (reverse("link_revoke", args=[link.pk]), {}),
            (reverse("link_rename", args=[link.pk]), {"label_0": "Bad"}),
            (reverse("link_previews", args=[link.pk]), {"enabled": "1"}),
        ]:
            with self.subTest(path=path):
                # An invalid manifest can be rejected before the authorization gate.
                if path.endswith("/uploads/start/"):
                    data = json.dumps(
                        {
                            "files": [
                                {"path": "new.txt", "size": 3, "fingerprint": "a" * 64}
                            ]
                        }
                    )
                    result = self.client.post(
                        path, data, content_type="application/json"
                    )
                else:
                    result = self.client.post(path, data)
                self.assertEqual(result.status_code, 404)
        self.assertNotContains(self.client.get("/links/"), link.label)
        listed = self.client.get("/api/v1/collections/", **self.auth).json()["results"]
        self.assertEqual([row["id"] for row in listed], [str(self.folder.pk)])
        self.assertEqual(
            self.client.get("/api/v1/links/", **self.auth).json()["results"], []
        )
        self.assertEqual(
            self.post_api(
                f"collections/{self.folder.pk}/links/", {"label": "Bad"}
            ).status_code,
            404,
        )

    def test_collaborator_can_edit_create_batches_export_and_revoke_with_actor_audit(
        self,
    ):
        self.collaborate()
        response = self.client.post(
            f"/collections/{self.folder.pk}/edit/",
            {"title": "Updated", "description": "Shared", "recipient_note": ""},
        )
        self.assertEqual(response.status_code, 302)
        self.folder.refresh_from_db()
        self.assertEqual(self.folder.title, "Updated")
        data = {
            "name": "Team batch",
            "names": ["A", "B"],
            "conditions": {"max_downloads": 3, "expires_in_days": 30},
        }
        path = f"collections/{self.folder.pk}/link-batches/"
        created = self.post_api(path, data)
        self.assertEqual(created.status_code, 201, created.content)
        batch = created.json()
        self.assertEqual(self.post_api(path, data).json(), batch)
        self.assertEqual(ShareLink.objects.count(), 2)
        self.assertContains(self.client.get("/link-batches/"), "Team batch")
        for key in ["view_url", "api_url"]:
            page = self.client.get(
                "/" + batch[key].split("/", 3)[3],
                **(self.auth if key == "api_url" else {}),
            )
            self.assertEqual(page.status_code, 200)
        xlsx = self.client.get(
            "/" + batch["exports"]["xlsx"].split("/", 3)[3], **self.auth
        )
        workbook = load_workbook(io.BytesIO(xlsx.content), read_only=True)
        self.assertEqual(len(list(workbook.active.values)), 3)
        workbook.close()
        self.assertEqual(
            set(
                Audit.objects.filter(action="link_created").values_list(
                    "actor_id", flat=True
                )
            ),
            {self.member.pk},
        )
        link = ShareLink.objects.first()
        self.assertEqual(
            self.client.post(
                reverse("link_rename", args=[link.pk]), {"label_0": "Renamed"}
            ).status_code,
            302,
        )
        self.assertEqual(
            self.client.post(
                reverse("link_previews", args=[link.pk]), {"enabled": "1"}
            ).status_code,
            302,
        )
        self.assertEqual(self.post_api(f"links/{link.pk}/revoke/", {}).status_code, 200)
        self.client.force_login(self.owner)
        self.assertContains(self.client.get("/link-batches/"), "Team batch")
        self.assertContains(self.client.get("/links/"), "Renamed")
        # Revoking membership must also deny replays containing old share URLs.
        set_access(self.owner, self.folder.pk, self.member, "")
        self.assertEqual(self.post_api(path, data).status_code, 404)
        self.assertEqual(
            self.client.get(
                "/" + batch["exports"]["xlsx"].split("/", 3)[3], **self.auth
            ).status_code,
            404,
        )

    def test_browser_batch_creation_and_reader_exclusion(self):
        self.collaborate()
        prepared = self.client.post(
            f"/collections/{self.folder.pk}/links/batch/",
            {"name": "Browser team batch", "mode": "count", "count": 2},
        )
        self.assertEqual(prepared.status_code, 302)
        created = self.client.post(
            prepared.url,
            {
                "action": "create",
                "label_0": "A",
                "label_1": "B",
                "expiry_mode": "days",
                "expires_in_days": 30,
                "max_downloads": 3,
            },
        )
        self.assertEqual(created.status_code, 302, created.content)
        self.assertEqual(self.folder.links.count(), 2)
        self.assertEqual(
            set(self.folder.links.values_list("max_downloads", flat=True)), {3}
        )
        set_access(self.owner, self.folder.pk, self.member, "reader")
        self.assertEqual(self.client.get(prepared.url).status_code, 404)
        self.assertNotContains(self.client.get("/link-batches/"), "Browser team batch")

    def test_api_upload_uses_actor_credentials_and_owner_reservation(self):
        self.collaborate()
        started = self.post_api(
            f"collections/{self.folder.pk}/uploads/start/",
            {
                "files": [{"path": "api.txt", "size": 3, "fingerprint": "a" * 64}],
            },
        )
        self.assertEqual(started.status_code, 201, started.content)
        session = started.json()
        chunk = self.client.post(
            f"/api/v1/uploads/{session['id']}/parts/{session['parts'][0]['id']}/chunk/",
            b"api",
            content_type="application/octet-stream",
            HTTP_UPLOAD_OFFSET="0",
            HTTP_UPLOAD_CHECKSUM=hashlib.sha256(b"api").hexdigest(),
            **self.auth,
        )
        self.assertEqual(chunk.status_code, 200, chunk.content)
        self.assertEqual(
            self.client.get(
                f"/api/v1/uploads/{session['id']}/status/", **self.auth
            ).json()["parts"][0]["offset"],
            3,
        )
        with self.captureOnCommitCallbacks(execute=True):
            finished = self.post_api(f"uploads/{session['id']}/finish/", {})
        self.assertEqual(finished.status_code, 200, finished.content)
        self.owner.refresh_from_db()
        self.member.refresh_from_db()
        self.assertEqual(
            (self.owner.used_bytes, self.owner.reserved_bytes, self.member.used_bytes),
            (8, 0, 0),
        )

    def test_api_scopes_still_limit_collaborators(self):
        self.collaborate()
        _token, raw = api_key(self.member, ["read"])
        self.auth = {"HTTP_AUTHORIZATION": "Bearer " + raw}
        self.assertEqual(
            self.post_api(
                f"collections/{self.folder.pk}/links/", {"label": "A"}
            ).status_code,
            403,
        )
        self.assertEqual(
            self.post_api(
                f"collections/{self.folder.pk}/uploads/start/", {}
            ).status_code,
            403,
        )

    def test_only_owner_manages_access_deletion_and_private_settings(self):
        self.collaborate()
        for user in [self.member, self.outsider]:
            self.client.force_login(user)
            for path in [
                f"/collections/{self.folder.pk}/access/",
                f"/collections/{self.folder.pk}/delete/",
            ]:
                self.assertEqual(
                    self.client.post(
                        path, {"email": self.outsider.email, "role": "collaborator"}
                    ).status_code,
                    404,
                )
            self.assertEqual(
                self.client.post(
                    "/storage/delete/",
                    {
                        "selected": [str(self.folder.files.first().pk)],
                        "confirm": "delete",
                    },
                ).status_code,
                404,
            )
        self.client.force_login(self.outsider)
        self.assertEqual(
            self.client.get(f"/collections/{self.folder.pk}/").status_code, 404
        )
        self.assertNotContains(self.client.get("/collections/"), self.folder.title)
        self.client.force_login(self.owner)
        response = self.client.post(
            f"/team/members/{self.member.pk}/access/",
            {str(self.folder.pk): "collaborator", str(self.private.pk): "reader"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            CollectionAccess.objects.get(
                collection=self.private, user=self.member
            ).role,
            "reader",
        )
        self.client.post(
            f"/collections/{self.folder.pk}/access/",
            {"email": self.member.email, "role": ""},
        )
        self.assertFalse(
            CollectionAccess.objects.filter(
                collection=self.folder, user=self.member
            ).exists()
        )
        # Every permission write is CSRF protected.
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.owner)
        self.assertEqual(
            strict.post(
                f"/collections/{self.folder.pk}/access/",
                {"email": self.member.email, "role": "reader"},
            ).status_code,
            403,
        )

    def start_upload(self, path="new.txt"):
        response = self.client.post(
            f"/collections/{self.folder.pk}/uploads/start/",
            {"files": [{"path": path, "size": 3, "fingerprint": "a" * 64}]},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def block(self, session):
        return self.client.post(
            f"/uploads/{session['id']}/parts/{session['parts'][0]['id']}/chunk/",
            b"new",
            content_type="application/octet-stream",
            HTTP_UPLOAD_OFFSET="0",
            HTTP_UPLOAD_CHECKSUM=hashlib.sha256(b"new").hexdigest(),
        )

    def test_shared_upload_charges_owner_and_keeps_sessions_private_to_initiator(self):
        self.collaborate()
        session = self.start_upload()
        self.owner.refresh_from_db()
        self.member.refresh_from_db()
        self.assertEqual(
            (self.owner.reserved_bytes, self.member.reserved_bytes), (3, 0)
        )
        stored = UploadSession.objects.get(pk=session["id"])
        self.assertEqual(
            (stored.owner_id, stored.initiated_by_id), (self.owner.pk, self.member.pk)
        )
        CollectionAccess.objects.create(
            collection=self.folder, user=self.outsider, role="collaborator"
        )
        other = Client()
        other.force_login(self.outsider)
        self.assertEqual(
            other.get(f"/uploads/{session['id']}/status/").status_code, 404
        )
        self.assertEqual(
            other.get(f"/collections/{self.folder.pk}/uploads/pending/").json()[
                "sessions"
            ],
            [],
        )
        self.assertEqual(self.block(session).status_code, 200)
        with self.captureOnCommitCallbacks(execute=True):
            finished = self.client.post(f"/uploads/{session['id']}/finish/")
        self.assertEqual(finished.status_code, 200, finished.content)
        self.owner.refresh_from_db()
        self.member.refresh_from_db()
        self.assertEqual(
            (self.owner.used_bytes, self.owner.reserved_bytes, self.member.used_bytes),
            (8, 0, 0),
        )
        asset = self.folder.files.get(path="new.txt")
        self.assertTrue(asset.blob.name.startswith(str(self.owner.pk) + "/"))
        self.assertTrue(
            Audit.objects.filter(actor=self.member, action="files_uploaded").exists()
        )

    def test_downgrade_cancels_upload_and_blocks_every_session_operation(self):
        self.collaborate()
        session = self.start_upload()
        with self.captureOnCommitCallbacks(execute=True):
            set_access(self.owner, self.folder.pk, self.member, "reader")
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.reserved_bytes, 0)
        self.assertEqual(UploadSession.objects.get(pk=session["id"]).state, "canceled")
        self.assertEqual(self.block(session).status_code, 404)
        for suffix in ["finish", "cancel"]:
            self.assertEqual(
                self.client.post(f"/uploads/{session['id']}/{suffix}/").status_code, 404
            )
        self.assertEqual(
            self.client.get(f"/uploads/{session['id']}/status/").status_code, 404
        )
        self.assertEqual(
            self.client.get(f"/collections/{self.folder.pk}/").status_code, 200
        )

    def test_multipart_upload_uses_owner_quota_and_webhooks(self):
        self.collaborate()
        self.member.quota_bytes = 0
        self.member.save(update_fields=["quota_bytes"])
        with patch("vault.webhooks.enqueue") as enqueue:
            response = self.client.post(
                f"/collections/{self.folder.pk}/upload/",
                {"files": SimpleUploadedFile("direct.txt", b"abc")},
            )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(self.folder.files.filter(path="direct.txt").exists())
        enqueue.assert_called_once_with(
            self.owner.pk, "files.uploaded", {"collection_id": str(self.folder.pk)}
        )
        self.owner.refresh_from_db()
        self.member.refresh_from_db()
        self.assertEqual((self.owner.used_bytes, self.member.used_bytes), (8, 0))
        self.owner.quota_bytes = 8
        self.owner.save(update_fields=["quota_bytes"])
        response = self.client.post(
            f"/collections/{self.folder.pk}/upload/",
            {"files": SimpleUploadedFile("denied.txt", b"abc")},
        )
        self.assertEqual(response.status_code, 413)

    def test_revocation_during_bundle_preparation_is_rechecked(self):
        original = zipfile.ZipFile.write

        def revoke(archive, *args, **kwargs):
            result = original(archive, *args, **kwargs)
            CollectionAccess.objects.filter(pk=self.grant.pk).delete()
            return result

        with patch("vault.storage.zipfile.ZipFile.write", revoke):
            with self.assertRaises(Http404):
                member_bundle(self.member, self.folder.pk)
        self.assertFalse(Audit.objects.filter(action="collection_downloaded").exists())

    def test_suspended_owner_or_member_and_trash_cannot_be_bypassed(self):
        for change in ["owner", "member", "trash"]:
            with self.subTest(change=change):
                if change == "trash":
                    self.folder.deleted_at = timezone.now()
                    self.folder.save()
                else:
                    user = getattr(self, change)
                    user.is_active = False
                    user.save()
                self.assertNotEqual(
                    self.client.get(f"/collections/{self.folder.pk}/").status_code, 200
                )
                self.assertNotEqual(
                    self.client.post(
                        f"/collections/{self.folder.pk}/download/"
                    ).status_code,
                    200,
                )
                if change == "trash":
                    self.folder.deleted_at = None
                    self.folder.save()
                else:
                    user.is_active = True
                    user.save()
                    self.client.force_login(self.member)

    def test_permission_pages_in_all_languages_and_no_automatic_new_folder_access(self):
        self.client.force_login(self.owner)
        for language in ["fr", "en", "de"]:
            for route in [
                f"/collections/{self.folder.pk}/access/",
                f"/team/members/{self.member.pk}/access/",
            ]:
                response = self.client.get(route, HTTP_ACCEPT_LANGUAGE=language)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["Content-Language"], language)
        created = Collection.objects.create(
            owner=self.owner, title="New private folder"
        )
        self.client.force_login(self.member)
        self.assertEqual(
            self.client.get(f"/collections/{created.pk}/").status_code, 404
        )


class TeamAccessMigrationTests(TransactionTestCase):
    def test_existing_folders_remain_private_and_upload_ownership_is_preserved(self):
        old = [("vault", "0010_sharelink_short_code_digest")]
        executor = MigrationExecutor(connection)
        executor.migrate(old)
        try:
            apps = executor.loader.project_state(old).apps
            owner = apps.get_model("vault", "User").objects.create(
                email="owner-migration@example.com",
                display_name="Owner",
                password="!",
                used_bytes=123,
                reserved_bytes=456,
            )
            folder = apps.get_model("vault", "Collection").objects.create(
                owner=owner, title="Previously private"
            )
            session = apps.get_model("vault", "UploadSession").objects.create(
                owner=owner, collection=folder, size=456
            )
            before = (
                apps.get_model("vault", "UploadSession")
                .objects.values()
                .get(pk=session.pk)
            )
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())
            after = UploadSession.objects.values().get(pk=session.pk)
            self.assertIsNone(after.pop("initiated_by_id"))
            self.assertEqual({key: after[key] for key in before}, before)
            self.assertFalse(CollectionAccess.objects.exists())
            self.assertEqual(Collection.objects.get(pk=folder.pk).owner_id, owner.pk)
            current = UploadSession.objects.select_related("owner").get(pk=session.pk)
            self.assertEqual(current.owner.used_bytes, 123)
            self.assertEqual(current.owner.reserved_bytes, 456)
        finally:
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())


class TeamAccessConcurrencyTests(IsolatedFiles, TransactionTestCase):
    @skipUnlessDBFeature("has_select_for_update")
    def test_revocation_wins_over_inflight_api_creation_after_initial_lookup(self):
        from vault import access

        owner = self.account()
        member = self.account("concurrent@example.com")
        folder = self.collection(owner)
        CollectionAccess.objects.create(
            collection=folder, user=member, role="collaborator"
        )
        _token, raw = api_key(member)
        ready = threading.Event()
        waiting = threading.Event()
        release = threading.Event()
        original_lock = access.lock_accounts

        def hold_revocation():
            close_old_connections()
            try:
                with transaction.atomic():
                    original_lock(owner, member.pk)
                    CollectionAccess.objects.filter(
                        collection=folder, user=member
                    ).delete()
                    ready.set()
                    if not release.wait(10):
                        raise TimeoutError("creation did not reach authorization lock")
            finally:
                connections.close_all()

        def observed_lock(actor, owner_id, **kwargs):
            if actor.pk == member.pk:
                waiting.set()
            return original_lock(actor, owner_id, **kwargs)

        def create():
            close_old_connections()
            try:
                response = Client().post(
                    f"/api/v1/collections/{folder.pk}/link-batches/",
                    json.dumps({"name": "Race", "names": ["A"]}),
                    content_type="application/json",
                    HTTP_AUTHORIZATION="Bearer " + raw,
                    HTTP_IDEMPOTENCY_KEY="shared-race-001",
                )
                return response.status_code
            finally:
                connections.close_all()

        with (
            ThreadPoolExecutor(max_workers=2) as pool,
            patch("vault.access.lock_accounts", observed_lock),
        ):
            revoking = pool.submit(hold_revocation)
            self.assertTrue(ready.wait(10))
            creating = pool.submit(create)
            try:
                self.assertTrue(waiting.wait(10))
            finally:
                release.set()
            revoking.result(timeout=15)
            self.assertEqual(creating.result(timeout=15), 404)
        self.assertFalse(ShareLink.objects.filter(collection=folder).exists())
