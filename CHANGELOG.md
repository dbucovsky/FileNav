# Changelog

## V0.2.0 — 2026-09-20 02:00
### Changes
- **Add a default ignore list for noise directories, plus `--ignore-dirs`/`--no-default-ignores`**
  - Beyond the existing `.filenav-skip` marker, folders are now also skipped by *name* (case-insensitive, matched anywhere in the tree) against a built-in default list: version-control internals (`.git`, `.svn`, `.hg`), dev/build caches (`node_modules`, `__pycache__`, `.venv`, `venv`, `.tox`, `.mypy_cache`, `.pytest_cache`, `dist`, `build`), Windows system folders (`System Volume Information`, `$RECYCLE.BIN`), and editor config (`.vscode`, `.idea`) — all machine-generated noise rather than real user data, and normally either regenerable, permission-denied, or (for `.git`) thousands of tiny internal blob files that would otherwise dominate a scan.
  - `--ignore-dirs NAME [NAME ...]` adds more names on top of the default list; `--no-default-ignores` turns the default list off entirely (only `.filenav-skip` and any explicit `--ignore-dirs` still apply) for cases like deliberately wanting `.git` history included in a duplicate-file audit.
  - The output JSON's `options` now records `default_ignores_applied` and the exact `ignore_dir_names` set used for that run; `summary` now reports `directories_skipped_via_marker` and `directories_skipped_via_ignore_list` separately (previously one combined `directories_skipped_via_marker` count).
  - Matching subdirectories are pruned before `os.walk` descends into them (not just skipped once reached), avoiding a wasted directory listing for each one.

## V0.1.0 — 2026-09-20 01:15
### Changes
- **Initial implementation of the filesystem inventory scanner**
  - `filenav.py` (CLI entry point) + `filenavlib/` (scanner, hashing, archive handling, media metadata, streaming JSON writer).
  - Walks one or more roots (default: `C:\`; `--root all` enumerates every ready Windows drive) recording, per file: full path, filename, created/modified timestamps, size in bytes, and a `sha256` hash (configurable via `--hash-algo`) for validation and duplicate matching. Files above `--max-hash-size-mb` (default 2048) are recorded without a hash rather than reading the whole thing.
  - Folders containing a file named `.filenav-skip` are excluded entirely, along with every file and subfolder beneath them.
  - **Archive expansion**: `.zip` and `.tar`/`.tar.gz`/`.tar.bz2`/`.tar.xz` are read via the standard library; `.7z` and `.rar` via the system's 7-Zip CLI (auto-detected on PATH or at `C:\Program Files\7-Zip\7z.exe`, gracefully degraded to "file only, contents not listed" if absent). An archive nested inside another archive (zip-in-zip-in-zip, a zip inside a 7z, etc.) is extracted to a scratch temp folder and recursed into, up to `--max-archive-depth` (default 10) levels; each member's recorded `path` shows the full container chain (`outer.zip // inner.zip // file.txt`). Zip members reuse the format's own built-in CRC32 (free — no need to decompress); 7z/rar members use the CRC 7-Zip's own listing reports; tar members (no built-in checksum) are hashed by streaming their content, same size cutoff as regular files.
  - **Image metadata** (Pillow): dimensions, EXIF capture date, camera make/model, and GPS coordinates (converted to decimal degrees) when present.
  - **Video metadata** (hachoir, pure Python — no ffmpeg/external binary needed): duration, dimensions, recording date, and device/producer info when the container exposes it.
  - Output is streamed to the JSON file record-by-record (not built up in memory first), so a full-drive scan with millions of files doesn't blow up RAM.
  - The output JSON file is passed as a parameter and, per the spec, always gets one final self-referencing entry in its own `files` array (`"self_reference": true`) — its `size_bytes` is captured immediately before the closing JSON is appended (documented as a close approximation, since a file can't know its own exact final size while still being written), and its content is never parsed/analyzed.
  - Output path resolution: a bare filename (`results.json`) is written next to `filenav.py`; a filename with any path component (relative or absolute) is used as given.
  - Full pytest suite (`tests/`) covering the skip-marker rule, hash correctness and the size cutoff, recursive nested-zip expansion (with CRC verification), output-path resolution, and an end-to-end CLI run producing valid JSON with the expected self-record.

### Planned (not yet implemented)
- None beyond this initial build.

### Known limitations (not addressed this pass)
- `.rar` (and `.7z`) contents require 7-Zip to be installed on the machine running the scan; without it those files are still recorded, just without their internal member list. Password-protected/encrypted archives of any format are recorded as a plain file with an error noted, not opened.
- Image/video metadata extraction only applies to files sitting directly on disk — an image or video found *inside* an archive is recorded (path/size/hash) but its EXIF/duration/etc. is not extracted, to avoid extracting every archived file during a whole-drive scan.
- `--root all` drive auto-enumeration is Windows-only (uses `GetLogicalDrives`/`GetDriveTypeW`); on other platforms it falls back to scanning `/`.
- "Created" timestamp uses `os.stat().st_ctime`, which is genuinely the file creation time on Windows/NTFS (this project's target environment) but means "last metadata change" on Linux/macOS.
