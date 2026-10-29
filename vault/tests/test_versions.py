import json
import uuid
import zipfile
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from vault.models import Asset, CollectionAccess, ShareLink
from vault.storage import add_files, download_bundle
from vault.versions import files_for, remove_revision, restore_revision
from .test_batch_api import api_key
from .test_security import IsolatedFiles


class VersionTests(IsolatedFiles, TestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.account()
        self.folder = self.collection(self.owner)
        _key, raw = api_key(self.owner)
        self.auth = {"HTTP_AUTHORIZATION": "Bearer " + raw}

    def link(self, **conditions):
        response = self.client.post(
            f"/api/v1/collections/{self.folder.pk}/links/",
            json.dumps({"label": "Client", **conditions}),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY=uuid.uuid4().hex,
            **self.auth,
        )
        self.assertEqual(response.status_code, 201, response.content)
        return ShareLink.objects.get(pk=response.json()["id"])

    def replace(self, contents=b"new version"):
        add_files(
            self.owner,
            self.folder.pk,
            [SimpleUploadedFile("note.txt", contents)],
            ["Dossier/note.txt"],
            replace_files=True,
        )

    def contents(self, link):
        with download_bundle(link) as handle, zipfile.ZipFile(handle) as archive:
            return archive.read("Dossier/note.txt")

    def test_pinned_link_keeps_original_and_live_link_follows_replacement(self):
        pinned = self.link()
        live = self.link(follow_updates=True)
        self.replace()
        self.assertEqual(self.contents(pinned), b"hello")
        self.assertEqual(self.contents(live), b"new version")
        self.assertEqual(self.folder.files.count(), 1)
        self.assertEqual(Asset.all_objects.filter(collection=self.folder).count(), 2)
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.used_bytes, 16)
        self.assertEqual(files_for(pinned).get().size, 5)
        self.assertEqual(files_for(live).get().size, 11)
        page = (
            self.client.get(pinned.public_url.split("http://testserver")[-1])
            if pinned.public_url.startswith("http://testserver")
            else self.client.get("/s/" + pinned.public_url.split("/s/")[1])
        )
        self.assertContains(page, "5")
        self.assertEqual(page.context["share_size"], 5)

    def test_snapshot_reuse_and_owner_restore_without_duplicate_storage(self):
        first, second = self.link(), self.link()
        self.assertEqual(first.revision_id, second.revision_id)
        self.replace()
        newest = self.link()
        self.assertNotEqual(first.revision_id, newest.revision_id)
        restore_revision(self.owner, self.folder.pk, first.revision_id)
        self.assertEqual(self.folder.files.get().blob.read(), b"hello")
        self.assertEqual(self.contents(newest), b"new version")
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.used_bytes, 16)
        remove_revision(self.owner, self.folder.pk, newest.revision_id)
        newest.refresh_from_db()
        self.assertIsNotNone(newest.revoked_at)

    def test_legacy_link_requires_explicit_freeze_and_keeps_url_counter(self):
        legacy = ShareLink.objects.create(
            collection=self.folder, label="Old", downloads=2
        )
        with self.assertRaises(ValidationError):
            self.replace()
        url = legacy.public_url
        self.client.force_login(self.owner)
        self.assertEqual(
            self.client.post(
                f"/collections/{self.folder.pk}/versions/", {"action": "freeze"}
            ).status_code,
            302,
        )
        legacy.refresh_from_db()
        self.assertIsNotNone(legacy.revision_id)
        self.assertEqual((legacy.public_url, legacy.downloads), (url, 2))
        self.replace()
        self.assertEqual(self.contents(legacy), b"hello")

    def test_selective_original_and_invalid_selection_do_not_leak_other_files(self):
        add_files(
            self.owner,
            self.folder.pk,
            [SimpleUploadedFile("extra.txt", b"other")],
            ["extra.txt"],
        )
        link = self.link(max_downloads=2)
        path = "/s/" + link.public_url.split("/s/")[1] + "download/"
        asset = self.folder.files.get(path="Dossier/note.txt")
        bad = self.client.post(path, {"selection": "1", "assets": [str(uuid.uuid4())]})
        self.assertNotEqual(bad.status_code, 200)
        link.refresh_from_db()
        self.assertEqual(link.downloads, 0)
        response = self.client.post(path, {"selection": "1", "assets": [str(asset.pk)]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"hello")
        self.assertIn("note.txt", response["Content-Disposition"])
        link.refresh_from_db()
        self.assertEqual(link.downloads, 1)

    def test_changed_live_collection_does_not_consume_download(self):
        link = self.link(follow_updates=True, max_downloads=1)
        original = zipfile.ZipFile.write
        changed = False

        def replace_during_zip(archive, *args, **kwargs):
            nonlocal changed
            result = original(archive, *args, **kwargs)
            if not changed:
                changed = True
                self.replace()
            return result

        with (
            patch.object(zipfile.ZipFile, "write", replace_during_zip),
            self.assertRaises(ValidationError),
        ):
            download_bundle(link)
        link.refresh_from_db()
        self.assertEqual(link.downloads, 0)

    def test_other_collection_revision_and_collaborator_restore_are_denied(self):
        link = self.link()
        member = self.account("colleague@example.com")
        CollectionAccess.objects.create(
            collection=self.folder, user=member, role="collaborator"
        )
        self.client.force_login(member)
        self.assertEqual(
            self.client.post(
                f"/collections/{self.folder.pk}/versions/",
                {
                    "action": "restore",
                    "revision": str(link.revision_id),
                    "confirm": "restore",
                },
            ).status_code,
            404,
        )
        private = self.collection(self.owner)
        response = self.client.post(
            f"/api/v1/collections/{private.pk}/links/",
            json.dumps(
                {"label": "Wrong revision", "revision_id": str(link.revision_id)}
            ),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY=uuid.uuid4().hex,
            **self.auth,
        )
        self.assertEqual(response.status_code, 400)
