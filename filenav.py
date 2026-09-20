#!/usr/bin/env python3
"""FileNav: inventory every file (and archive-member) on disk into one JSON file.

Usage:
    python filenav.py output.json
    python filenav.py output.json --root D:\\Photos
    python filenav.py output.json --root all          (every ready drive)
    python filenav.py C:\\out\\inventory.json --root C:\\ D:\\

For each file: path, filename, created/modified timestamps, size, and a hash
(sha256 by default) usable for validation and duplicate detection. Archive
files (.zip/.tar*/.7z/.rar) are also opened and every member recorded, including
archives nested inside archives. Images and videos additionally get EXIF/
metadata such as GPS location, capture date, device, and (for video) duration.

Folders containing a file named ".filenav-skip" are skipped entirely, along
with everything under them. A built-in default list of noise directories
(.git, node_modules, __pycache__, Recycle Bin, etc.) is also skipped unless
--no-default-ignores is passed; see --ignore-dirs to add more.
"""

import argparse
import os
import platform
import sys
import tempfile
import time
from datetime import datetime

from filenavlib.config import (
    DEFAULT_HASH_ALGO,
    DEFAULT_IGNORE_DIR_NAMES,
    DEFAULT_MAX_ARCHIVE_DEPTH,
    DEFAULT_MAX_HASH_SIZE_MB,
    DEFAULT_MAX_NESTED_EXTRACT_MB,
    DEFAULT_ROOTS,
)
from filenavlib.drives import resolve_roots
from filenavlib.scanner import ScanOptions, walk_root
from filenavlib.writer import JsonScanWriter


def resolve_output_path(output_arg, script_dir):
    """A bare filename lands next to this script; anything with a directory
    component in it (relative or absolute) is used as given."""
    if os.path.dirname(output_arg):
        return os.path.abspath(output_arg)
    return os.path.join(script_dir, output_arg)


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("output", help="Output JSON filename (or path). Bare filenames are written next to this script.")
    parser.add_argument(
        "--root", nargs="*", default=DEFAULT_ROOTS,
        help=f"Root path(s) to scan, or 'all' for every ready drive. Default: {DEFAULT_ROOTS[0]}",
    )
    parser.add_argument("--hash-algo", default=DEFAULT_HASH_ALGO, help="hashlib algorithm name (default: sha256)")
    parser.add_argument("--max-hash-size-mb", type=float, default=DEFAULT_MAX_HASH_SIZE_MB,
                         help="Files larger than this are recorded without a hash")
    parser.add_argument("--max-nested-extract-mb", type=float, default=DEFAULT_MAX_NESTED_EXTRACT_MB,
                         help="Nested archives larger than this are not expanded")
    parser.add_argument("--max-archive-depth", type=int, default=DEFAULT_MAX_ARCHIVE_DEPTH,
                         help="Max archive-in-archive nesting to expand")
    parser.add_argument("--no-media", action="store_true", help="Skip image/video metadata extraction (faster)")
    parser.add_argument(
        "--ignore-dirs", nargs="*", default=[],
        help="Extra directory names to skip entirely (case-insensitive, matched by basename), "
             "in addition to the built-in default list",
    )
    parser.add_argument(
        "--no-default-ignores", action="store_true",
        help=f"Disable the built-in default ignore list ({', '.join(sorted(DEFAULT_IGNORE_DIR_NAMES))}); "
             "only .filenav-skip and --ignore-dirs still apply",
    )
    parser.add_argument("--progress-every", type=int, default=5000, help="Print progress every N files (0 to disable)")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])

    script_dir = os.path.dirname(os.path.abspath(__file__))
    output_path = resolve_output_path(args.output, script_dir)

    roots = resolve_roots(args.root)
    if not roots:
        print("No usable roots to scan (check --root / drive availability).", file=sys.stderr)
        return 1

    ignore_dir_names = set() if args.no_default_ignores else set(DEFAULT_IGNORE_DIR_NAMES)
    ignore_dir_names |= {name.lower() for name in args.ignore_dirs}

    opts = ScanOptions(
        hash_algo=args.hash_algo,
        max_hash_size_bytes=int(args.max_hash_size_mb * 1024 * 1024),
        max_nested_extract_bytes=int(args.max_nested_extract_mb * 1024 * 1024),
        max_archive_depth=args.max_archive_depth,
        extract_media_metadata=not args.no_media,
        self_path_norm=os.path.normcase(os.path.abspath(output_path)),
        ignore_dir_names=frozenset(ignore_dir_names),
    )

    scan_started = datetime.now()
    meta = {
        "scan_started": scan_started.isoformat(),
        "host": platform.node(),
        "platform": platform.platform(),
        "roots": roots,
        "options": {
            "hash_algo": opts.hash_algo,
            "max_hash_size_bytes": opts.max_hash_size_bytes,
            "max_nested_extract_bytes": opts.max_nested_extract_bytes,
            "max_archive_depth": opts.max_archive_depth,
            "media_metadata": opts.extract_media_metadata,
            "default_ignores_applied": not args.no_default_ignores,
            "ignore_dir_names": sorted(opts.ignore_dir_names),
        },
    }

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    writer = JsonScanWriter(output_path, meta)

    errors = []
    stats = {
        "files": 0, "bytes": 0, "archives": 0, "archive_entries": 0,
        "archive_entry_bytes": 0, "errors": 0,
        "skipped_dirs_marker": 0, "skipped_dirs_ignore_list": 0, "self_deferred": False,
    }

    def emit_record(record):
        writer.write_record(record)
        if args.progress_every and (stats["files"] + stats["archive_entries"]) % args.progress_every == 0:
            print(f"\r  ...{stats['files']} files, {stats['archive_entries']} archive entries, "
                  f"{stats['errors']} errors", end="", file=sys.stderr)

    start_time = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="filenav_scratch_") as scratch_dir:
        for root in roots:
            print(f"Scanning {root} ...", file=sys.stderr)
            if not os.path.isdir(root):
                errors.append({"path": root, "error": "root does not exist or is not accessible"})
                continue
            walk_root(root, opts, emit_record, errors, scratch_dir, stats)
    print(file=sys.stderr)

    elapsed = time.monotonic() - start_time
    scan_finished = datetime.now()

    self_record = {
        "type": "file",
        "path": output_path,
        "filename": os.path.basename(output_path),
        "created": scan_started.isoformat(),
        "modified": scan_finished.isoformat(),
        "size_bytes": writer.current_size(),
        "hash_algo": None,
        "hash": None,
        "hash_skip_reason": "not computed: this is the scan's own output file, still being written when this record was created",
        "self_reference": True,
        "note": "size_bytes is the file's size just before this record and the closing JSON were appended; "
                "the true final on-disk size is slightly larger.",
    }
    writer.write_record(self_record)
    stats["files"] += 1

    summary = {
        "scan_finished": scan_finished.isoformat(),
        "elapsed_seconds": round(elapsed, 2),
        "files_recorded": stats["files"],
        "total_bytes": stats["bytes"],
        "archives_expanded": stats["archives"],
        "archive_entries_recorded": stats["archive_entries"],
        "archive_entry_bytes": stats["archive_entry_bytes"],
        "directories_skipped_via_marker": stats["skipped_dirs_marker"],
        "directories_skipped_via_ignore_list": stats["skipped_dirs_ignore_list"],
        "errors": stats["errors"],
        "output_file": output_path,
    }
    writer.close(errors, summary)

    print(f"Done. {stats['files']} files, {stats['archive_entries']} archive entries, "
          f"{stats['errors']} errors, {elapsed:.1f}s.")
    print(f"Output written to: {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
