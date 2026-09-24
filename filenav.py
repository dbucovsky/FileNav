#!/usr/bin/env python3
"""FileNav: inventory every file (and archive-member) on disk into one JSON file.

Usage:
    python filenav.py output.json
    python filenav.py output.json --root D:\\Photos
    python filenav.py output.json --root all          (every ready drive)
    python filenav.py C:\\out\\inventory.json --root C:\\ D:\\
    python filenav.py                                  (defaults to output.json)

For each file: path, filename, created/modified timestamps, size, and a hash
(sha256 by default) usable for validation and duplicate detection. Archive
files (.zip/.tar*/.7z/.rar) are also opened and every member recorded, including
archives nested inside archives. Images and videos additionally get EXIF/
metadata such as GPS location, capture date, device, and (for video) duration.

Folders containing a file named ".filenav-skip" are skipped entirely, along
with everything under them. A built-in default list of noise directories
(.git, node_modules, __pycache__, Recycle Bin, etc.) is also skipped unless
--no-default-ignores is passed; see --ignore-dirs to add more.

--exclude-paths PATTERN [PATTERN ...] skips any file or folder (plus its
whole subtree) whose full path matches a glob pattern (*, ?, [seq] -- see
Python's fnmatch), e.g. --exclude-paths "*\\Downloads\\*" "C:\\Temp\\*". Unlike
--ignore-dirs (an exact folder-name match, applied anywhere), this matches
the complete path, so it can target one specific location. Real filesystem
paths only -- does not reach inside archives.

Every file/archive-member that couldn't be read or hashed is logged to a
sidecar "<output>.errors.log" file (one JSON object per line) as it happens;
the main output JSON also embeds a capped sample (see summary.errors /
summary.errors_sample_truncated) for quick inspection without opening a
second file.

By default, both the output and error-log filenames are prefixed with
yyyy-mm-dd-hh-mm-ss_ (the local time the scan started, 24-hour format), so
repeated runs never collide and sort chronologically. Pass --no-timestamp-prefix
to write exactly the filename given.

A diagnostic log is written to "<output>.log" -- one line per directory
entered (so if the scan hangs, the log's last line is where), periodic
progress/rate heartbeats, and warnings for any single operation slower than
--slow-threshold-seconds. Pass --verbose for one line per file too.

Any option (including the output filename and --ignore-dirs) can instead be
set in a JSON file passed via --config PATH:
    {"root": ["C:\\\\", "D:\\\\"], "ignore_dirs": ["Backups"], "verbose": true}
A value explicitly given on the command line always wins over the config
file; the config file always wins over the built-in default. The output
filename itself follows the same rule, falling back to "output.json" if
neither the command line nor the config file sets one.
"""

import argparse
import os
import platform
import sys
import tempfile
import time
from datetime import datetime

from filenavlib.config import (
    ARCHIVE_COMPOUND_EXTS,
    ARCHIVE_SINGLE_EXTS,
    DEFAULT_HASH_ALGO,
    DEFAULT_IGNORE_DIR_NAMES,
    DEFAULT_MAX_ARCHIVE_DEPTH,
    DEFAULT_MAX_HASH_SIZE_MB,
    DEFAULT_MAX_NESTED_EXTRACT_MB,
    DEFAULT_ROOTS,
)
from filenavlib.configfile import ConfigError, load_config
from filenavlib.drives import resolve_roots
from filenavlib.errorlog import ErrorSink
from filenavlib.scanlog import setup_logging
from filenavlib.scanner import ScanOptions, walk_root
from filenavlib.writer import JsonScanWriter

# Used when neither the command line nor --config's file supplies an output
# filename. Still goes through the normal resolve_output_path()/timestamp-prefix
# handling below, same as any other filename.
DEFAULT_OUTPUT_FILENAME = "output.json"

ARCHIVE_FORMAT_CHOICES = sorted(set(ARCHIVE_SINGLE_EXTS.values()) | set(ARCHIVE_COMPOUND_EXTS.values()))

# Real defaults for every --config/CLI-overridable option, used to layer:
# built-in default -> config file -> explicit CLI flag (CLI always wins).
# Kept separate from argparse's own defaults (which are set to SUPPRESS below
# so we can tell "not passed on the CLI" apart from "passed with this value").
ARG_DEFAULTS = {
    "root": DEFAULT_ROOTS,
    "hash_algo": DEFAULT_HASH_ALGO,
    "max_hash_size_mb": float(DEFAULT_MAX_HASH_SIZE_MB),
    "max_nested_extract_mb": float(DEFAULT_MAX_NESTED_EXTRACT_MB),
    "max_archive_depth": DEFAULT_MAX_ARCHIVE_DEPTH,
    "no_media": False,
    "archive_media": False,
    "date_analysis": False,
    "ignore_dirs": [],
    "exclude_paths": [],
    "no_expand_formats": [],
    "no_default_ignores": False,
    "progress_every": 5000,
    "no_timestamp_prefix": False,
    "verbose": False,
    "slow_threshold_seconds": 10.0,
    "log_interval": 60.0,
}

# Types accepted for each option in a --config JSON file; "output" is only
# valid there (not in ARG_DEFAULTS, since it's a required positional on the CLI).
CONFIG_SCHEMA = {
    "output": str,
    "root": list,
    "hash_algo": str,
    "max_hash_size_mb": float,
    "max_nested_extract_mb": float,
    "max_archive_depth": int,
    "no_media": bool,
    "archive_media": bool,
    "date_analysis": bool,
    "ignore_dirs": list,
    "exclude_paths": list,
    "no_expand_formats": list,
    "no_default_ignores": bool,
    "progress_every": int,
    "no_timestamp_prefix": bool,
    "verbose": bool,
    "slow_threshold_seconds": float,
    "log_interval": float,
}


def resolve_output_path(output_arg, script_dir):
    """A bare filename lands next to this script; anything with a directory
    component in it (relative or absolute) is used as given."""
    if os.path.dirname(output_arg):
        return os.path.abspath(output_arg)
    return os.path.join(script_dir, output_arg)


def error_log_path_for(output_path):
    stem, _ext = os.path.splitext(output_path)
    return stem + ".errors.log"


def run_log_path_for(output_path):
    stem, _ext = os.path.splitext(output_path)
    return stem + ".log"


def apply_timestamp_prefix(path, when):
    """Prefix a path's filename (not its directory) with yyyy-mm-dd-hh-mm-ss_,
    using the given local datetime, 24-hour format."""
    directory, basename = os.path.split(path)
    return os.path.join(directory, when.strftime("%Y-%m-%d-%H-%M-%S") + "_" + basename)


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "output", nargs="?", default=None,
        help="Output JSON filename (or path). Bare filenames are written next to this script. "
             "Optional -- falls back to --config's \"output\" if it sets one, otherwise "
             f"\"{DEFAULT_OUTPUT_FILENAME}\".",
    )
    parser.add_argument(
        "--config", default=None,
        help="JSON file to read any of these options from (a value given on the command line "
             "always overrides the same option in this file)",
    )
    parser.add_argument(
        "--root", nargs="*", default=argparse.SUPPRESS,
        help=f"Root path(s) to scan, or 'all' for every ready drive. Default: {DEFAULT_ROOTS[0]}",
    )
    parser.add_argument("--hash-algo", default=argparse.SUPPRESS, help="hashlib algorithm name (default: sha256)")
    parser.add_argument("--max-hash-size-mb", type=float, default=argparse.SUPPRESS,
                         help="Files larger than this are recorded without a hash")
    parser.add_argument("--max-nested-extract-mb", type=float, default=argparse.SUPPRESS,
                         help="Nested archives larger than this are not expanded")
    parser.add_argument("--max-archive-depth", type=int, default=argparse.SUPPRESS,
                         help="Max archive-in-archive nesting to expand")
    parser.add_argument("--no-media", action="store_true", default=argparse.SUPPRESS,
                         help="Skip image/video metadata extraction (faster)")
    parser.add_argument(
        "--archive-media", action="store_true", default=argparse.SUPPRESS,
        help="Also extract EXIF/video metadata for images/videos found inside archives "
             "(off by default: requires decompressing every such member found)",
    )
    parser.add_argument(
        "--date-analysis", action="store_true", default=argparse.SUPPRESS,
        help="Extract dates found in filenames/folder names, and flag files with a future "
             "or OS-placeholder (1970/1980/1601) created/modified/EXIF date (off by default)",
    )
    parser.add_argument(
        "--ignore-dirs", nargs="*", default=argparse.SUPPRESS,
        help="Extra directory names to skip entirely (case-insensitive, matched by basename), "
             "in addition to the built-in default list",
    )
    parser.add_argument(
        "--exclude-paths", nargs="*", default=argparse.SUPPRESS,
        help="Glob pattern(s) (*, ?, [seq]) matched against a file or folder's full path; a match "
             "skips it (and its whole subtree, for a folder). Real filesystem only, not archive contents.",
    )
    parser.add_argument(
        "--no-expand-formats", nargs="*", default=argparse.SUPPRESS, choices=ARCHIVE_FORMAT_CHOICES,
        help=f"Archive format(s) to record as a normal file (path/size/hash/dates) without opening "
             f"and enumerating their contents. Choices: {', '.join(ARCHIVE_FORMAT_CHOICES)} "
             "('tar' covers .tar/.tgz/.tbz2/.txz/.tar.gz/.tar.bz2/.tar.xz).",
    )
    parser.add_argument(
        "--no-default-ignores", action="store_true", default=argparse.SUPPRESS,
        help=f"Disable the built-in default ignore list ({', '.join(sorted(DEFAULT_IGNORE_DIR_NAMES))}); "
             "only .filenav-skip and --ignore-dirs still apply",
    )
    parser.add_argument("--progress-every", type=int, default=argparse.SUPPRESS,
                         help="Print progress every N files (0 to disable)")
    parser.add_argument(
        "--no-timestamp-prefix", action="store_true", default=argparse.SUPPRESS,
        help="Don't prefix the output/error filenames with yyyy-mm-dd-hh-mm-ss_ "
             "(the local time the scan started, 24-hour format). On by default.",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", default=argparse.SUPPRESS,
        help="Log one line per file (not just per directory) to <output>.log",
    )
    parser.add_argument(
        "--slow-threshold-seconds", type=float, default=argparse.SUPPRESS,
        help="Log a warning when hashing/expanding/extracting a single item takes longer than this",
    )
    parser.add_argument(
        "--log-interval", type=float, default=argparse.SUPPRESS,
        help="Seconds between progress heartbeats in <output>.log (0 to disable)",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])
    provided = dict(vars(args))  # only options actually typed on the CLI (thanks to SUPPRESS), plus output/config

    config_values = {}
    if args.config:
        try:
            config_values = load_config(args.config, CONFIG_SCHEMA)
        except ConfigError as exc:
            print(f"Error in --config file: {exc}", file=sys.stderr)
            return 1

    settings = dict(ARG_DEFAULTS)
    settings.update({k: v for k, v in config_values.items() if k != "output"})
    settings.update({k: v for k, v in provided.items() if k in ARG_DEFAULTS})
    for key, value in settings.items():
        setattr(args, key, value)

    args.output = provided.get("output") or config_values.get("output") or DEFAULT_OUTPUT_FILENAME

    scan_started = datetime.now()  # local time the script started running

    script_dir = os.path.dirname(os.path.abspath(__file__))
    output_path = resolve_output_path(args.output, script_dir)
    if not args.no_timestamp_prefix:
        output_path = apply_timestamp_prefix(output_path, scan_started)
    error_log_path = error_log_path_for(output_path)
    run_log_path = run_log_path_for(output_path)

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    logger = setup_logging(run_log_path, verbose=args.verbose)

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
        extract_archive_media_metadata=args.archive_media,
        extract_date_analysis=args.date_analysis,
        scan_reference_time=scan_started,
        slow_threshold_seconds=args.slow_threshold_seconds,
        self_path_norms=frozenset({
            os.path.normcase(os.path.abspath(output_path)),
            os.path.normcase(os.path.abspath(error_log_path)),
            os.path.normcase(os.path.abspath(run_log_path)),
        }),
        ignore_dir_names=frozenset(ignore_dir_names),
        exclude_path_patterns=frozenset(args.exclude_paths),
        no_expand_formats=frozenset(args.no_expand_formats),
    )
    logger.info("Scan starting. roots=%s hash_algo=%s slow_threshold=%.1fs", roots, opts.hash_algo, opts.slow_threshold_seconds)

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
            "archive_media_metadata": opts.extract_archive_media_metadata,
            "date_analysis": opts.extract_date_analysis,
            "default_ignores_applied": not args.no_default_ignores,
            "ignore_dir_names": sorted(opts.ignore_dir_names),
            "exclude_path_patterns": sorted(opts.exclude_path_patterns),
            "no_expand_formats": sorted(opts.no_expand_formats),
            "slow_threshold_seconds": opts.slow_threshold_seconds,
            "log_interval_seconds": args.log_interval,
            "verbose_logging": args.verbose,
            "config_file": args.config,
        },
    }

    writer = JsonScanWriter(output_path, meta)
    errors = ErrorSink(error_log_path)

    stats = {
        "files": 0, "bytes": 0, "archives": 0, "archives_not_expanded": 0, "archive_entries": 0,
        "archive_entry_bytes": 0, "archive_entries_skipped_ignore_list": 0, "archive_media_extracted": 0,
        "date_analysis_flagged": 0,
        "skipped_dirs_marker": 0, "skipped_dirs_ignore_list": 0, "skipped_dirs_path_pattern": 0,
        "skipped_files_path_pattern": 0, "self_deferred": False,
    }

    start_time = time.monotonic()
    heartbeat = {"scratch_dir": None, "last_time": start_time, "last_count": 0}

    def emit_record(record):
        writer.write_record(record)
        count = stats["files"] + stats["archive_entries"]
        if args.progress_every and count % args.progress_every == 0:
            print(f"\r  ...{stats['files']} files, {stats['archive_entries']} archive entries, "
                  f"{errors.count} errors", end="", file=sys.stderr)

        if args.log_interval > 0:
            now = time.monotonic()
            since = now - heartbeat["last_time"]
            if since >= args.log_interval:
                done = count - heartbeat["last_count"]
                rate = done / since if since > 0 else 0.0
                scratch_entries = "?"
                if heartbeat["scratch_dir"]:
                    try:
                        scratch_entries = len(os.listdir(heartbeat["scratch_dir"]))
                    except OSError:
                        pass
                logger.info(
                    "Heartbeat: %d files, %d archive entries, %d errors, "
                    "%.1f items/s over last %.0fs, scratch_dir entries=%s",
                    stats["files"], stats["archive_entries"], errors.count, rate, since, scratch_entries,
                )
                heartbeat["last_time"] = now
                heartbeat["last_count"] = count

    fatal_error = None
    try:
        with tempfile.TemporaryDirectory(prefix="filenav_scratch_") as scratch_dir:
            heartbeat["scratch_dir"] = scratch_dir
            for root in roots:
                print(f"Scanning {root} ...", file=sys.stderr)
                logger.info("Scanning root: %s", root)
                if not os.path.isdir(root):
                    errors.append({"path": root, "error": "root does not exist or is not accessible"})
                    continue
                walk_root(root, opts, emit_record, errors, scratch_dir, stats)
    except KeyboardInterrupt:
        fatal_error = "scan interrupted by user (Ctrl+C)"
        print(f"\n{fatal_error}; finishing the output file with what was scanned so far...", file=sys.stderr)
    except Exception as exc:  # never let an unexpected bug throw away hours of scanning
        fatal_error = f"scan aborted by unexpected error: {exc}"
        print(f"\n{fatal_error}\nFinishing the output file with what was scanned so far...", file=sys.stderr)
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
        "archives_not_expanded": stats["archives_not_expanded"],
        "archive_entries_recorded": stats["archive_entries"],
        "archive_entry_bytes": stats["archive_entry_bytes"],
        "directories_skipped_via_marker": stats["skipped_dirs_marker"],
        "directories_skipped_via_ignore_list": stats["skipped_dirs_ignore_list"],
        "directories_skipped_via_path_pattern": stats["skipped_dirs_path_pattern"],
        "files_skipped_via_path_pattern": stats["skipped_files_path_pattern"],
        "archive_entries_skipped_ignore_list": stats["archive_entries_skipped_ignore_list"],
        "archive_media_extracted": stats["archive_media_extracted"],
        "date_analysis_flagged": stats["date_analysis_flagged"],
        "errors": errors.count,
        "errors_sample_truncated": errors.count > len(errors.sample),
        "error_log_file": error_log_path,
        "log_file": run_log_path,
        "output_file": output_path,
        "aborted": fatal_error is not None,
        "abort_reason": fatal_error,
    }
    errors.close()
    writer.close(errors.sample, summary)

    status = "Aborted" if fatal_error else "Done"
    logger.info("%s. files=%d archive_entries=%d errors=%d elapsed=%.1fs",
                status, stats["files"], stats["archive_entries"], errors.count, elapsed)
    print(f"{status}. {stats['files']} files, {stats['archive_entries']} archive entries, "
          f"{errors.count} errors, {elapsed:.1f}s.")
    print(f"Output written to: {output_path}")
    print(f"Diagnostic log: {run_log_path}")
    for handler in logger.handlers[:]:
        handler.close()
    return 1 if fatal_error else 0


if __name__ == "__main__":
    sys.exit(main())
