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
        if self.total > settings.MAX_UPLOAD_BYTES or self.current > settings.MAX_FILE_BYTES:
            self.request.upload_limit_exceeded = True
            raise StopUpload(connection_reset=False)
        return raw_data

    def file_complete(self, file_size):
        return None
