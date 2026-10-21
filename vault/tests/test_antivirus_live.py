"""Opt-in integration tests; run against an isolated ClamAV daemon in CI."""
import os
import unittest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from vault.storage import scan


@unittest.skipUnless(os.environ.get("RUN_CLAMAV_INTEGRATION") == "1", "Live ClamAV integration is opt-in")
class LiveClamTests(SimpleTestCase):
    def test_clean_file(self):
        self.assertTrue(scan(SimpleUploadedFile("clean.txt", b"LinkVault harmless integration fixture")))

    def test_eicar_signature_refused(self):
        # The industry-standard harmless antivirus test string, not malware.
        signature = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
        with self.assertRaises(ValidationError):
            scan(SimpleUploadedFile("eicar.txt", signature))
