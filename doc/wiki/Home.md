# FileNav Wiki

FileNav is a Python command-line scanner that walks drives/folders/files and
writes a single JSON inventory: path, filename, timestamps, size, and a hash
for every file for validation and duplicate detection.

- Opens archives (`.zip`, `.tar`/`.tar.gz`/`.tar.bz2`/`.tar.xz`, `.7z`, `.rar`)
  and records every member too, recursively — a zip inside a zip inside a
  zip, and so on.
- Extracts EXIF (capture date, GPS, camera) and video metadata for images and
  videos, on disk by default, optionally inside archives too.
- Folder exclusion by exact name, glob path pattern, or a per-folder marker
  file — plus a sensible default ignore list (`.git`, `node_modules`, etc.)
  applied automatically.
- Every option can live in a reusable JSON config file instead of a long
  command line.
- Built to survive a real whole-drive scan: streams output instead of
  buffering it in memory, never lets one bad file or archive abort the run,
  and writes a diagnostic log so a slow or stuck scan can actually be
  diagnosed rather than just killed and re-run blind.

## Pages

- [[Getting-Started]] — install, run your first scan
- [[Usage]] — full CLI flag reference, config file, exclusion rules, output
  JSON shape
- [[Architecture]] — how the scanner is put together, module by module, and
  the real-world bugs (and fixes) behind several of its design choices

See the repository root `README.md` for a quick-reference version of the same
information, and `CHANGELOG.md` for version history.
