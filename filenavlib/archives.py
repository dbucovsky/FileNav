"""Archive enumeration: zip / tar via the standard library, .7z / .rar via the
7-Zip CLI. Handles archives nested inside archives (zip-in-zip-in-zip, a zip
inside a 7z, etc.) up to a configurable depth, by extracting nested members
to a scratch temp directory and recursing.
"""

import os
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from datetime import datetime

from . import hashing
from .config import ARCHIVE_COMPOUND_EXTS, ARCHIVE_SINGLE_EXTS, SEVEN_ZIP_FALLBACK_PATHS

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


def _run_7z(args):
    seven_zip = find_seven_zip()
    if not seven_zip:
        raise RuntimeError("7-Zip executable not found")
    proc = subprocess.run(
        [seven_zip] + args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
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


def _list_tar_entries(path):
    entries = []
    with tarfile.open(path) as tf:
        for member in tf.getmembers():
            if not member.isfile():
                if member.isdir():
                    entries.append({"name": member.name, "size": 0, "mtime": None, "crc": None, "is_dir": True})
                continue
            mtime = None
            try:
                mtime = datetime.fromtimestamp(member.mtime).isoformat()
            except (ValueError, OSError, OverflowError):
                pass
            entries.append({
                "name": member.name,
                "size": member.size,
                "mtime": mtime,
                "crc": None,  # tar has no built-in per-member checksum
                "is_dir": False,
            })
    return entries


def list_entries(path, fmt):
    if fmt == "zip":
        return _list_zip_entries(path)
    if fmt == "tar":
        return _list_tar_entries(path)
    if fmt in ("7z", "rar"):
        return _list_7z_entries(path)
    raise ValueError(f"unsupported archive format: {fmt}")


def hash_member(path, fmt, entry, opts):
    """Return (hash_hex, algo, skip_reason) for one archive member."""
    if entry.get("crc"):
        return entry["crc"], "crc32", None

    if entry["size"] > opts.max_hash_size_bytes:
        return None, opts.hash_algo, f"skipped: member size {entry['size']} bytes exceeds max-hash-size"

    try:
        if fmt == "zip":
            with zipfile.ZipFile(path) as zf, zf.open(entry["name"]) as fh:
                digest, reason, _ = hashing.hash_stream(fh, opts.hash_algo, opts.max_hash_size_bytes)
                return digest, opts.hash_algo, reason
        if fmt == "tar":
            with tarfile.open(path) as tf:
                fh = tf.extractfile(entry["name"])
                if fh is None:
                    return None, opts.hash_algo, "could not open tar member"
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
            )
            try:
                digest, reason, _ = hashing.hash_stream(proc.stdout, opts.hash_algo, opts.max_hash_size_bytes)
            finally:
                proc.stdout.close()
                proc.wait(timeout=60)
            return digest, opts.hash_algo, reason
    except Exception as exc:
        return None, opts.hash_algo, f"could not hash member: {exc}"

    return None, opts.hash_algo, "unsupported format"


def _extract_member_to_temp(path, fmt, entry, scratch_dir):
    """Extract one archive member to a temp file under scratch_dir, return its path or None."""
    dest_dir = tempfile.mkdtemp(dir=scratch_dir)
    try:
        if fmt == "zip":
            with zipfile.ZipFile(path) as zf:
                extracted = zf.extract(entry["name"], dest_dir)
            return extracted
        if fmt == "tar":
            with tarfile.open(path) as tf:
                member = tf.getmember(entry["name"])
                tf.extract(member, dest_dir)
            return os.path.join(dest_dir, entry["name"])
        if fmt in ("7z", "rar"):
            seven_zip = find_seven_zip()
            if not seven_zip:
                return None
            _run_7z(["x", "-y", f"-o{dest_dir}", "--", path, entry["name"]])
            expected = os.path.join(dest_dir, *entry["name"].split("/"))
            if os.path.isfile(expected):
                return expected
            # Fallback: locate by basename if 7z normalized the path differently.
            base = os.path.basename(entry["name"])
            for root, _dirs, files in os.walk(dest_dir):
                if base in files:
                    return os.path.join(root, base)
            return None
    except Exception:
        return None
    return None


def process_archive_file(fs_path, container_chain, depth, opts, emit_record, errors, scratch_dir):
    """Recursively enumerate an archive's contents and emit one record per member.

    container_chain: list of display-name segments from the top-level file down
                      to (and including) this archive.
    emit_record: callable taking a single record dict.
    errors: list to append {"path": ..., "error": ...} dicts to.
    """
    fmt = detect_archive_format(fs_path if depth == 1 else container_chain[-1])
    display_path = " // ".join(container_chain)

    if depth > opts.max_archive_depth:
        errors.append({
            "path": display_path,
            "error": f"max archive nesting depth ({opts.max_archive_depth}) reached; not expanded further",
        })
        return

    try:
        entries = list_entries(fs_path, fmt)
    except Exception as exc:
        errors.append({"path": display_path, "error": f"failed to open archive: {exc}"})
        return

    for entry in entries:
        if entry["is_dir"]:
            continue

        entry_chain = container_chain + [entry["name"]]
        entry_display = " // ".join(entry_chain)
        digest, algo, skip_reason = hash_member(fs_path, fmt, entry, opts)

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

        extracted_path = _extract_member_to_temp(fs_path, fmt, entry, scratch_dir)
        if not extracted_path:
            errors.append({"path": entry_display, "error": "could not extract nested archive for expansion"})
            continue
        try:
            process_archive_file(extracted_path, entry_chain, depth + 1, opts, emit_record, errors, scratch_dir)
        finally:
            shutil.rmtree(os.path.dirname(extracted_path), ignore_errors=True)
