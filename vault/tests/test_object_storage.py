import io
import tarfile
import zipfile

import boto3

try:
    from moto import mock_aws
except ImportError:
    import unittest

    raise unittest.SkipTest(
        "Mock S3 integration runs in development/CI tests, not runtime images."
    ) from None
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.files.storage import default_storage
from django.test import TestCase, override_settings

from vault.backup import export_archive
from vault.models import Asset, Collection, Instance, ShareLink, User
from vault.storage import add_files, download_bundle


@override_settings(
    ANTIVIRUS_REQUIRED=False,
    S3_BUCKET="liasio-fixture",
    S3_ENDPOINT="",
    S3_REGION="us-east-1",
    S3_ADDRESSING_STYLE="auto",
    S3_ENCRYPTION="AES256",
    S3_KMS_KEY_ID="",
    STORAGES={
        "default": {"BACKEND": "vault.object_storage.PrivateS3Storage"},
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    },
)
class ObjectStorageTests(TestCase):
    def setUp(self):
        self.mock = mock_aws()
        self.mock.start()
        self.addCleanup(self.mock.stop)
        self.s3 = boto3.client("s3", region_name="us-east-1")
        self.s3.create_bucket(Bucket="liasio-fixture")
        Instance.objects.update_or_create(pk=1, defaults={"initialized": True})
        self.owner = User.objects.create_user("s3@example.test", "password")
        self.folder = Collection.objects.create(owner=self.owner, title="Private files")

    def test_private_s3_upload_download_and_portable_backup(self):
        add_files(
            self.owner,
            self.folder.pk,
            [SimpleUploadedFile("note.txt", b"private text")],
            ["folder/note.txt"],
        )
        asset = Asset.objects.get()
        with self.assertRaises(NotImplementedError):
            _ = asset.blob.url
        with self.assertRaises(NotImplementedError):
            _ = asset.blob.path
        metadata = self.s3.head_object(Bucket="liasio-fixture", Key=asset.blob.name)
        self.assertEqual(metadata["ServerSideEncryption"], "AES256")
        grants = self.s3.get_object_acl(Bucket="liasio-fixture", Key=asset.blob.name)[
            "Grants"
        ]
        self.assertFalse(any(g["Grantee"].get("URI") for g in grants))
        link = ShareLink.objects.create(collection=self.folder, label="Recipient")
        with download_bundle(link) as output, zipfile.ZipFile(output) as archive:
            self.assertEqual(archive.read("folder/note.txt"), b"private text")
        call_command("verify_storage", stdout=io.StringIO())
        output = io.BytesIO()
        export_archive(output)
        output.seek(0)
        with tarfile.open(fileobj=output, mode="r:gz") as archive:
            self.assertEqual(
                archive.extractfile("files/" + asset.blob.name).read(), b"private text"
            )
        default_storage.delete(asset.blob.name)
        self.assertFalse(default_storage.exists(asset.blob.name))

    def test_read_failure_is_not_an_empty_successful_download(self):
        add_files(
            self.owner,
            self.folder.pk,
            [SimpleUploadedFile("note.txt", b"private")],
            ["note.txt"],
        )
        asset = Asset.objects.get()
        self.s3.delete_object(Bucket="liasio-fixture", Key=asset.blob.name)
        link = ShareLink.objects.create(collection=self.folder, label="Recipient")
        with self.assertRaises(OSError):
            download_bundle(link)
        link.refresh_from_db()
        self.assertEqual(link.downloads, 0)
