# Usage

## Command line

```
python filenav.py OUTPUT [--root PATH [PATH ...]] [options]
python filenav.py --config myscan.json
```

`OUTPUT` is the JSON file to write. A bare filename (`results.json`) is written
next to `filenav.py`; anything with a path in it (`out\results.json`,
`C:\scans\results.json`) is used exactly as given, in either case as a
*starting point* for the name — see timestamp prefixing next. `OUTPUT` is
optional: if it's not given on the command line and `--config`'s file doesn't
set `"output"` either, it defaults to `output.json` (still next to `filenav.py`,
still timestamp-prefixed as usual) — see below.

### Timestamp prefix

By default, both the output filename and its error log get a
`yyyy-mm-dd-hh-mm-ss_` prefix (local time, 24-hour, the moment the scan
started) — `results.json` becomes `2026-09-20-14-05-30_results.json`, and its
error log is `2026-09-20-14-05-30_results.errors.log`. This means repeated
runs never collide or overwrite each other, and filenames sort chronologically.
Pass `--no-timestamp-prefix` to write the exact filename given instead.

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
| `--no-expand-formats {zip,tar,7z,rar} [...]` | (none) | Record these archive types as a plain file, without opening them |
| `--no-media` | off | Skip image/video metadata extraction |
| `--archive-media` | off | Also extract EXIF/video metadata for images/videos found inside archives |
| `--ignore-dirs NAME [NAME ...]` | (none) | Extra folder names to skip, added to the default list |
| `--exclude-paths PATTERN [PATTERN ...]` | (none) | Glob pattern(s) matched against a file/folder's full path |
| `--no-default-ignores` | off | Turn off the built-in default ignore list |
| `--no-timestamp-prefix` | off | Write the exact filename given, no `yyyy-mm-dd-hh-mm-ss_` prefix |
| `--progress-every` | 5000 | Progress line every N records (`0` = silent) |
| `-v`, `--verbose` | off | Log one line per file (not just per directory) to `<output>.log` |
| `--slow-threshold-seconds` | 10 | Warn in the log when a single operation takes longer than this |
| `--log-interval` | 60 | Seconds between progress heartbeats in `<output>.log` (`0` = off) |
| `--config PATH` | (none) | Read any of these options, plus `OUTPUT`, from a JSON file |

## Reading options from a file

`--config PATH` points at a JSON file (a plain object) supplying any of the
options above, plus `"output"` for the output filename itself:

```jsonc
{
  "output": "C:\\scans\\inventory.json",
  "root": ["C:\\", "D:\\"],
  "ignore_dirs": ["Backups", "OldProjects"],
  "exclude_paths": ["C:\\Users\\*\\Downloads\\*"],
  "no_media": false,
  "verbose": true,
  "slow_threshold_seconds": 5
}
```

Keys match the CLI dest names 1:1 (a flag like `--no-media` is `"no_media"`;
`--root`/`--ignore-dirs` accept either a JSON array or a single bare string as
shorthand for a one-item array). Precedence, applied per option independently:

1. A value given explicitly on the command line always wins.
2. Otherwise, the config file's value (if it sets that key) is used.
3. Otherwise, the built-in default applies.

So `filenav.py --config myscan.json --root E:\` scans only `E:\`, while every
other option (including `output`, `ignore_dirs`, etc.) still comes from
`myscan.json`. `OUTPUT` follows the same rule against the file's `"output"`
key: the CLI positional wins if given, otherwise `"output"` is used, otherwise
it falls back to `output.json` — `filenav.py --config myscan.json` with no
`"output"` in the file and nothing on the command line still runs, writing
`output.json` (timestamp-prefixed as usual) next to `filenav.py`.

Validation happens before any scanning starts: an unrecognized key, a value
of the wrong type (e.g. a string where a number is expected), a missing file,
or invalid JSON all print a clear error and exit with status `1`.

## Folder exclusion

Three mechanisms, matched independently (any one of them excludes a folder
and everything below it — or, for path/pattern, a single file):

1. **`.filenav-skip` marker** — any folder containing a file literally named
   `.filenav-skip` (content doesn't matter, even empty) is excluded.
2. **Default ignore list** — a folder whose *name* (case-insensitive, matched
   anywhere in the tree, not just at the root) is one of: `.git`, `.svn`,
   `.hg`, `node_modules`, `__pycache__`, `.venv`, `venv`, `.tox`,
   `.mypy_cache`, `.pytest_cache`, `dist`, `build`,
   `System Volume Information`, `$RECYCLE.BIN`, `.vscode`, `.idea`. Extend it
   with `--ignore-dirs NAME [NAME ...]`, or disable it entirely with
   `--no-default-ignores`.
3. **`--exclude-paths PATTERN [PATTERN ...]`** — a glob pattern (`*`, `?`,
   `[seq]`, via Python's `fnmatch`) matched against a file or folder's
   **full path**, not just its name. Where `--ignore-dirs` excludes every
   folder sharing a name, this can target one specific location, or match a
   naming pattern rather than an exact name — e.g.
   `--exclude-paths "C:\Users\*\Downloads\*" "*.tmp"`. A folder match
   excludes its whole subtree; a file match excludes only that file. `*`
   matches any characters including path separators (plain glob semantics,
   not `.gitignore`-style `**`), and either `\` or `/` works in a pattern on
   Windows (both get normalized the same way `fnmatch` normalizes the real
   path). Real filesystem paths only — unlike the default ignore list, it
   does not reach inside archives.

The output JSON's `options.ignore_dir_names`, `options.default_ignores_applied`,
and `options.exclude_path_patterns` fields record exactly what was in effect
for that run, and `summary.directories_skipped_via_marker` /
`directories_skipped_via_ignore_list` / `directories_skipped_via_path_pattern`
/ `files_skipped_via_path_pattern` report how many of each were actually
skipped.

The default ignore list also applies *inside* archives: an entry whose
internal path has an ignored directory anywhere in it (e.g. a Python wheel
or sdist containing `mypkg/__pycache__/...`, or a downloaded repo export
containing a `.git/` folder) is skipped the same way, whether or not that
archive itself came from an ignored real folder. `summary.archive_entries_skipped_ignore_list`
reports how many. (The `.filenav-skip` marker only applies to real
filesystem folders — there's no equivalent marker-file convention for
inside an archive.)

## Not opening certain archive types

`--exclude-paths`/`--ignore-dirs`/the default ignore list all leave the
archive file out of the output entirely. `--no-expand-formats {zip,tar,7z,rar}
[...]` is different: the archive file itself is still recorded normally
(path, filename, size, hash, dates — exactly like any other file), it's just
never opened to enumerate its contents. Useful when a specific archive type
is known to be large/slow/uninteresting to expand, without losing track of
the file's own existence and hash.

`tar` covers every tar variant as one choice, since they're all the same
underlying format once opened: `.tar`, `.tgz`, `.tbz2`, `.txz`, `.tar.gz`,
`.tar.bz2`, `.tar.xz`.

A skipped archive's record gets `"archive_format"` (still identifying what it
is) and `"archive_expansion_skipped": true`. `summary.archives_not_expanded`
counts how many were left unopened this way, separately from
`summary.archives_expanded`; `options.no_expand_formats` records which
formats were configured.

## EXIF/video metadata inside archives

Loose images/videos on disk already get `"image"`/`"video"` metadata
(dimensions, EXIF capture date, GPS, camera make/model, duration) by default.
Archive members don't, unless `--archive-media` is passed — off by default,
since it means decompressing every image/video member found, which adds real
cost on a scan with a lot of archived photos/videos.

Implementation differs by format for performance reasons: zip/7z/rar extract
each qualifying member individually (cheap, since those formats support
random access to one member). Tar computes it during the *same* single
sequential pass already used for hashing — extracting a second time via a
fresh `tarfile.open()` per member would reintroduce the exact O(n²)
reopening cost that pass exists to avoid; see [[Architecture]] for the full
story.

Subject to the same `--max-hash-size-mb` cap already used to skip hashing
huge files. `summary.archive_media_extracted` counts how many members got
metadata this way; `options.archive_media_metadata` records whether it was on.

## Errors

A file or archive member that couldn't be read/hashed/opened doesn't stop the
scan — it's recorded as an error and skipped. Every error is streamed, as it
happens, to a sidecar log next to the output file — `<output>.errors.log`
(e.g. `output.json` → `output.errors.log`), one JSON object per line
(`{"path": ..., "error": ...}`). It's independently greppable, and it's never
lost even if the scan later aborts hard, since each line is flushed to disk
immediately.

The main JSON's own `errors` array is only a capped sample (the first 1000);
`summary.errors` is the true total count, and `summary.errors_sample_truncated`
says whether anything was left out of the embedded sample. The sidecar log
always has every error regardless. This design keeps scan-time memory use
bounded even in a pathological case (e.g. a flaky network drive throwing
permission errors on huge numbers of files) — nothing about output size
(JSON or error count) grows memory usage during the scan; everything streams
to disk as it's produced.

If something still goes wrong badly enough to abort the whole scan (an
unexpected bug, or Ctrl+C), the output JSON is still finished as valid JSON
with everything scanned up to that point — `summary.aborted` and
`summary.abort_reason` record what happened, and the process exits with a
non-zero status, but no data already collected is thrown away.

A password-protected `.7z`/`.rar` fails fast and is recorded as a normal
error, rather than hanging the scan waiting on a password prompt that (in an
unattended run) will never be answered — see [[Architecture]] for why this
needed a real fix, not just a try/except.

## Diagnosing a slow or stuck scan

A third sidecar, `<output>.log` (`summary.log_file`), is written for
diagnosing the scan process itself — separate from the errors log, which
records *what couldn't be scanned*, this records *what the scan was doing*:

- One `INFO` line per directory entered (`Scanning directory (N files): ...`)
  and per archive expanded — by default, not per file, since directories are
  orders of magnitude fewer. If a scan hangs, the log's last line names
  exactly where. Pass `--verbose`/`-v` for a `DEBUG` line per file too.
- A periodic `INFO` heartbeat, every `--log-interval` seconds (default 60,
  `0` disables it): running totals, the processing rate since the last
  heartbeat, and the scratch temp folder's current entry count (used for
  expanding nested archives) — a number that keeps climbing across
  heartbeats instead of staying near zero indicates a resource leak, not
  just a slow file.
- A `WARNING` line for any single hash, archive listing, or archive-member
  extraction slower than `--slow-threshold-seconds` (default 10), naming the
  exact file.

`WARNING`-and-above lines also print live to the console; `INFO`/`DEBUG`
lines are file-only.

## Output JSON shape

```jsonc
{
  "scan_started": "2026-09-20T01:00:00",
  "host": "...",
  "platform": "...",
  "roots": ["C:\\"],
  "options": { "hash_algo": "sha256", "config_file": null, ... },
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
  "summary": {
    "files_recorded": 1234, "archives_expanded": 5, "elapsed_seconds": 42.1,
    "errors": 3, "errors_sample_truncated": false, "error_log_file": "C:\\...\\output.errors.log",
    "log_file": "C:\\...\\output.log",
    "aborted": false, "abort_reason": null,
    "...": "..."
  }
}
```

The archive file itself (`archive.zip`) is always recorded as an ordinary
`"file"` entry too — `"archive_entry"` records are its contents, in addition to
that.

The output file always ends with one `"self_reference": true` entry describing
itself (path/filename/approximate size) — it is recorded like any other file
would be, never parsed as meaningful content.

Related: [[Getting-Started]], [[Architecture]].
