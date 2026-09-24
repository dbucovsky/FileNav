"""Diagnostic logging for a scan run.

Separate from the errors sidecar (filenavlib/errorlog.py), which records
*what couldn't be scanned*. This is for diagnosing the scan process itself --
what it was doing, how fast, and (if it hangs) exactly where it last made
progress, since the log's last line is whatever was in flight when it stopped.
"""

import logging
import sys


def setup_logging(log_path, verbose=False):
    logger = logging.getLogger("filenav")
    logger.setLevel(logging.DEBUG)
    for handler in logger.handlers[:]:
        handler.close()  # release any previous run's file handle (Windows locks open files)
        logger.removeHandler(handler)
    logger.propagate = False

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG if verbose else logging.INFO)
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s"))
    logger.addHandler(file_handler)

    # Warnings/errors also surface live on the console; INFO/DEBUG stay in the file only.
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(logging.WARNING)
    console_handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    logger.addHandler(console_handler)

    return logger
