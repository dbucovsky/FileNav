# FileNav

A Python filesystem inventory scanner. Walks drives/folders/files and writes a
single JSON file describing everything it finds: path, filename, created/modified
timestamps, size, and a hash for validation and duplicate detection.

Archive files (`.zip`, `.tar`/`.tar.gz`/`.tar.bz2`/`.tar.xz`, `.7z`, `.rar`) are opened
and every member recorded too, including archives nested inside archives (a zip
inside a zip inside a zip, and so on) — or pass `--no-expand-formats` to record
specific archive types as plain files (path/size/hash) without opening them.
Images and videos get extra metadata when available: dimensions, EXIF capture
date, camera make/model, GPS location, and (for video) duration — on disk by
default; pass `--archive-media` to also do this for images/videos found
*inside* archives (off by default, since it means decompressing every one).

Full documentation: [project wiki](https://github.com/dbucovsky/FileNav/wiki)
(same content as `doc/wiki/` in this repo).

## Requirements

- Python 3.9+
- [7-Zip](https://www.7-zip.org/) installed, for `.7z` and `.rar` archive contents
  (auto-detected on PATH or at `C:\Program Files\7-Zip\7z.exe`). Without it, `.7z`/`.rar`
  files are still recorded as plain files, just without their internal contents listed.
- Python packages in `requirements.txt` (Pillow for image EXIF, hachoir for video
  metadata, pytest for the test suite)

## Setup

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

## Usage

```
.venv\Scripts\python filenav.py output.json
```

The output filename is optional — omit it (or leave it out of `--config`'s
file) and it defaults to `output.json`:

```
.venv\Scripts\python filenav.py
```

A bare filename (whether given or defaulted) is written next to `filenav.py`. Pass a
path instead (relative or absolute) to control where it lands:

```
.venv\Scripts\python filenav.py C:\scans\inventory.json
```

By default, the output and error-log filenames are prefixed with the local time
the scan started (`yyyy-mm-dd-hh-mm-ss_`, 24-hour), so repeated runs never
overwrite each other and sort chronologically — `output.json` becomes something
like `2026-09-20-14-05-30_output.json`. Pass `--no-timestamp-prefix` to write
the exact filename given instead.

By default the scan starts at `C:\`. Override with `--root`:

```
.venv\Scripts\python filenav.py output.json --root D:\Photos
.venv\Scripts\python filenav.py output.json --root C:\ D:\
.venv\Scripts\python filenav.py output.json --root all      REM every ready drive
```

Other options (see `filenav.py --help`):

| Flag | Default | Purpose |
|---|---|---|
| `--hash-algo` | `sha256` | Any `hashlib` algorithm name |
| `--max-hash-size-mb` | 2048 | Files larger than this are recorded without a hash |
| `--max-nested-extract-mb` | 500 | Nested archives larger than this are not expanded |
| `--max-archive-depth` | 10 | Archive-in-archive nesting limit |
| `--no-expand-formats {zip,tar,7z,rar} [...]` | (none) | Record these archive types as a normal file (path/size/hash) without opening them |
| `--no-media` | off | Skip image/video metadata extraction (faster) |
| `--archive-media` | off | Also extract EXIF/video metadata for images/videos found inside archives |
| `--ignore-dirs NAME [NAME ...]` | (none) | Extra directory names to skip, on top of the default list |
| `--exclude-paths PATTERN [PATTERN ...]` | (none) | Glob pattern(s) matched against a file/folder's full path; a match skips it |
| `--no-default-ignores` | off | Disable the built-in default ignore list entirely |
| `--no-timestamp-prefix` | off | Write the exact filename given, without the `yyyy-mm-dd-hh-mm-ss_` prefix |
| `--progress-every` | 5000 | Print progress every N records (0 to disable) |
| `-v`, `--verbose` | off | Log one line per file (not just per directory) to `<output>.log` |
| `--slow-threshold-seconds` | 10 | Log a warning when a single hash/archive operation takes longer than this |
| `--log-interval` | 60 | Seconds between progress heartbeats in `<output>.log` (0 to disable) |
| `--config PATH` | (none) | Read any of these options (plus the output filename) from a JSON file |

## Reading options from a file

Any option above, plus the output filename, can be set in a JSON file instead
of on the command line:

```
.venv\Scripts\python filenav.py --config myscan.json
```

```jsonc
// myscan.json
{
  "output": "C:\\scans\\inventory.json",
  "root": ["C:\\", "D:\\"],
  "ignore_dirs": ["Backups", "OldProjects"],
  "exclude_paths": ["C:\\Users\\*\\Downloads\\*"],
  "verbose": true,
  "slow_threshold_seconds": 5
}
```

Keys match the CLI flag names (with dashes as underscores, e.g. `--no-media`
→ `"no_media": true`). Precedence is: **command line wins over the config
file, which wins over the built-in default** — so `filenav.py --config
myscan.json --root E:\` scans `E:\` even if `myscan.json` says otherwise,
and everything else still comes from the file. `output` follows the same
rule, falling back to `output.json` if neither sets it. An unknown key, a
wrong type, or a missing/invalid file is a clear error before any scanning
starts.

## Skipping folders

Drop a file named `.filenav-skip` (any content, even empty) into a folder to
exclude that folder and everything under it from the scan.

A built-in default list of noise directories is also skipped automatically
(matched case-insensitively by folder name, anywhere in the tree): `.git`,
`.svn`, `.hg`, `node_modules`, `__pycache__`, `.venv`, `venv`, `.tox`,
`.mypy_cache`, `.pytest_cache`, `dist`, `build`, `System Volume Information`,
`$RECYCLE.BIN`, `.vscode`, `.idea`. Add more with `--ignore-dirs`, or turn the
whole default list off with `--no-default-ignores` (e.g. if you deliberately
want `.git` history included in a duplicate-file audit).

This list also applies *inside* archives — a `__pycache__` or `.git` folder
bundled inside a zip (a Python wheel, an sdist, a downloaded repo export)
is skipped there too, not just when it's a real folder on disk.

### Excluding by path/pattern, not just by name

`--ignore-dirs` matches a folder's *name* only (e.g. `Backups` excludes every
folder named `Backups`, anywhere). To target one specific location instead,
or match by a pattern rather than an exact name, use `--exclude-paths` —
glob patterns (`*`, `?`, `[seq]`, via Python's `fnmatch`) matched against the
**full path** of every file and folder:

```
.venv\Scripts\python filenav.py output.json --exclude-paths "C:\Users\*\Downloads\*" "*.tmp"
```

A folder match excludes its whole subtree, just like `--ignore-dirs`; a file
match excludes only that file. `*` matches any characters, including path
separators (plain glob semantics, not `.gitignore`-style `**`), so `*\Downloads\*`
matches `Downloads` at any depth in one pattern. Either `\` or `/` works in a
pattern on Windows. This only applies to real files/folders on disk — unlike
`--ignore-dirs`, it does not reach inside archives.

## Not opening certain archive types

`--exclude-paths`/`--ignore-dirs` leave the archive file out of the output
entirely. To keep the archive file itself in the output (path, size, hash,
dates — same as any file) but skip opening and enumerating its contents, use
`--no-expand-formats` instead:

```
.venv\Scripts\python filenav.py output.json --no-expand-formats tar
```

Choices are `zip`, `tar`, `7z`, `rar` — `tar` covers every tar variant
(`.tar`, `.tgz`, `.tbz2`, `.txz`, `.tar.gz`, `.tar.bz2`, `.tar.xz`), since
they're all the same underlying format. A skipped archive's record gets
`"archive_expansion_skipped": true`; `summary.archives_not_expanded` counts
how many.

## EXIF/video metadata for images and videos inside archives

Off by default — pass `--archive-media` to turn it on:

```
.venv\Scripts\python filenav.py output.json --archive-media
```

Every image/video member found while expanding a `.zip`/`.tar*`/`.7z`/`.rar`
then gets the same `"image"`/`"video"` metadata (dimensions, EXIF capture
date, GPS, camera make/model, video duration) that a loose file on disk
already gets — which means decompressing that member first, so this adds
real cost on a scan with a lot of archived photos/videos. Subject to the
same `--max-hash-size-mb` cap already used to skip hashing huge files.
`summary.archive_media_extracted` counts how many were processed this way.

## Output format

The output JSON has a `files` array (one entry per real file, plus one entry per
archive member), an `errors` array (a capped sample of what couldn't be read/opened,
with why — see below), and a `summary` with counts and timings. Archive members
carry a `path` that shows the full container chain, e.g.
`C:\a.zip // inner\b.zip // photo.jpg`.

The output JSON file itself always gets one final entry in `files` (marked
`"self_reference": true`) — it can't know its own exact final size while still
writing itself, so that entry's `size_bytes` is captured just before the last
few bytes are appended and is documented as a close approximation, not analyzed
further.

## Errors

Every file or archive member that couldn't be read/hashed/opened is written, as
it happens, to a sidecar log next to the output — `output.json` gets
`output.errors.log`, one JSON object per line (`{"path": ..., "error": ...}`), so
it's greppable on its own without touching the (possibly huge) main JSON, and it's
never lost even if the scan later aborts hard. This is also where a single bad
file's error ends up if it doesn't stop the scan (see below).

The main JSON's own `errors` array is a capped sample (first 1000) for quick
inspection; `summary.errors` is the true total count and
`summary.errors_sample_truncated` says whether the sample left anything out — the
sidecar log always has the complete list either way.

## Resilience

A single unreadable file, corrupt archive, or oddly-encoded archive-member name
is logged as an error and skipped — it does not stop the rest of the scan. Even
an unexpected crash (or Ctrl+C) mid-scan still finishes the output JSON as valid,
loadable JSON with everything scanned up to that point (`summary.aborted` /
`summary.abort_reason` record that it happened), rather than leaving a corrupt,
truncated file and losing all the work.

A password-protected `.7z`/`.rar` is recorded as a normal file (it just can't
be opened, same as any unreadable archive) rather than hanging the scan
waiting for a password that will never be typed.

## Diagnosing a slow or stuck scan

Every run writes a diagnostic log to `<output>.log`, separate from the errors
log — this is for *what the scan was doing*, not what it failed on. It logs:

- One line per directory entered (not per file, unless `--verbose`/`-v`) — if
  a scan hangs, the log's last line is exactly where it was.
- A periodic heartbeat (every `--log-interval` seconds, default 60) with the
  running counts, the processing rate since the last heartbeat, and the
  current entry count of the scratch temp folder used to expand nested
  archives — a rising number there across heartbeats (instead of staying near
  zero) points at a resource leak rather than a slow file.
- A warning for any single hash, archive listing, or archive-member
  extraction that takes longer than `--slow-threshold-seconds` (default 10),
  naming the exact file responsible.

## Tests

```
.venv\Scripts\python -m pytest tests/ -v
```
