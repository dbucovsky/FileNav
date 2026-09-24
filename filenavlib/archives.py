"""Archive enumeration: zip / tar via the standard library, .7z / .rar via the
7-Zip CLI. Handles archives nested inside archives (zip-in-zip-in-zip, a zip
inside a 7z, etc.) up to a configurable depth, by extracting nested members
to a scratch temp directory and recursing.
"""

import io
import logging
import os
import shutil
import subprocess
import tarfile
import tempfile
import time
import zipfile
from datetime import datetime

from . import hashing, media
from .config import (
    ARCHIVE_COMPOUND_EXTS,
    ARCHIVE_SINGLE_EXTS,
    IMAGE_EXTS,
    SEVEN_ZIP_FALLBACK_PATHS,
    SEVEN_ZIP_TIMEOUT_SECONDS,
    VIDEO_EXTS,
)

logger = logging.getLogger("filenav")

_seven_zip_path = "unset"  # sentinel so we only probe once per process


def find_seven_zip():
    global _seven_zip_path
    if _seven_zip_path != "unset":
        return _seven_zip_path

    found = shutil.which("7z") or shutil.which("7z.exe")
    if not found:
        for candidate in SEVEN_ZIP_FALLBACK_PATHS:
            if os.path.isfile(candidate):
                found = candidate
                break
    _seven_zip_path = found
    return found


def detect_archive_format(filename):
    lower = filename.lower()
    for ext, fmt in ARCHIVE_COMPOUND_EXTS.items():
        if lower.endswith(ext):
            return fmt
    ext = os.path.splitext(lower)[1]
    return ARCHIVE_SINGLE_EXTS.get(ext)


def _is_ignored_entry(name, ignore_dir_names):
    """True if any path segment of an archive member's internal name matches
    the ignore-dir list -- e.g. "pkg/__pycache__/mod.pyc" is ignored the same
    way a real __pycache__ folder on disk would be, symmetric with scanner.py.
    """
    if not ignore_dir_names:
        return False
    return any(seg.lower() in ignore_dir_names for seg in name.split("/")[:-1])


def _media_kind_for_name(name):
    ext = os.path.splitext(name)[1].lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    return None


def _extract_media_from_bytes(data, name, scratch_dir):
    """Write an in-memory member's bytes to a temp file and run the same
    EXIF/video-metadata extraction used for real on-disk files against it.
    Used for tar, where the bytes are already in hand from the single
    sequential listing/hashing pass (see _list_tar_entries) -- extracting a
    second time via a fresh tarfile.open() would reintroduce the same O(n^2)
    reopening cost that pass exists to avoid.
    """
    kind = _media_kind_for_name(name)
    if kind is None:
        return None
    fd, temp_path = tempfile.mkstemp(suffix=os.path.splitext(name)[1], dir=scratch_dir)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        info = media.get_image_info(temp_path) if kind == "image" else media.get_video_info(temp_path)
        return {kind: info}
    finally:
        try:
            os.remove(temp_path)
        except OSError:
            pass


def _extract_media_for_entry(fs_path, fmt, entry, scratch_dir):
    """Same as _extract_media_from_bytes, but for zip/7z/rar: extracts the
    member to a real temp file via the existing generic extraction helper
    (safe to reopen per-member for these formats, unlike tar) and runs the
    same EXIF/video-metadata extraction against it.
    """
    kind = _media_kind_for_name(entry["name"])
    if kind is None:
        return None
    extracted_path, dest_dir = _extract_member_to_temp(fs_path, fmt, entry, scratch_dir)
    if not extracted_path:
        shutil.rmtree(dest_dir, ignore_errors=True)
        return None
    try:
        info = media.get_image_info(extracted_path) if kind == "image" else media.get_video_info(extracted_path)
        return {kind: info}
    finally:
        shutil.rmtree(dest_dir, ignore_errors=True)


def _run_7z(args):
    seven_zip = find_seven_zip()
    if not seven_zip:
        raise RuntimeError("7-Zip executable not found")
    try:
        proc = subprocess.run(
            [seven_zip] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,  # a password-protected archive must fail fast, never prompt-and-hang
            check=False,
            timeout=SEVEN_ZIP_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"7z timed out after {SEVEN_ZIP_TIMEOUT_SECONDS}s (possibly password-protected)") from exc
    stdout = proc.stdout.decode("utf-8", errors="replace")
    if proc.returncode not in (0, 1):  # 1 = "warning" (e.g. some entries skipped), still usable
        stderr = proc.stderr.decode("utf-8", errors="replace")
        raise RuntimeError(f"7z exited with code {proc.returncode}: {stderr.strip()[:500]}")
    return stdout


def _list_7z_entries(path):
    stdout = _run_7z(["l", "-slt", "-y", "--", path])
    marker = "----------"
    idx = stdout.find(marker)
    body = stdout[idx + len(marker):] if idx != -1 else ""

    entries = []
    for block in body.split("\n\n"):
        block = block.strip("\r\n")
        if not block.strip():
            continue
        fields = {}
        for line in block.splitlines():
            if " = " in line:
                key, _, value = line.partition(" = ")
                fields[key.strip()] = value.strip()
        if "Path" not in fields:
            continue
        is_dir = fields.get("Folder") == "+" or fields.get("Attributes", "").startswith("D")
        try:
            size = int(fields.get("Size") or 0)
        except ValueError:
            size = 0
        crc = fields.get("CRC") or None
        entries.append({
            "name": fields["Path"].replace("\\", "/"),
            "size": size,
            "mtime": fields.get("Modified") or None,
            "crc": crc.lower() if crc else None,
            "is_dir": is_dir,
        })
    return entries


def _list_zip_entries(path):
    entries = []
    with zipfile.ZipFile(path) as zf:
        for info in zf.infolist():
            is_dir = info.filename.endswith("/") or (info.external_attr >> 16) & 0o170000 == 0o040000
            mtime = None
            try:
                mtime = datetime(*info.date_time).isoformat()
            except (ValueError, TypeError):
                pass
            entries.append({
                "name": info.filename,
                "size": info.file_size,
                "mtime": mtime,
                "crc": f"{info.CRC & 0xFFFFFFFF:08x}",
                "is_dir": is_dir,
            })
    return entries


def _list_tar_entries(path, opts, scratch_dir):
    """List tar members AND hash them (and, if enabled, extract image/video
    metadata for them) in the same single sequential pass.

    A compressed tar (.tar.gz/.tar.bz2/.tar.xz) is not seekable: reaching
    member N requires decompressing from byte zero. hash_member() reopening
    the tar per member turned a 143,770-entry real-world .tar.bz2 into an
    O(n^2) operation -- observed taking ~50s *per small file*, on pace for
    months to finish one archive. Iterating the TarFile object directly
    (rather than tf.getmembers() up front) and extracting+hashing each file
    member right as it's encountered keeps this to one sequential O(n) pass,
    the same cost as listing alone. Media metadata extraction piggybacks on
    the same pass for the same reason -- a second per-member reopen just to
    check EXIF would reintroduce the exact bug this function exists to avoid.
    """
    entries = []
    with tarfile.open(path) as tf:
        for member in tf:
            if not member.isfile():
                if member.isdir():
                    entries.append({"name": member.name, "size": 0, "mtime": None, "crc": None, "is_dir": True})
                continue
            mtime = None
            try:
                mtime = datetime.fromtimestamp(member.mtime).isoformat()
            except (ValueError, OSError, OverflowError):
                pass

            media_info = None
            if member.size > opts.max_hash_size_bytes:
                digest, hash_reason = None, f"skipped: member size {member.size} bytes exceeds max-hash-size"
            else:
                fh = tf.extractfile(member)
                if fh is None:
                    digest, hash_reason = None, "could not open tar member"
                elif opts.extract_archive_media_metadata and _media_kind_for_name(member.name):
                    # Buffer once (bounded by the same size cap as hashing) and reuse
                    # the bytes for both, instead of reading the member twice.
                    data = fh.read()
                    digest, hash_reason, _ = hashing.hash_stream(io.BytesIO(data), opts.hash_algo, opts.max_hash_size_bytes)
                    media_info = _extract_media_from_bytes(data, member.name, scratch_dir)
                else:
                    digest, hash_reason, _ = hashing.hash_stream(fh, opts.hash_algo, opts.max_hash_size_bytes)

            entries.append({
                "name": member.name,
                "size": member.size,
                "mtime": mtime,
                "crc": None,  # tar has no built-in per-member checksum
                "is_dir": False,
                "precomputed_hash": digest,
                "precomputed_hash_reason": hash_reason,
                "precomputed_media": media_info,
            })
    return entries


def list_entries(path, fmt, opts, scratch_dir):
    if fmt == "zip":
        return _list_zip_entries(path)
    if fmt == "tar":
        return _list_tar_entries(path, opts, scratch_dir)
    if fmt in ("7z", "rar"):
        return _list_7z_entries(path)
    raise ValueError(f"unsupported archive format: {fmt}")


def hash_member(path, fmt, entry, opts):
    """Return (hash_hex, algo, skip_reason) for one archive member."""
    if entry.get("crc"):
        return entry["crc"], "crc32", None

    if "precomputed_hash" in entry:
        # tar: already hashed during _list_tar_entries()'s single sequential
        # pass -- reopening the (possibly compressed, non-seekable) tar here
        # per member would be the O(n^2) bug this field exists to avoid.
        return entry["precomputed_hash"], opts.hash_algo, entry["precomputed_hash_reason"]

    if entry["size"] > opts.max_hash_size_bytes:
        return None, opts.hash_algo, f"skipped: member size {entry['size']} bytes exceeds max-hash-size"

    try:
        if fmt == "zip":
            with zipfile.ZipFile(path) as zf, zf.open(entry["name"]) as fh:
                digest, reason, _ = hashing.hash_stream(fh, opts.hash_algo, opts.max_hash_size_bytes)
                return digest, opts.hash_algo, reason
        if fmt in ("7z", "rar"):
            seven_zip = find_seven_zip()
            if not seven_zip:
                return None, "crc32", "7-Zip not available; could not compute checksum"
            proc = subprocess.Popen(
                [seven_zip, "x", "-so", "-y", "--", path, entry["name"]],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,  # a password-protected member must fail fast, never prompt-and-hang
            )
            try:
                digest, reason, _ = hashing.hash_stream(proc.stdout, opts.hash_algo, opts.max_hash_size_bytes)
            finally:
                proc.stdout.close()
                proc.wait(timeout=SEVEN_ZIP_TIMEOUT_SECONDS)
            return digest, opts.hash_algo, reason
    except Exception as exc:
        return None, opts.hash_algo, f"could not hash member: {exc}"

    return None, opts.hash_algo, "unsupported format"


def _extract_member_to_temp(path, fmt, entry, scratch_dir):
    """Extract one archive member to a temp file under scratch_dir.

    Returns (extracted_file_path, dest_dir) so the caller can clean up
    dest_dir itself -- NOT os.path.dirname(extracted_file_path), which is
    only the immediate parent and, whenever the member's internal name has
    a subdirectory component (very common: "docs/manual.zip"), leaves the
    actual mkdtemp() directory behind forever. On a long scan with many
    nested archives that leaks one directory per nested archive into the
    scratch folder for the rest of the run.
    """
    dest_dir = tempfile.mkdtemp(dir=scratch_dir)
    try:
        if fmt == "zip":
            with zipfile.ZipFile(path) as zf:
                extracted = zf.extract(entry["name"], dest_dir)
            return extracted, dest_dir
        if fmt == "tar":
            with tarfile.open(path) as tf:
                member = tf.getmember(entry["name"])
                tf.extract(member, dest_dir)
            return os.path.join(dest_dir, entry["name"]), dest_dir
        if fmt in ("7z", "rar"):
            seven_zip = find_seven_zip()
            if not seven_zip:
                return None, dest_dir
            _run_7z(["x", "-y", f"-o{dest_dir}", "--", path, entry["name"]])
            expected = os.path.join(dest_dir, *entry["name"].split("/"))
            if os.path.isfile(expected):
                return expected, dest_dir
            # Fallback: locate by basename if 7z normalized the path differently.
            base = os.path.basename(entry["name"])
            for root, _dirs, files in os.walk(dest_dir):
                if base in files:
                    return os.path.join(root, base), dest_dir
            return None, dest_dir
    except Exception:
        return None, dest_dir
    return None, dest_dir


def process_archive_file(fs_path, container_chain, depth, opts, emit_record, errors, scratch_dir, stats=None):
    """Recursively enumerate an archive's contents and emit one record per member.

    container_chain: list of display-name segments from the top-level file down
                      to (and including) this archive.
    emit_record: callable taking a single record dict.
    errors: list to append {"path": ..., "error": ...} dicts to.
    stats: optional dict to increment "archive_entries_skipped_ignore_list" on
           (matches scanner.py's ignore-dir-name filtering, applied to archive
           members too -- e.g. a __pycache__ folder bundled inside a zip).
    """
    fmt = detect_archive_format(fs_path if depth == 1 else container_chain[-1])
    display_path = " // ".join(container_chain)

    if depth > opts.max_archive_depth:
        errors.append({
            "path": display_path,
            "error": f"max archive nesting depth ({opts.max_archive_depth}) reached; not expanded further",
        })
        return

    logger.info("Expanding archive (depth %d): %s", depth, display_path)
    list_start = time.monotonic()
    try:
        entries = list_entries(fs_path, fmt, opts, scratch_dir)
    except Exception as exc:
        errors.append({"path": display_path, "error": f"failed to open archive: {exc}"})
        return
    list_elapsed = time.monotonic() - list_start
    if list_elapsed > opts.slow_threshold_seconds:
        logger.warning("Slow archive listing (%.1fs, %d entries): %s", list_elapsed, len(entries), display_path)

    for entry in entries:
        if entry["is_dir"]:
            continue

        if _is_ignored_entry(entry["name"], opts.ignore_dir_names):
            if stats is not None:
                stats["archive_entries_skipped_ignore_list"] += 1
            continue

        entry_chain = container_chain + [entry["name"]]
        entry_display = " // ".join(entry_chain)

        # One bad member (odd encoding, corrupt local header, ...) must not
        # abort the rest of this archive, or the scan it's part of.
        try:
            logger.debug("Archive member: %s", entry_display)
            member_start = time.monotonic()
            digest, algo, skip_reason = hash_member(fs_path, fmt, entry, opts)
            member_elapsed = time.monotonic() - member_start
            if member_elapsed > opts.slow_threshold_seconds:
                logger.warning("Slow archive member hash (%.1fs): %s", member_elapsed, entry_display)

            record = {
                "type": "archive_entry",
                "path": entry_display,
                "filename": entry["name"].rsplit("/", 1)[-1],
                "container_path": container_chain[0],
                "internal_path": " // ".join(entry_chain[1:]),
                "nesting_depth": depth,
                "created": None,
                "modified": entry["mtime"],
                "size_bytes": entry["size"],
                "hash_algo": algo,
                "hash": digest,
                "hash_skip_reason": skip_reason,
            }

            media_info = None
            if fmt == "tar":
                # Already computed during _list_tar_entries()'s single sequential
                # pass -- see _extract_media_from_bytes for why.
                media_info = entry.get("precomputed_media")
            elif opts.extract_archive_media_metadata and entry["size"] <= opts.max_hash_size_bytes:
                media_start = time.monotonic()
                media_info = _extract_media_for_entry(fs_path, fmt, entry, scratch_dir)
                media_elapsed = time.monotonic() - media_start
                if media_elapsed > opts.slow_threshold_seconds:
                    logger.warning("Slow archive media extraction (%.1fs): %s", media_elapsed, entry_display)
            if media_info:
                record.update(media_info)
                if stats is not None:
                    stats["archive_media_extracted"] += 1

            emit_record(record)

            nested_fmt = detect_archive_format(entry["name"])
            if not nested_fmt:
                continue
            if entry["size"] > opts.max_nested_extract_bytes:
                errors.append({
                    "path": entry_display,
                    "error": "nested archive exceeds max-nested-extract-size; not expanded",
                })
                continue

            extracted_path, dest_dir = _extract_member_to_temp(fs_path, fmt, entry, scratch_dir)
            if not extracted_path:
                errors.append({"path": entry_display, "error": "could not extract nested archive for expansion"})
                shutil.rmtree(dest_dir, ignore_errors=True)
                continue
            try:
                process_archive_file(extracted_path, entry_chain, depth + 1, opts, emit_record, errors, scratch_dir, stats)
            finally:
                shutil.rmtree(dest_dir, ignore_errors=True)
        except Exception as exc:
            errors.append({"path": entry_display, "error": f"unexpected error processing archive member: {exc}"})
