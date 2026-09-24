"""Streaming error sink: writes every error to a sidecar log file as it
happens (one JSON object per line, so it's greppable and never lost even if
the main scan later aborts hard), while keeping only a bounded sample in
memory for embedding in the main output JSON.

A plain list would hold every error for the whole run -- fine normally, but
not bounded: a pathological run (a flaky network drive throwing permission
errors on huge numbers of files) could grow it without limit. This has the
same .append() interface as a list, so it's a drop-in replacement everywhere
the scanner/archive code does errors.append(...).
"""

import json

from .writer import _sanitize


class ErrorSink:
    def __init__(self, log_path, sample_limit=1000):
        self.log_path = log_path
        self.sample_limit = sample_limit
        self.sample = []
        self.count = 0
        self._log = open(log_path, "w", encoding="utf-8")

    def append(self, error):
        error = _sanitize(error)
        self.count += 1
        if len(self.sample) < self.sample_limit:
            self.sample.append(error)
        try:
            line = json.dumps(error, default=str, ensure_ascii=False)
        except (TypeError, ValueError, UnicodeEncodeError) as exc:
            line = json.dumps({"path": str(error.get("path", ""))[:500],
                                "error": f"error record could not be serialized: {exc}"})
        self._log.write(line + "\n")
        self._log.flush()  # errors are rare relative to files; survive a hard crash right after

    def close(self):
        self._log.close()
