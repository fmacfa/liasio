"""Single-host disk admission shared by uploads and streamed temporary ZIPs.

POSIX locks live as long as their open descriptors, including across worker
processes. A dead worker cannot leave a reservation permanently active.
"""

import fcntl
import json
import os
import shutil
import uuid
from contextlib import contextmanager
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import Sum

from .models import User


class CapacityUnavailable(ValidationError):
    pass


def lease_directory():
    directory = Path(settings.FILE_UPLOAD_TEMP_DIR) / ".capacity"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    return directory


@contextmanager
def capacity_lock():
    descriptor = os.open(
        lease_directory() / "admission.lock", os.O_RDWR | os.O_CREAT, 0o600
    )
    with os.fdopen(descriptor, "r+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def active_leases():
    """Read under capacity_lock; reclaim only descriptors no worker holds."""
    active = []
    for path in lease_directory().glob("*.lease"):
        try:
            descriptor = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
        except FileNotFoundError:
            continue
        with os.fdopen(descriptor, "r+b") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                active.append(json.load(handle))
            else:
                path.unlink(missing_ok=True)
    return active


def check_capacity(required, leases=None):
    """Caller holds capacity_lock. Double counting written bytes is deliberate."""
    leases = active_leases() if leases is None else leases
    reserved = User.objects.aggregate(total=Sum("reserved_bytes"))["total"] or 0
    needed = (
        settings.MIN_FREE_DISK_BYTES
        + 2 * reserved
        + sum(item["bytes"] for item in leases)
        + required
    )
    if shutil.disk_usage(settings.FILE_UPLOAD_TEMP_DIR).free < needed:
        raise CapacityUnavailable(
            "Espace temporaire insuffisant. Réessayez après les transferts en cours ou contactez l’administrateur."
        )


class DiskLease:
    def __init__(self, handle, path):
        self.handle, self.path = handle, path

    def close(self):
        if not self.handle.closed:
            # Unlink while holding our lock; a new admission may proceed only
            # after the caller has already closed its corresponding temp files.
            self.path.unlink(missing_ok=True)
            self.handle.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def reserve_temporary(size, *, owner_id=None, kind="archive"):
    with capacity_lock():
        leases = active_leases()
        if (
            kind == "multipart"
            and sum(
                item.get("owner") == owner_id and item.get("kind") == kind
                for item in leases
            )
            >= 2
        ):
            raise CapacityUnavailable(
                "Deux envois classiques sont déjà en cours pour ce compte."
            )
        check_capacity(size, leases)
        path = lease_directory() / f"{uuid.uuid4().hex}.lease"
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
        handle = os.fdopen(descriptor, "w+b")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX)
            handle.write(
                json.dumps({"bytes": size, "owner": owner_id, "kind": kind}).encode()
            )
            handle.flush()
            return DiskLease(handle, path)
        except BaseException:
            handle.close()
            path.unlink(missing_ok=True)
            raise


class LeasedFile:
    """FileResponse closes this after streaming, not just after ZIP creation."""

    def __init__(self, file, lease):
        self.file, self.lease = file, lease

    def __getattr__(self, name):
        return getattr(self.file, name)

    def close(self):
        try:
            self.file.close()
        finally:
            self.lease.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
