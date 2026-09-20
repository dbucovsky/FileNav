"""Drive enumeration for Windows, with a plain fallback for other platforms."""

import ctypes
import os
import string
import sys


def list_windows_drives():
    """Return root paths (e.g. 'C:\\\\') for every ready, accessible drive letter."""
    drives = []
    try:
        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
    except AttributeError:
        bitmask = 0
    for i, letter in enumerate(string.ascii_uppercase):
        if not (bitmask & (1 << i)):
            continue
        root = f"{letter}:\\"
        try:
            drive_type = ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(root))
        except AttributeError:
            drive_type = 0
        # DRIVE_NO_ROOT_DIR = 1, DRIVE_UNKNOWN = 0 -> not usable.
        if drive_type in (0, 1):
            continue
        if not os.path.exists(root):
            continue
        drives.append(root)
    return drives


def resolve_roots(root_args):
    """Turn CLI --root values into concrete filesystem roots.

    The literal value "all" (case-insensitive) expands to every ready drive on
    Windows, or "/" on other platforms. Anything else is used as given.
    """
    if len(root_args) == 1 and root_args[0].strip().lower() == "all":
        if sys.platform.startswith("win"):
            return list_windows_drives()
        return ["/"]
    return list(root_args)
