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

# Hard ceiling on any single 7z subprocess call (listing an archive, or
# extracting one member). Generous enough for a legitimately huge archive,
# but bounded so a password-protected/corrupt/otherwise-stuck archive can't
# hang the whole scan -- see also stdin=DEVNULL on every 7z call, the actual
# fix for the specific case this was written for (a password prompt reading
# from an inherited, un-attended console and blocking forever).
SEVEN_ZIP_TIMEOUT_SECONDS = 300

# Directory *names* (matched case-insensitively against the basename) that are
# skipped by default -- machine-generated noise, not real user data. A folder
# matching one of these is excluded the same way a .filenav-skip marker would
# exclude it (the folder and everything under it). Override with
# --no-default-ignores / extend with --ignore-dirs.
DEFAULT_IGNORE_DIR_NAMES = {
    # version-control internals
    ".git", ".svn", ".hg",
    # dev/build caches -- reproducible from source, often huge
    "node_modules", "__pycache__", ".venv", "venv", ".tox", ".mypy_cache", ".pytest_cache",
    "dist", "build",
    # Windows system folders -- normally permission-denied anyway
    "system volume information", "$recycle.bin",
    # editor/IDE config
    ".vscode", ".idea",
}
