import hashlib
from datetime import timedelta
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from .test_security import IsolatedFiles

from vault.models import Asset, Collection, FileRequest, Instance, UploadSession, User


@override_settings(ANTIVIRUS_REQUIRED=False)
class FileRequestTests(IsolatedFiles, TestCase):
    def setUp(self):
        super().setUp()
        Instance.objects.update_or_create(pk=1, defaults={"initialized": True})
        self.owner = User.objects.create_user(
            "owner@example.test", "password", quota_bytes=1024**2
        )
        self.request = FileRequest.objects.create(
            owner=self.owner,
            title="Client files",
            max_bytes=10,
            max_submissions=2,
            expires_at=timezone.now() + timedelta(days=1),
        )
        self.path = (
            "/file-requests/" + self.request.public_url.rstrip("/").split("/")[-1] + "/"
        )
        self.visitor = Client()
        self.other = Client()
        self.bytes = b"hello"
        self.checksum = hashlib.sha256(self.bytes).hexdigest()

    def start(self, client=None):
        return (client or self.visitor).post(
            self.path + "start/",
            {
                "files": [
                    {
                        "path": "folder/hello.txt",
                        "size": 5,
                        "fingerprint": self.checksum,
                    }
                ]
            },
            content_type="application/json",
        )

    def test_anonymous_resumable_upload_is_private_and_counted_once(self):
        self.assertContains(self.visitor.get(self.path), "Client files")
        response = self.start()
        self.assertEqual(response.status_code, 201, response.content)
        session = response.json()
        path = self.path + "uploads/" + session["id"] + "/"
        self.assertEqual(self.other.get(path + "status/").status_code, 404)
        self.assertEqual(self.other.get(self.path + "pending/").json()["sessions"], [])
        self.assertEqual(
            self.visitor.get(self.path + "pending/").json()["sessions"][0]["id"],
            session["id"],
        )
        chunk = path + "parts/" + session["parts"][0]["id"] + "/chunk/"
        result = self.visitor.post(
            chunk,
            self.bytes,
            content_type="application/octet-stream",
            HTTP_UPLOAD_OFFSET="0",
            HTTP_UPLOAD_CHECKSUM=self.checksum,
        )
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual(self.visitor.post(path + "finish/").status_code, 200)
        self.assertEqual(self.visitor.post(path + "finish/").status_code, 200)
        self.request.refresh_from_db()
        self.owner.refresh_from_db()
        self.assertEqual(self.request.received_bytes, 5)
        self.assertEqual(self.request.received_count, 1)
        self.assertEqual(self.owner.used_bytes, 5)
        self.assertEqual(self.owner.reserved_bytes, 0)
        self.assertEqual(Asset.objects.get().path, "folder/hello.txt")
        self.assertNotIn("_auth_user_id", self.visitor.session)
        self.assertEqual(
            self.visitor.get(
                f"/collections/{Asset.objects.get().collection_id}/"
            ).status_code,
            302,
        )

    def test_pending_reservations_count_and_revoke_releases_quota(self):
        self.assertEqual(self.start().status_code, 201)
        self.assertEqual(self.start(self.other).status_code, 201)
        count = Collection.objects.count()
        self.assertEqual(self.start().status_code, 400)
        self.assertEqual(Collection.objects.count(), count)
        self.client.force_login(self.owner)
        self.assertEqual(
            self.client.post(
                f"/requests/{self.request.pk}/", {"action": "revoke"}
            ).status_code,
            302,
        )
        self.assertFalse(UploadSession.objects.filter(state="pending").exists())
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.reserved_bytes, 0)
        self.assertEqual(self.start().status_code, 404)

    def test_expiration_and_csrf_guard(self):
        strict = Client(enforce_csrf_checks=True)
        self.assertEqual(self.start(strict).status_code, 403)
        row = self.start().json()
        self.request.expires_at = timezone.now() - timedelta(seconds=1)
        self.request.save()
        path = self.path + "uploads/" + row["id"] + "/finish/"
        self.assertEqual(self.visitor.post(path).status_code, 404)
        self.assertFalse(Asset.objects.exists())

    def test_malware_failure_preserves_reservation_and_no_files_visible(self):
        row = self.start().json()
        path = self.path + "uploads/" + row["id"] + "/"
        self.visitor.post(
            path + "parts/" + row["parts"][0]["id"] + "/chunk/",
            self.bytes,
            content_type="application/octet-stream",
            HTTP_UPLOAD_OFFSET="0",
            HTTP_UPLOAD_CHECKSUM=self.checksum,
        )
        with patch("vault.resumable.add_files", side_effect=ValidationError("malware")):
            self.assertEqual(self.visitor.post(path + "finish/").status_code, 400)
        self.assertFalse(Asset.objects.exists())
        self.request.refresh_from_db()
        self.assertEqual(self.request.received_count, 0)
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.reserved_bytes, 5)
