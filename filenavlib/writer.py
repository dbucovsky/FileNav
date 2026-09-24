"""Incremental JSON writer.

Writes the "files" array one record at a time as the scan progresses, instead
of building the whole result in memory first -- a full-drive scan can produce
millions of records.
"""

import json
import os


def _sanitize(value):
    """Make a value safe to json.dumps(..., ensure_ascii=False) and write as UTF-8.

    Archive member names (tarfile in particular) and other loosely-encoded
    metadata can come back as Python strings containing lone surrogate code
    points, which json.dumps happily stringifies but which then blow up with
    a UnicodeEncodeError the moment they're written to a UTF-8 file. Round-trip
    through surrogateescape/replace so a handful of unrepresentable characters
    become U+FFFD instead of crashing a multi-hour scan.
    """
    if isinstance(value, str):
        try:
            value.encode("utf-8")
            return value
        except UnicodeEncodeError:
            return value.encode("utf-8", "surrogateescape").decode("utf-8", "replace")
    if isinstance(value, dict):
        return {k: _sanitize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize(v) for v in value]
    return value


class JsonScanWriter:
    def __init__(self, path, meta):
        self.path = path
        self._f = open(path, "w", encoding="utf-8")
        self._first = True
        self._f.write("{\n")
        for key, value in meta.items():
            self._f.write(f"  {json.dumps(key)}: {json.dumps(_sanitize(value), default=str, ensure_ascii=False)},\n")
        self._f.write('  "files": [\n')

    def write_record(self, record):
        prefix = "" if self._first else ",\n"
        try:
            text = json.dumps(_sanitize(record), default=str, ensure_ascii=False)
        except (TypeError, ValueError, UnicodeEncodeError) as exc:
            # Last-resort fallback: never let one bad record take the whole scan down.
            text = json.dumps({
                "type": "unserializable_record",
                "path": str(record.get("path", ""))[:500],
                "error": f"record could not be serialized: {exc}",
            })
        self._f.write(prefix + "    " + text)
        self._first = False

    def current_size(self):
        """Size of the file on disk right now, while it is still being written."""
        self._f.flush()
        return os.fstat(self._f.fileno()).st_size

    def close(self, errors, summary):
        self._f.write("\n  ],\n")
        self._f.write('  "errors": ' + json.dumps(_sanitize(errors), default=str, ensure_ascii=False) + ",\n")
        self._f.write('  "summary": ' + json.dumps(_sanitize(summary), default=str, ensure_ascii=False) + "\n")
        self._f.write("}\n")
        self._f.close()
