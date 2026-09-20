"""Shared constants and tunable defaults."""

SKIP_MARKER = ".filenav-skip"

DEFAULT_HASH_ALGO = "sha256"
DEFAULT_MAX_HASH_SIZE_MB = 2048        # files larger than this skip hashing
DEFAULT_MAX_NESTED_EXTRACT_MB = 500    # nested archives larger than this are not expanded
DEFAULT_MAX_ARCHIVE_DEPTH = 10         # zip-in-zip-in-zip... recursion limit
DEFAULT_ROOTS = ["C:\\"]
HASH_CHUNK_SIZE = 1024 * 1024

# Archive extensions this tool knows how to open, mapped to a format family.
# ".tar.gz"-style compound extensions are matched separately (see archives.detect_archive_format).
ARCHIVE_SINGLE_EXTS = {
    ".zip": "zip",
    ".tar": "tar",
    ".tgz": "tar",
    ".tbz2": "tar",
    ".txz": "tar",
    ".7z": "7z",
    ".rar": "rar",
}
ARCHIVE_COMPOUND_EXTS = {
    ".tar.gz": "tar",
    ".tar.bz2": "tar",
    ".tar.xz": "tar",
}

IMAGE_EXTS = {
    ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".gif", ".webp", ".heic", ".heif",
}
VIDEO_EXTS = {
    ".mp4", ".m4v", ".mov", ".avi", ".mkv", ".wmv", ".3gp", ".3g2", ".mpg", ".mpeg", ".webm",
}

# Where to look for a 7-Zip CLI if it isn't on PATH (used for .7z / .rar support).
SEVEN_ZIP_FALLBACK_PATHS = [
    r"C:\Program Files\7-Zip\7z.exe",
    r"C:\Program Files (x86)\7-Zip\7z.exe",
]
