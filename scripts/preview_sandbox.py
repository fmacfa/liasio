"""Landlock filesystem confinement for the Linux production decoder.

Requires ABI 3 (Linux 6.2+, or a backport) to mediate truncation too. Failure
is fatal to this preview: never parse untrusted media without the requested
boundary. The outer container still supplies network/PID/memory isolation.
"""

import ctypes
import os
from pathlib import Path
import platform
import sys


class Ruleset(ctypes.Structure):
    _fields_ = [
        ("filesystem", ctypes.c_uint64),
        ("network", ctypes.c_uint64),
        ("scoped", ctypes.c_uint64),
    ]


class PathRule(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("allowed", ctypes.c_uint64), ("parent", ctypes.c_int32)]


def confine(directory):
    if sys.platform != "linux" or platform.machine() not in {"x86_64", "aarch64"}:
        raise RuntimeError("Production previews require Linux Landlock ABI 3+")
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long

    def syscall(number, *args):
        result = libc.syscall(ctypes.c_long(number), *args)
        if result < 0:
            raise OSError(ctypes.get_errno(), "Preview sandbox unavailable")
        return result

    abi = syscall(444, None, ctypes.c_size_t(0), ctypes.c_uint(1))
    if abi < 3:
        raise RuntimeError("Production previews require Landlock ABI 3+")
    # All filesystem rights through ABI 3, including REFER and TRUNCATE.
    # Newer kernels also prevent signalling processes outside this sandbox.
    rules = Ruleset((1 << 15) - 1, 0, 3 if abi >= 6 else 0)
    ruleset = syscall(
        444,
        ctypes.byref(rules),
        ctypes.c_size_t(ctypes.sizeof(rules)),
        ctypes.c_uint(0),
    )
    read = (1 << 2) | (1 << 3)  # READ_FILE | READ_DIR
    writable = read | sum(1 << bit for bit in (1, 4, 5, 7, 8, 13, 14))
    try:
        paths = {Path(p).resolve(): read for p in sys.path if p and Path(p).is_dir()}
        for p in ("/lib", "/lib64", "/usr/lib", "/etc/ld.so.cache"):
            if Path(p).exists():
                paths[Path(p).resolve()] = read
        paths[Path(directory).resolve()] = writable
        for path, allowed in paths.items():
            if path.is_file():
                allowed &= ~(1 << 3)
            descriptor = os.open(path, os.O_PATH | os.O_CLOEXEC)
            try:
                rule = PathRule(allowed, descriptor)
                syscall(
                    445,
                    ctypes.c_int(ruleset),
                    ctypes.c_int(1),
                    ctypes.byref(rule),
                    ctypes.c_uint(0),
                )
            finally:
                os.close(descriptor)
        if libc.prctl(38, 1, 0, 0, 0):  # PR_SET_NO_NEW_PRIVS
            raise OSError(ctypes.get_errno(), "Cannot restrict preview privileges")
        syscall(446, ctypes.c_int(ruleset), ctypes.c_uint(0))
    finally:
        os.close(ruleset)
