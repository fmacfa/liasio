"""Private S3 objects, streamed through the application's authorization boundary."""

from boto3.s3.transfer import TransferConfig
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings
from django.core.files import File
from storages.backends.s3 import S3Storage


class PrivateS3Storage(S3Storage):
    def __init__(self, **kwargs):
        kwargs.setdefault("bucket_name", settings.S3_BUCKET)
        kwargs.setdefault("endpoint_url", settings.S3_ENDPOINT or None)
        kwargs.setdefault("region_name", settings.S3_REGION)
        kwargs.setdefault("default_acl", None)
        kwargs.setdefault("custom_domain", None)
        kwargs.setdefault("file_overwrite", False)
        kwargs.setdefault("max_memory_size", 8 * 1024**2)
        kwargs.setdefault(
            "client_config",
            Config(
                connect_timeout=5,
                read_timeout=30,
                retries={"max_attempts": 2, "mode": "standard"},
                signature_version="s3v4",
                s3={"addressing_style": settings.S3_ADDRESSING_STYLE},
            ),
        )
        kwargs.setdefault(
            "transfer_config",
            TransferConfig(
                use_threads=False,
                multipart_chunksize=8 * 1024**2,
                multipart_threshold=8 * 1024**2,
                max_io_queue=4,
            ),
        )
        super().__init__(**kwargs)

    def url(self, name, *args, **kwargs):
        raise NotImplementedError(
            "Original objects never have browser-accessible URLs."
        )

    def get_object_parameters(self, name):
        parameters = {
            "ContentType": "application/octet-stream",
            "CacheControl": "private, no-store",
            "ServerSideEncryption": settings.S3_ENCRYPTION,
        }
        if settings.S3_KMS_KEY_ID:
            parameters["SSEKMSKeyId"] = settings.S3_KMS_KEY_ID
        return parameters

    def _open(self, name, mode="rb"):
        if mode != "rb":
            raise ValueError("Use save() for immutable objects.")
        try:
            response = self.connection.meta.client.get_object(
                Bucket=self.bucket_name, Key=self._normalize_name(name)
            )
        except (BotoCoreError, ClientError):
            raise OSError("Private object unavailable") from None
        result = File(response["Body"], name=name)
        result.size = response["ContentLength"]
        return result

    def _save(self, name, content):
        try:
            return super()._save(name, content)
        except (BotoCoreError, ClientError):
            raise OSError("Private object write failed") from None

    def delete(self, name):
        try:
            return super().delete(name)
        except (BotoCoreError, ClientError):
            raise OSError("Private object deletion failed") from None

    def exists(self, name):
        try:
            return super().exists(name)
        except (BotoCoreError, ClientError):
            raise OSError("Private object lookup failed") from None

    def size(self, name):
        try:
            return super().size(name)
        except (BotoCoreError, ClientError):
            raise OSError("Private object lookup failed") from None
