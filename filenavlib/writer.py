"""Incremental JSON writer.

Writes the "files" array one record at a time as the scan progresses, instead
of building the whole result in memory first -- a full-drive scan can produce
millions of records.
"""

import json
import os


class JsonScanWriter:
    def __init__(self, path, meta):
        self.path = path
        self._f = open(path, "w", encoding="utf-8")
        self._first = True
        self._f.write("{\n")
        for key, value in meta.items():
            self._f.write(f"  {json.dumps(key)}: {json.dumps(value, default=str)},\n")
        self._f.write('  "files": [\n')

    def write_record(self, record):
        prefix = "" if self._first else ",\n"
        self._f.write(prefix + "    " + json.dumps(record, default=str, ensure_ascii=False))
        self._first = False

    def current_size(self):
        """Size of the file on disk right now, while it is still being written."""
        self._f.flush()
        return os.fstat(self._f.fileno()).st_size

    def close(self, errors, summary):
        self._f.write("\n  ],\n")
        self._f.write('  "errors": ' + json.dumps(errors, default=str, ensure_ascii=False) + ",\n")
        self._f.write('  "summary": ' + json.dumps(summary, default=str, ensure_ascii=False) + "\n")
        self._f.write("}\n")
        self._f.close()
