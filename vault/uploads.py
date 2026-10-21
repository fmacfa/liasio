import shutil

from django.conf import settings
from django.core.files.uploadhandler import FileUploadHandler, StopUpload


class BoundedUploadHandler(FileUploadHandler):
    """Enforce byte limits while receiving, before materializing entire uploads."""

    def __init__(self, request=None):
        super().__init__(request)
        self.total = 0
        self.current = 0

    def new_file(self, *args, **kwargs):
        super().new_file(*args, **kwargs)
        self.current = 0

    def receive_data_chunk(self, raw_data, start):
        self.total += len(raw_data)
        self.current += len(raw_data)
        if (
            self.total > settings.MAX_MULTIPART_BYTES
            or self.current > settings.MAX_FILE_BYTES
            or self.total > getattr(self.request, "upload_byte_budget", 0)
            or shutil.disk_usage(settings.FILE_UPLOAD_TEMP_DIR).free
            < settings.MIN_FREE_DISK_BYTES + len(raw_data)
        ):
            self.request.upload_limit_exceeded = True
            raise StopUpload(connection_reset=True)
        return raw_data

    def file_complete(self, file_size):
        return None
