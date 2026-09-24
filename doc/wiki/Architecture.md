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
                        memory; sanitizes strings so an unwritable character
                        can't crash the write.
  errorlog.py                Streams every error to a sidecar log file as it
                        happens, keeping only a bounded in-memory sample for
                        the main JSON -- a list-like .append() drop-in.
  scanlog.py                   Configures the "filenav" logger: a sidecar
                        <output>.log file (INFO+, DEBUG with --verbose) plus
                        WARNING+ echoed to the console.
  configfile.py                 Loads/validates the --config JSON file
                        against a caller-supplied schema; knows nothing
                        about argparse.
  dateanalysis.py                Heuristic date extraction from filenames/
                        folder names (--date-analysis), plus a narrow set of
                        anomaly flags against the real timestamp sources.
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

- Regular files: `sha256` (or whatever `--hash-algo` is), computed by
  streaming the file.
- Zip archive members: the zip format's own built-in CRC32 (`ZipInfo.CRC`) —
  free, no decompression needed.
- Tar archive members: `sha256` (or `--hash-algo`), computed for every member
  in one single sequential pass over the tar during listing itself (see next
  section for why this isn't optional).
- 7z/rar archive members: the CRC 7-Zip's own `-slt` listing reports, when
  present; extracted and hashed on demand only if it isn't.

Each record's `hash_algo` field says which one was used, so nothing is silently
inconsistent across the output.

## Why tar members must be hashed in one pass, not one `tarfile.open()` each

A real-world scan got stuck for over 20 minutes hashing one single archive: a
143,770-member `.tar.bz2`, at a pace of ~50 seconds *per small source file* —
on track to take months. `hash_member()` for `"tar"` originally reopened the
archive fresh, per member: `tarfile.open(path)` then `tf.extractfile(entry["name"])`.
A compressed tar is not seekable — reaching member N means bzip2-decompressing
the *entire* stream again from byte zero, every single time. That's O(n²)
over the member count, and it scales with the archive's total decompressed
size, not the individual member's size — which is exactly why files that
"look too insignificant to cause a slowdown" were the ones stuck for a minute
each: the file being hashed was never the expensive part.

`_list_tar_entries()` now iterates the `TarFile` object directly (`for member
in tf`, not `tf.getmembers()` up front) and extracts+hashes each file member
the moment it's encountered, in the same single sequential pass that already
reads every header. The result is stashed on the entry as `precomputed_hash`;
`hash_member()` checks for that field first and returns it directly for tar,
the same way it already does for zip's free CRC32. This turns the cost back
into what listing alone already cost (one sequential decompression), instead
of costing that all over again per member.

`archives._extract_member_to_temp()` for tar (used only when a tar member is
itself a nested archive needing recursion) still reopens per call — a known,
much rarer remaining case: it only fires when a tar contains other archives,
not for ordinary hashing. Not fixed here since it wasn't what caused this
incident and would need queuing extractions during the same listing pass
rather than a small, isolated change. `--no-expand-formats tar` is the
available escape hatch in the meantime — the archive file is still recorded
(and hashed normally, as a plain file), just never opened.

## Resilience: one bad file must not lose the whole scan

A whole-drive scan runs for a long time and will hit real surprises: archive
member names with broken encodings, corrupt files, permission quirks. Three
layers keep any of that from destroying hours of work:

1. `writer.py` sanitizes every string before it's written, so an unwritable
   character (e.g. a lone surrogate from a badly-encoded archive member name)
   never reaches the file handle and can't raise mid-write.
2. `scanner.py` and `archives.py` each wrap their per-item processing in
   `try/except` — one bad file, or one bad archive member, is logged to
   `errors` and skipped; everything else keeps going.
3. `filenav.py`'s top-level loop catches anything that still gets through
   (plus Ctrl+C) and finishes the JSON file anyway, marking
   `summary.aborted`/`summary.abort_reason` rather than leaving a truncated,
   unparseable file behind.

None of this holds if the error bookkeeping itself grows without bound, so
`errorlog.ErrorSink` replaces the plain `errors` list: every error is written
to a sidecar log immediately (and flushed, so a hard crash right after doesn't
lose it) and only a capped sample (`errors.sample`, default first 1000) is
kept in memory for the main JSON. `errors.count` is the single authoritative
total — nothing else tracks its own separate error counter, so there's no way
for the numbers to drift apart.

## Found via this design: the nested-archive cleanup leak

Real-world use surfaced a case the try/except layers above don't cover:
resource leaks that make a *long* scan progressively slower without ever
raising an exception. `archives._extract_member_to_temp()` extracts a nested
archive's member into a freshly made `tempfile.mkdtemp()` directory; cleanup
used to remove `os.path.dirname(extracted_path)` -- the member's immediate
parent. Whenever the member's own internal name has a subdirectory component
(ordinary in real zips, e.g. `docs/manual.zip`), that only deletes the
innermost subfolder and leaves the actual `mkdtemp()` directory behind, for
every nested archive found, for the rest of the run. `_extract_member_to_temp`
now returns `(extracted_path, dest_dir)` and the caller removes `dest_dir`
itself.

This class of bug (a leak, not a crash) is exactly why the heartbeat in
`filenav.py` reports the scratch directory's entry count on every tick, not
just file/error counts: a number that climbs steadily across heartbeats
instead of returning near zero is the signature of a leak like this one, and
would have pointed straight at it.

## Why `--config` uses argparse.SUPPRESS instead of comparing to defaults

Merging CLI flags with a config file needs to tell "the user didn't pass
this flag" apart from "the user passed this flag with the same value as the
default" — the naive approach (compare `args.X` against the built-in
default after parsing) gets this wrong: `filenav.py out.json --hash-algo
sha256 --config c.json` would wrongly let `c.json`'s `hash_algo` win, since
`sha256` also happens to be the built-in default.

Every layered option's `add_argument(..., default=argparse.SUPPRESS)` in
`filenav.py` fixes this properly: if the flag isn't passed, `vars(args)`
simply doesn't have that key at all, so "was it explicitly given" is a plain
`in` check rather than a value comparison. The real defaults live separately
in `filenav.ARG_DEFAULTS`, and `main()` layers `ARG_DEFAULTS` → config file →
CLI-provided keys, in that order, then copies the merged result back onto
`args` so the rest of `main()` reads `args.X` exactly as before.

## Two exclusion mechanisms, one shared `os.walk` pass

`--ignore-dirs` (exact name match) and `--exclude-paths` (glob against the
full path, via stdlib `fnmatch`) are deliberately separate checks in
`scanner.should_skip_dir()` / the eager `dirnames[:]` pruning loop in
`walk_root()`, rather than one generalized "matcher" — a name check is a
plain set lookup (cheap, exact), while a pattern check compiles and runs a
regex per candidate per pattern; keeping them distinct means a scan with no
`--exclude-paths` set (the common case) never pays that cost. Both still run
in the same single pass over `dirnames`/`filenames` per directory, so a
folder is only ever `os.walk`'d into once its exclusion status from *every*
mechanism (marker, name, pattern) is already known — matching the existing
`.filenav-skip` marker's short-circuit behavior. `matches_any_pattern()` is
exposed standalone (not just inlined) specifically so archive-internal
filtering or a future exclusion mechanism can reuse it without duplicating
the `fnmatch` call.

## Why every 7z subprocess call sets `stdin=subprocess.DEVNULL`

A real scan got stuck for 28+ minutes (until interrupted) on one
password-protected `.7z`. `subprocess.run()`/`subprocess.Popen()` for 7z
never set `stdin=`, so the child process inherited whatever the parent's own
stdin was — a real, live console in an interactive run. When 7z hits an
archive with encrypted headers (a password needed just to list filenames,
not only to extract), it prints "Enter password (will not be echoed):" and
blocks reading from stdin — and since nobody is sitting at the console
mid-unattended-scan to type one, it waits forever.

Verified directly (`_run_7z` in `archives.py`, and the `-so` streaming
extraction `Popen` in `hash_member`): with `stdin=subprocess.DEVNULL`, 7z
hits immediate EOF trying to read the password, prints "Break signaled", and
exits with a clean non-zero code in well under a second — instead of
blocking. Both call sites now set it. `config.SEVEN_ZIP_TIMEOUT_SECONDS`
(300s) is added as a second, defense-in-depth layer via `subprocess.run(...,
timeout=...)` on the listing call, in case some other kind of stall occurs;
`stdin=DEVNULL` is the actual, verified fix for the reported case, not the
timeout.

Only `.7z`/`.rar` are at risk here, since both route through the 7z CLI.
Zip's password-protection can't hang the same way: `zipfile` has no
interactive prompt at all — an encrypted entry just raises `RuntimeError`
immediately, which `hash_member`'s existing `try/except Exception` around
that whole block already turned into a normal per-file error. Tar has no
password-protection concept at the format level.

## Archive-internal media metadata: two different implementations, one reason

`--archive-media` (off by default) extracts EXIF/video metadata for images
and videos found while expanding an archive, reusing the same
`media.get_image_info()`/`get_video_info()` on-disk functions unchanged --
just pointed at a temp file holding the member's decompressed bytes instead
of a real file on disk. *How* those bytes get there differs by format,
directly following from the tar hashing fix above:

- **zip/7z/rar** (`_extract_media_for_entry`): extracted on demand, per
  qualifying member, via the same generic `_extract_member_to_temp()` already
  used for nested-archive recursion. Safe to reopen per-member for these
  formats -- zip has a central directory, 7z has its own internal index, so
  neither needs to rescan from byte zero to reach one member.
- **tar** (`_extract_media_from_bytes`, fed from `_list_tar_entries`): a
  compressed tar is *not* seekable, so a fresh `tarfile.open()` per media
  member would reintroduce the exact O(n²) reopening cost the tar hashing
  fix exists to avoid. Instead, when a tar member both matches an image/video
  extension and the option is on, `_list_tar_entries()` reads its bytes once
  (bounded by the same `--max-hash-size-mb` cap as hashing) during the
  single sequential pass it already makes, and reuses those same bytes for
  *both* the hash (via `hashing.hash_stream()` on a wrapping `io.BytesIO`,
  not the live tar stream) and the media extraction -- one read instead of
  two, and zero extra archive reopens.

Both paths write to a temp file under the run's shared `scratch_dir` (so
cleanup on a hard abort is the same as everywhere else) and delete it
immediately after; only `"image"`/`"video"` survive on the record, matching
the shape a loose on-disk file's record already has.

## Date analysis: heuristic by design, and two performance decisions

`dateanalysis.find_dates_in_text()` tries an ordered list of regex patterns
against a single filename or folder name and keeps the highest-confidence
match for any given substring (a `consumed` span list prevents, say, the
bare-year fallback from also matching the `2019` inside an already-claimed
`08-09-2019`). This can never be exhaustive — real filenames are too varied
— so every candidate carries a `"confidence"` rather than pretending to be
authoritative, and a numeric date that's genuinely ambiguous (`08-09-2019`)
returns *both* readings tagged `"ambiguous": true` instead of silently
guessing a locale convention.

Two performance decisions, both following directly from lessons learned
earlier in this project (the tar O(n²) hashing fix):

1. **Folder-path dates are computed once per directory, not once per file.**
   `os.walk()`'s `dirpath` is constant for every file returned in the same
   iteration, so `scanner.walk_root()` calls
   `dateanalysis.find_dates_in_path_segments()` exactly once per directory
   (right next to the existing `"Scanning directory"` log line) and passes
   the result into `build_file_record()` for every file in that batch.
2. **Archive-internal folder segments are memoized per archive.** Many
   archive members commonly share the same few ancestor folders (`src`,
   `docs`, ...); `archives.process_archive_file()` keeps a plain dict cache
   keyed by segment string, scoped to that one archive's call (not
   scan-wide — simpler, and the within-one-archive redundancy is what
   actually shows up in practice).

**Why the epoch-placeholder flag uses a tolerance window, not an exact date
match:** `default_epoch_date` originally compared `(year, month, day)`
against exactly `(1970, 1, 1)`/`(1980, 1, 1)`/`(1601, 1, 1)` and missed real
cases in testing. The root cause: every timestamp reaching `compute_flags()`
has already gone through `datetime.fromtimestamp()` upstream, which converts
to *local* time — on a UTC-5 machine, `os.utime(path, (0, 0))` (literally
"set to the Unix epoch") produces `1969-12-31 19:00:00` locally, one day
short of the exact-match check. Fixed by comparing `abs(dt - epoch) <=
timedelta(days=2)` against each reference instant instead of an exact
calendar-date match — robust to timezone shift without being so wide it
could catch an unrelated nearby date.

Related: [[Home]], [[Usage]].
