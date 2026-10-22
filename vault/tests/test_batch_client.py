import io
import json
import os
import stat
import tempfile
import uuid
from contextlib import redirect_stdout
from email.message import Message
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from django.test import SimpleTestCase

from examples.batches import export_file, main
from examples.upload import API, APIError


class BatchClientTests(SimpleTestCase):
    def test_create_preserves_request_bytes_and_outputs_name_url_pairs(self):
        collection = uuid.uuid4()
        raw = b'{ "name": "Clients", "names": ["A", "B"] }\n'
        result = {
            "id": str(uuid.uuid4()),
            "items": [{"label": "A", "url": "https://example.com/s/test/"}],
        }
        with tempfile.TemporaryDirectory() as directory:
            request = Path(directory) / "request.json"
            request.write_bytes(raw)
            output = io.StringIO()
            with (
                patch.dict(os.environ, {"LINKVAULT_API_KEY": "dummy-local-only"}),
                patch("examples.batches.API") as factory,
                redirect_stdout(output),
            ):
                factory.return_value.request.return_value = result
                main(
                    [
                        "--url",
                        "https://files.example.com",
                        "create",
                        "--collection",
                        str(collection),
                        "--request",
                        str(request),
                        "--idempotency-key",
                        "saved-command-001",
                    ]
                )
            factory.assert_called_once_with(
                "https://files.example.com", "dummy-local-only"
            )
            factory.return_value.request.assert_called_once_with(
                f"/api/v1/collections/{collection}/link-batches/",
                raw,
                key="saved-command-001",
            )
            self.assertEqual(json.loads(output.getvalue()), result)

    def test_export_checks_type_size_and_preserves_existing_private_files(self):
        client = API("https://files.example.com", "dummy-local-only")
        response = MagicMock()
        response.headers = Message()
        response.headers["Content-Type"] = "text/csv; charset=utf-8"
        response.read.return_value = (
            b'"Name";"URL"\n"A";"https://example.com/s/test/"\n'
        )
        response.__enter__.return_value = response
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(client.opener, "open", return_value=response) as opened,
        ):
            destination = Path(directory) / "list.csv"
            batch = uuid.uuid4()
            export_file(client, batch, "csv", destination)
            self.assertEqual(destination.read_bytes(), response.read.return_value)
            self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)
            request = opened.call_args.args[0]
            self.assertEqual(
                request.full_url,
                f"https://files.example.com/api/v1/link-batches/{batch}/export/csv/",
            )
            self.assertEqual(
                request.get_header("Authorization"), "Bearer dummy-local-only"
            )
            with self.assertRaises(FileExistsError):
                export_file(client, batch, "csv", destination)
            target = Path(directory) / "target.csv"
            shortcut = Path(directory) / "shortcut.csv"
            shortcut.symlink_to(destination)
            with self.assertRaises(FileExistsError):
                export_file(client, batch, "csv", shortcut)
            response.headers.replace_header("Content-Type", "text/html")
            with self.assertRaises(ValueError):
                export_file(client, batch, "csv", target)
            self.assertFalse(target.exists())
            response.headers.replace_header("Content-Type", "text/csv")
            response.read.return_value = b"x" * (4 * 1024 * 1024 + 1)
            with self.assertRaises(ValueError):
                export_file(client, batch, "csv", target)
            self.assertFalse(target.exists())

    def test_client_rejects_redirects_instead_of_forwarding_credentials(self):
        client = API("https://files.example.com", "dummy-local-only")
        redirect = HTTPError(
            "https://files.example.com/api/v1/link-batches/",
            302,
            "Found",
            {"Location": "https://other.example/"},
            io.BytesIO(b""),
        )
        with patch.object(client.opener, "open", side_effect=redirect) as opened:
            with self.assertRaises(APIError) as error:
                client.request("/api/v1/link-batches/")
        self.assertEqual(error.exception.status, 302)
        self.assertEqual(opened.call_count, 1)
        for handler in client.opener.handlers:
            if hasattr(handler, "redirect_request"):
                self.assertIsNone(
                    handler.redirect_request(
                        None, None, 302, "Found", {}, "https://other.example/"
                    )
                )

    def test_missing_credentials_and_invalid_origin_do_not_make_requests(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("examples.batches.API") as factory,
            patch("sys.stderr", io.StringIO()),
        ):
            with self.assertRaises(SystemExit):
                main(["--url", "https://files.example.com", "list"])
        factory.assert_not_called()
        for origin in (
            "http://files.example.com",
            "https://files.example.com/path",
            "https://user:password@files.example.com",
            "https://files.example.com/?token=secret",
        ):
            with self.assertRaises(ValueError):
                API(origin, "dummy-local-only")
