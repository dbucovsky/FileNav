# Usage

## Command line

```
python filenav.py OUTPUT [--root PATH [PATH ...]] [options]
```

`OUTPUT` is the JSON file to write. A bare filename (`results.json`) is written
next to `filenav.py`; anything with a path in it (`out\results.json`,
`C:\scans\results.json`) is used exactly as given.

### `--root`

- Default: `C:\` (one drive)
- One or more paths: `--root C:\ D:\Photos`
- `--root all` — every ready Windows drive (skips unready removable/optical
  drives automatically)

### Other flags

| Flag | Default | Purpose |
|---|---|---|
| `--hash-algo` | `sha256` | Any name `hashlib.new()` accepts |
| `--max-hash-size-mb` | 2048 | Files bigger than this: no hash, just size/dates |
| `--max-nested-extract-mb` | 500 | Nested archives bigger than this are left unexpanded |
| `--max-archive-depth` | 10 | How many archive-in-archive levels to follow |
| `--no-media` | off | Skip image/video metadata extraction |
| `--progress-every` | 5000 | Progress line every N records (`0` = silent) |

## The `.filenav-skip` rule

Any folder containing a file literally named `.filenav-skip` (content doesn't
matter, even empty) is excluded from the scan — that folder, its files, and
every subfolder beneath it.

## Output JSON shape

```jsonc
{
  "scan_started": "2026-09-20T01:00:00",
  "host": "...",
  "platform": "...",
  "roots": ["C:\\"],
  "options": { "hash_algo": "sha256", ... },
  "files": [
    {
      "type": "file",
      "path": "C:\\Users\\me\\photo.jpg",
      "filename": "photo.jpg",
      "created": "2025-01-02T10:00:00",
      "modified": "2025-01-02T10:00:00",
      "size_bytes": 4200000,
      "hash_algo": "sha256",
      "hash": "…",
      "hash_skip_reason": null,
      "image": { "width": 4032, "height": 3024, "date_taken": "...", "device_make": "...", "gps_latitude": 40.7, "gps_longitude": -74.0 }
    },
    {
      "type": "file",
      "path": "C:\\Users\\me\\archive.zip",
      "filename": "archive.zip",
      "...": "...",
      "archive_format": "zip"
    },
    {
      "type": "archive_entry",
      "path": "C:\\Users\\me\\archive.zip // inner.zip // notes.txt",
      "filename": "notes.txt",
      "container_path": "C:\\Users\\me\\archive.zip",
      "internal_path": "inner.zip // notes.txt",
      "nesting_depth": 2,
      "size_bytes": 512,
      "hash_algo": "crc32",
      "hash": "…"
    }
  ],
  "errors": [ { "path": "...", "error": "..." } ],
  "summary": { "files_recorded": 1234, "archives_expanded": 5, "elapsed_seconds": 42.1, "...": "..." }
}
```

The archive file itself (`archive.zip`) is always recorded as an ordinary
`"file"` entry too — `"archive_entry"` records are its contents, in addition to
that.

The output file always ends with one `"self_reference": true` entry describing
itself (path/filename/approximate size) — it is recorded like any other file
would be, never parsed as meaningful content.

Related: [[Getting-Started]], [[Architecture]].
