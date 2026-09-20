"""Directory walk: applies the .filenav-skip rule, builds a record per file,
and hands each record off to a caller-supplied sink (the JSON writer).
"""

import os
from dataclasses import dataclass
from datetime import datetime

from . import archives, hashing, media
from .config import IMAGE_EXTS, SKIP_MARKER, VIDEO_EXTS


@dataclass
class ScanOptions:
    hash_algo: str = "sha256"
    max_hash_size_bytes: int = 2048 * 1024 * 1024
    max_nested_extract_bytes: int = 500 * 1024 * 1024
    max_archive_depth: int = 10
    extract_media_metadata: bool = True
    self_path_norm: str = ""


def _iso(ts):
    try:
        return datetime.fromtimestamp(ts).isoformat()
    except (OSError, OverflowError, ValueError):
        return None


def should_skip_dir(dirpath):
    return os.path.isfile(os.path.join(dirpath, SKIP_MARKER))


def build_file_record(fpath, opts):
    """Return (record, None) on success, or (None, error_dict) on failure."""
    try:
        st = os.stat(fpath)
    except OSError as exc:
        return None, {"path": fpath, "error": f"could not stat file: {exc}"}

    ext = os.path.splitext(fpath)[1].lower()
    digest, hash_reason = hashing.hash_file(
        fpath, opts.hash_algo, opts.max_hash_size_bytes, size_hint=st.st_size
    )

    record = {
        "type": "file",
        "path": fpath,
        "filename": os.path.basename(fpath),
        "created": _iso(st.st_ctime),
        "modified": _iso(st.st_mtime),
        "size_bytes": st.st_size,
        "hash_algo": opts.hash_algo,
        "hash": digest,
        "hash_skip_reason": hash_reason,
    }

    if opts.extract_media_metadata:
        if ext in IMAGE_EXTS:
            record["image"] = media.get_image_info(fpath)
        elif ext in VIDEO_EXTS:
            record["video"] = media.get_video_info(fpath)

    archive_fmt = archives.detect_archive_format(fpath)
    if archive_fmt:
        record["archive_format"] = archive_fmt

    return record, None


def walk_root(root, opts, emit_record, errors, scratch_dir, stats):
    def on_error(exc):
        errors.append({"path": getattr(exc, "filename", None) or root, "error": str(exc)})

    def emit_archive_entry(rec):
        emit_record(rec)
        stats["archive_entries"] += 1
        if rec.get("size_bytes"):
            stats["archive_entry_bytes"] += rec["size_bytes"]

    for dirpath, dirnames, filenames in os.walk(root, topdown=True, onerror=on_error):
        if should_skip_dir(dirpath):
            dirnames[:] = []
            stats["skipped_dirs"] += 1
            continue

        for fname in filenames:
            fpath = os.path.join(dirpath, fname)

            if os.path.normcase(os.path.abspath(fpath)) == opts.self_path_norm:
                stats["self_deferred"] = True
                continue

            record, error = build_file_record(fpath, opts)
            if error:
                errors.append(error)
                stats["errors"] += 1
                continue

            emit_record(record)
            stats["files"] += 1
            stats["bytes"] += record["size_bytes"]

            if record.get("archive_format"):
                stats["archives"] += 1
                archives.process_archive_file(
                    fpath, [fpath], 1, opts, emit_archive_entry, errors, scratch_dir
                )
