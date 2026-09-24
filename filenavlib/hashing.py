"""Streaming file hashing with a size cutoff for very large files."""

import hashlib

from .config import HASH_CHUNK_SIZE


def hash_file(path, algo="sha256", max_size_bytes=None, size_hint=None):
    """Hash a file on disk, streaming it in chunks.

    Returns (hex_digest_or_None, skip_reason_or_None).
    """
    if max_size_bytes is not None:
        size = size_hint
        if size is None:
            try:
                import os
                size = os.path.getsize(path)
            except OSError as exc:
                return None, f"could not stat file: {exc}"
        if size > max_size_bytes:
            return None, f"skipped: file size {size} bytes exceeds max-hash-size ({max_size_bytes} bytes)"

    try:
        hasher = hashlib.new(algo)
    except ValueError:
        return None, f"unknown hash algorithm: {algo}"

    try:
        with open(path, "rb") as f:
            while True:
                chunk = f.read(HASH_CHUNK_SIZE)
                if not chunk:
                    break
                hasher.update(chunk)
    except OSError as exc:
        return None, f"could not read file: {exc}"

    return hasher.hexdigest(), None


def hash_stream(fileobj, algo="sha256", max_size_bytes=None):
    """Hash an already-open binary stream (used for archive members).

    Returns (hex_digest_or_None, skip_reason_or_None, bytes_read).
    """
    try:
        hasher = hashlib.new(algo)
    except ValueError:
        return None, f"unknown hash algorithm: {algo}", 0

    total = 0
    try:
        while True:
            chunk = fileobj.read(HASH_CHUNK_SIZE)
            if not chunk:
                break
            total += len(chunk)
            if max_size_bytes is not None and total > max_size_bytes:
                return None, f"skipped: file exceeds max-hash-size ({max_size_bytes} bytes)", total
            hasher.update(chunk)
    except OSError as exc:
        return None, f"could not read stream: {exc}", total

    return hasher.hexdigest(), None, total
