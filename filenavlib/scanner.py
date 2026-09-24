"""Directory walk: applies the .filenav-skip rule, builds a record per file,
and hands each record off to a caller-supplied sink (the JSON writer).
"""

import fnmatch
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime

from . import archives, hashing, media
from .config import IMAGE_EXTS, SKIP_MARKER, VIDEO_EXTS

logger = logging.getLogger("filenav")


@dataclass
class ScanOptions:
    hash_algo: str = "sha256"
    max_hash_size_bytes: int = 2048 * 1024 * 1024
    max_nested_extract_bytes: int = 500 * 1024 * 1024
    max_archive_depth: int = 10
    extract_media_metadata: bool = True
    slow_threshold_seconds: float = 10.0  # log a WARNING when a single operation takes longer than this
    self_path_norms: frozenset = field(default_factory=frozenset)  # this run's own output files
    ignore_dir_names: frozenset = field(default_factory=frozenset)  # lowercased basenames
    exclude_path_patterns: frozenset = field(default_factory=frozenset)  # fnmatch glob patterns, matched
    # against a file or directory's full path (real filesystem only -- does not reach into archives)
    no_expand_formats: frozenset = field(default_factory=frozenset)  # e.g. {"tar"}: record the archive
    # file itself as usual (path/size/hash/dates), but don't open and enumerate its contents
    extract_archive_media_metadata: bool = False  # EXIF/video metadata for images/videos found
    # *inside* archives -- off by default: requires decompressing every such member, which adds
    # real cost on a scan with lots of archived photos/videos


def _iso(ts):
    try:
        return datetime.fromtimestamp(ts).isoformat()
    except (OSError, OverflowError, ValueError):
        return None


def matches_any_pattern(path, patterns):
    """fnmatch is used as-is (not pre-normalized): on Windows it already
    lowercases and folds "/" to "\\" on both the path and the pattern via
    os.path.normcase, so a pattern can be written with either slash style."""
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def should_skip_dir(dirpath, ignore_dir_names=frozenset(), exclude_path_patterns=frozenset()):
    """Return a skip reason ("marker" / "ignore_list" / "path_pattern") or None."""
    if os.path.isfile(os.path.join(dirpath, SKIP_MARKER)):
        return "marker"
    basename = os.path.basename(os.path.normpath(dirpath)).lower()
    if basename in ignore_dir_names:
        return "ignore_list"
    if exclude_path_patterns and matches_any_pattern(dirpath, exclude_path_patterns):
        return "path_pattern"
    return None


def build_file_record(fpath, opts):
    """Return (record, None) on success, or (None, error_dict) on failure."""
    try:
        st = os.stat(fpath)
    except OSError as exc:
        return None, {"path": fpath, "error": f"could not stat file: {exc}"}

    ext = os.path.splitext(fpath)[1].lower()

    hash_start = time.monotonic()
    digest, hash_reason = hashing.hash_file(
        fpath, opts.hash_algo, opts.max_hash_size_bytes, size_hint=st.st_size
    )
    hash_elapsed = time.monotonic() - hash_start
    if hash_elapsed > opts.slow_threshold_seconds:
        logger.warning("Slow hash (%.1fs, %d bytes): %s", hash_elapsed, st.st_size, fpath)

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
        media_start = time.monotonic()
        if ext in IMAGE_EXTS:
            record["image"] = media.get_image_info(fpath)
        elif ext in VIDEO_EXTS:
            record["video"] = media.get_video_info(fpath)
        media_elapsed = time.monotonic() - media_start
        if media_elapsed > opts.slow_threshold_seconds:
            logger.warning("Slow media metadata extraction (%.1fs): %s", media_elapsed, fpath)

    archive_fmt = archives.detect_archive_format(fpath)
    if archive_fmt:
        record["archive_format"] = archive_fmt
        if archive_fmt in opts.no_expand_formats:
            record["archive_expansion_skipped"] = True

    return record, None


def walk_root(root, opts, emit_record, errors, scratch_dir, stats):
    def on_error(exc):
        errors.append({"path": getattr(exc, "filename", None) or root, "error": str(exc)})

    def emit_archive_entry(rec):
        emit_record(rec)
        stats["archive_entries"] += 1
        if rec.get("size_bytes"):
            stats["archive_entry_bytes"] += rec["size_bytes"]

    dir_skip_stat = {
        "marker": "skipped_dirs_marker",
        "ignore_list": "skipped_dirs_ignore_list",
        "path_pattern": "skipped_dirs_path_pattern",
    }

    for dirpath, dirnames, filenames in os.walk(root, topdown=True, onerror=on_error):
        skip_reason = should_skip_dir(dirpath, opts.ignore_dir_names, opts.exclude_path_patterns)
        if skip_reason:
            dirnames[:] = []
            stats[dir_skip_stat[skip_reason]] += 1
            continue

        if opts.ignore_dir_names or opts.exclude_path_patterns:
            kept = []
            for d in dirnames:
                if d.lower() in opts.ignore_dir_names:
                    stats["skipped_dirs_ignore_list"] += 1
                elif opts.exclude_path_patterns and matches_any_pattern(
                    os.path.join(dirpath, d), opts.exclude_path_patterns
                ):
                    stats["skipped_dirs_path_pattern"] += 1
                else:
                    kept.append(d)
            dirnames[:] = kept

        # Directories are far fewer than files, so logging one line per
        # directory (rather than per file) at INFO stays affordable even on
        # a whole-drive scan, while still pinpointing where a hang occurred:
        # if the log goes quiet, the last "Scanning directory" line is where.
        logger.info("Scanning directory (%d files): %s", len(filenames), dirpath)

        for fname in filenames:
            fpath = os.path.join(dirpath, fname)

            if os.path.normcase(os.path.abspath(fpath)) in opts.self_path_norms:
                stats["self_deferred"] = True
                continue

            if opts.exclude_path_patterns and matches_any_pattern(fpath, opts.exclude_path_patterns):
                stats["skipped_files_path_pattern"] += 1
                continue

            logger.debug("File: %s", fpath)

            # One misbehaving file (corrupt archive, unreadable metadata, odd
            # encoding, ...) must never take the rest of a multi-hour scan down.
            try:
                record, error = build_file_record(fpath, opts)
                if error:
                    errors.append(error)
                    continue

                emit_record(record)
                stats["files"] += 1
                stats["bytes"] += record["size_bytes"]

                if record.get("archive_format"):
                    if record.get("archive_expansion_skipped"):
                        stats["archives_not_expanded"] += 1
                    else:
                        stats["archives"] += 1
                        archives.process_archive_file(
                            fpath, [fpath], 1, opts, emit_archive_entry, errors, scratch_dir, stats
                        )
            except Exception as exc:
                errors.append({"path": fpath, "error": f"unexpected error processing file: {exc}"})
