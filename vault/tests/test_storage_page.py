from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from vault.models import Asset, Event, ShareLink
from vault.storage import add_files
from .test_security import IsolatedFiles


class StoragePageTests(IsolatedFiles, TestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.account()
        self.item = self.collection(self.owner)
        add_files(self.owner, self.item.pk, [SimpleUploadedFile("larger.txt", b"a" * 128)], ["larger.txt"])
        self.client.force_login(self.owner)

    def test_list_counts_and_size_sort(self):
        response = self.client.get("/storage/")
        self.assertContains(response, "Votre stockage, en clair")
        self.assertEqual(response.context["file_count"], 2)
        self.assertEqual(list(response.context["files"])[0].size, 128)
        self.assertEqual(list(self.client.get("/storage/?sort=smallest").context["files"])[0].size, 5)

    def test_search_is_scoped_to_owner(self):
        other = self.account("other@example.com")
        self.collection(other)
        self.assertEqual(self.client.get("/storage/?q=note").context["page"].paginator.count, 1)

    def test_preview_does_not_delete_then_confirm_revokes_links_and_frees_bytes(self):
        link = ShareLink.objects.create(collection=self.item, label="Active")
        asset = self.item.files.get(path="larger.txt")
        path = Path(asset.blob.path)
        data = {"selected": [str(asset.pk)]}
        preview = self.client.post("/storage/delete/", data)
        self.assertContains(preview, "Supprimer définitivement")
        self.assertEqual(preview.context["freed"], 128)
        self.assertEqual(preview.context["affected"], 1)
        self.assertTrue(Asset.objects.filter(pk=asset.pk).exists())
        data["confirm"] = "delete"
        with self.captureOnCommitCallbacks(execute=True):
            self.assertRedirects(self.client.post("/storage/delete/", data), "/storage/")
        self.owner.refresh_from_db()
        link.refresh_from_db()
        self.assertEqual(self.owner.used_bytes, 5)
        self.assertFalse(path.exists())
        self.assertEqual(self.item.files.count(), 1)
        self.assertIsNotNone(link.revoked_at)
        self.assertTrue(Event.objects.filter(link=link, kind="revoked").exists())

    def test_bulk_delete_and_replayed_confirmation(self):
        ids = [str(pk) for pk in self.item.files.values_list("pk", flat=True)]
        data = {"selected": ids, "confirm": "delete"}
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post("/storage/delete/", data)
        self.assertFalse(self.item.files.exists())
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.used_bytes, 0)
        self.assertEqual(self.client.post("/storage/delete/", data).status_code, 404)

    def test_cross_account_delete_and_forged_id_fail(self):
        other = self.account("other@example.com")
        foreign = self.collection(other).files.get()
        response = self.client.post("/storage/delete/", {"selected": [str(foreign.pk)], "confirm": "delete"})
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Asset.objects.filter(pk=foreign.pk).exists())
        self.assertRedirects(self.client.post("/storage/delete/", {"selected": ["invalid"], "confirm": "delete"}), "/storage/")

    def test_server_statistics_admin_only(self):
        self.assertNotContains(self.client.get("/storage/"), "Stockage du serveur")
        self.owner.is_staff = True
        self.owner.save()
        response = self.client.get("/storage/")
        self.assertContains(response, "Stockage du serveur")
        self.assertEqual(response.context["server_file_count"], 2)
        self.assertGreater(response.context["disk"].total, 0)

    def test_import_page_with_destination_and_other_account_rejected(self):
        response = self.client.get("/upload/", {"collection": str(self.item.pk)})
        self.assertContains(response, "Progression de l’envoi")
        self.assertContains(response, "Choisir un dossier")
        other = self.account("other@example.com")
        foreign = self.collection(other)
        self.assertEqual(self.client.get("/upload/", {"collection": str(foreign.pk)}).status_code, 404)
        self.assertEqual(self.client.get("/upload/", {"collection": "invalid"}).status_code, 404)
