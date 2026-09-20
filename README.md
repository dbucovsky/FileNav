# FileNav

A Python filesystem inventory scanner. Walks drives/folders/files and writes a
single JSON file describing everything it finds: path, filename, created/modified
timestamps, size, and a hash for validation and duplicate detection.

Archive files (`.zip`, `.tar`/`.tar.gz`/`.tar.bz2`/`.tar.xz`, `.7z`, `.rar`) are opened
and every member recorded too, including archives nested inside archives (a zip
inside a zip inside a zip, and so on). Images and videos get extra metadata when
available: dimensions, EXIF capture date, camera make/model, GPS location, and
(for video) duration.

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

A bare filename (like `output.json` above) is written next to `filenav.py`. Pass a
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
| `--no-media` | off | Skip image/video metadata extraction (faster) |
| `--ignore-dirs NAME [NAME ...]` | (none) | Extra directory names to skip, on top of the default list |
| `--no-default-ignores` | off | Disable the built-in default ignore list entirely |
| `--no-timestamp-prefix` | off | Write the exact filename given, without the `yyyy-mm-dd-hh-mm-ss_` prefix |
| `--progress-every` | 5000 | Print progress every N records (0 to disable) |

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

## Tests

```
.venv\Scripts\python -m pytest tests/ -v
```
