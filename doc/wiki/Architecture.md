# Architecture

```
filenav.py            CLI entry point: argparse, output-path resolution,
                       drives the top-level scan loop, writes the self-record.
filenavlib/
  config.py            Constants and defaults (skip marker name, extension
                        tables, size cutoffs, 7-Zip fallback paths).
  drives.py             Windows drive enumeration for --root all.
  scanner.py            os.walk driver: applies the .filenav-skip rule,
                        builds one record per real file.
  hashing.py             Streaming hash of a file on disk or an open stream,
                        with a size cutoff.
  archives.py             zip/tar via the standard library; .7z/.rar via the
                        system 7-Zip CLI (`7z l -slt` for listing+CRC, `7z x`
                        for extracting a member). Recurses into archives
                        nested inside archives via a scratch temp directory.
  media.py                 Best-effort EXIF (Pillow) / video metadata
                        (hachoir) extraction — every function degrades to a
                        partial dict or None rather than raising.
  writer.py                 Streams the "files" JSON array to disk one record
                        at a time instead of buffering the whole scan in
                        memory.
tests/                   pytest suite exercising each module in isolation
                        plus one end-to-end CLI run.
```

## Why streaming output

A full-drive scan can produce millions of records. `JsonScanWriter` writes each
record to the open file handle as soon as it's produced, so memory use stays
roughly constant regardless of how many files are scanned. The trade-off: the
file is invalid/incomplete JSON until `close()` runs at the end of the scan (or
the process is interrupted) — there's no resume/partial-read support.

## Why archive recursion always goes through a temp file

Zip and tar can both be opened directly from an in-memory buffer, but the
7-Zip CLI (needed for `.7z`/`.rar`) only operates on real files on disk. Rather
than have two different code paths (one for formats that support streaming,
one for formats that don't), every nested archive member — regardless of its
own format or its container's format — is extracted to a scratch temp
directory and then handled by the same top-level `process_archive_file()`
function, recursively. Simpler to reason about, at the cost of some disk I/O
for deeply nested archives.

## Hash algorithm per record type

- Regular files and tar archive members: `sha256` (or whatever `--hash-algo`
  is), computed by streaming the file.
- Zip archive members: the zip format's own built-in CRC32 (`ZipInfo.CRC`) —
  free, no decompression needed.
- 7z/rar archive members: the CRC 7-Zip's own `-slt` listing reports, when
  present; extracted and hashed on demand only if it isn't.

Each record's `hash_algo` field says which one was used, so nothing is silently
inconsistent across the output.

Related: [[Home]], [[Usage]].
