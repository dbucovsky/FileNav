"""Best-effort date/time extraction from free text (filenames, folder names),
plus a small set of narrow anomaly flags for the real timestamp sources
(filesystem created/modified, EXIF/video capture date).

Textual date-guessing is inherently heuristic: real filenames are too varied
for any fixed pattern set to be exhaustive, and some matches (especially the
bare-year fallback) will be false positives. Every candidate carries a
"confidence" and, where a numeric date could be read more than one valid way,
"ambiguous": true with both readings returned -- never a silent guess.
"""

import os
import re
from datetime import datetime, timedelta

_MONTH_NAMES = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}
_MONTH_ABBREV_PATTERN = "|".join(sorted(_MONTH_NAMES, key=len, reverse=True))
_WEEKDAY_PATTERN = (
    "mon|tue|tues|wed|wednesday|thu|thur|thurs|fri|sat|sun"
    "|monday|tuesday|thursday|friday|saturday|sunday"
)

_PLAUSIBLE_YEAR_MIN = 1990
_PLAUSIBLE_YEAR_MAX = datetime.now().year + 1

# Known OS placeholder/uninitialized timestamps: Unix epoch, FAT/DOS default,
# Windows FILETIME zero. Timestamps are converted to *local* time upstream
# (os.stat -> datetime.fromtimestamp()), so a value stored as exactly one of
# these in UTC can land a day to either side locally depending on timezone --
# checked with a tolerance window, not an exact (year, month, day) match.
_EPOCH_DEFAULTS = [datetime(1970, 1, 1), datetime(1980, 1, 1), datetime(1601, 1, 1)]
_EPOCH_TOLERANCE = timedelta(days=2)


def _make_date_candidate(dt, has_time, source_text, pattern, confidence, ambiguous=False):
    return {
        "value": dt.isoformat() if has_time else dt.date().isoformat(),
        "source_text": source_text,
        "pattern": pattern,
        "confidence": confidence,
        "ambiguous": ambiguous,
    }


def _iso_datetime_matches(text):
    pattern = re.compile(
        r"(\d{4})-(\d{2})-(\d{2})[-_ T](\d{2})[-:_](\d{2})[-:_](\d{2})"
    )
    for m in pattern.finditer(text):
        y, mo, d, h, mi, s = (int(g) for g in m.groups())
        try:
            dt = datetime(y, mo, d, h, mi, s)
        except ValueError:
            continue
        if not (_PLAUSIBLE_YEAR_MIN <= y <= _PLAUSIBLE_YEAR_MAX):
            continue
        yield m.start(), m.end(), [_make_date_candidate(dt, True, m.group(0), "iso_datetime", "high")]


def _iso_date_matches(text):
    pattern = re.compile(r"(\d{4})[-_.](\d{2})[-_.](\d{2})")
    for m in pattern.finditer(text):
        y, mo, d = (int(g) for g in m.groups())
        try:
            dt = datetime(y, mo, d)
        except ValueError:
            continue
        if not (_PLAUSIBLE_YEAR_MIN <= y <= _PLAUSIBLE_YEAR_MAX):
            continue
        yield m.start(), m.end(), [_make_date_candidate(dt, False, m.group(0), "iso_date", "high")]


def _compact_date_matches(text):
    pattern = re.compile(r"(?<!\d)(\d{8})(?!\d)")
    for m in pattern.finditer(text):
        digits = m.group(1)
        y, mo, d = int(digits[:4]), int(digits[4:6]), int(digits[6:8])
        try:
            dt = datetime(y, mo, d)
        except ValueError:
            continue
        if not (_PLAUSIBLE_YEAR_MIN <= y <= _PLAUSIBLE_YEAR_MAX):
            continue
        yield m.start(), m.end(), [_make_date_candidate(dt, False, m.group(0), "compact_date", "medium")]


def _weekday_month_time_year_matches(text):
    pattern = re.compile(
        rf"(?:{_WEEKDAY_PATTERN})-({_MONTH_ABBREV_PATTERN})-(\d{{1,2}})-(\d{{2}})_(\d{{2}})_(\d{{2}})-(\d{{4}})",
        re.IGNORECASE,
    )
    for m in pattern.finditer(text):
        month_name, day, h, mi, s, year = m.groups()
        mo = _MONTH_NAMES.get(month_name.lower())
        if mo is None:
            continue
        y, d, h, mi, s = int(year), int(day), int(h), int(mi), int(s)
        try:
            dt = datetime(y, mo, d, h, mi, s)
        except ValueError:
            continue
        if not (_PLAUSIBLE_YEAR_MIN <= y <= _PLAUSIBLE_YEAR_MAX):
            continue
        yield m.start(), m.end(), [_make_date_candidate(dt, True, m.group(0), "weekday_month_time_year", "high")]


def _month_name_matches(text):
    day_month_year = re.compile(
        rf"(\d{{1,2}})[- ]({_MONTH_ABBREV_PATTERN})[a-z]*[-, ]+(\d{{4}})",
        re.IGNORECASE,
    )
    month_day_year = re.compile(
        rf"({_MONTH_ABBREV_PATTERN})[a-z]*[-, ]+(\d{{1,2}})[-, ]+(\d{{4}})",
        re.IGNORECASE,
    )
    for m in day_month_year.finditer(text):
        day, month_name, year = m.groups()
        mo = _MONTH_NAMES.get(month_name.lower())
        if mo is None:
            continue
        y, d = int(year), int(day)
        try:
            dt = datetime(y, mo, d)
        except ValueError:
            continue
        if not (_PLAUSIBLE_YEAR_MIN <= y <= _PLAUSIBLE_YEAR_MAX):
            continue
        yield m.start(), m.end(), [_make_date_candidate(dt, False, m.group(0), "month_name_date", "high")]
    for m in month_day_year.finditer(text):
        month_name, day, year = m.groups()
        mo = _MONTH_NAMES.get(month_name.lower())
        if mo is None:
            continue
        y, d = int(year), int(day)
        try:
            dt = datetime(y, mo, d)
        except ValueError:
            continue
        if not (_PLAUSIBLE_YEAR_MIN <= y <= _PLAUSIBLE_YEAR_MAX):
            continue
        yield m.start(), m.end(), [_make_date_candidate(dt, False, m.group(0), "month_name_date", "high")]


def _ambiguous_numeric_matches(text):
    pattern = re.compile(r"(?<!\d)(\d{1,2})[-/](\d{1,2})[-/](\d{4})(?!\d)")
    for m in pattern.finditer(text):
        a, b, year = (int(g) for g in m.groups())
        y = year
        if not (_PLAUSIBLE_YEAR_MIN <= y <= _PLAUSIBLE_YEAR_MAX):
            continue

        a_valid_as_month = 1 <= a <= 12
        b_valid_as_month = 1 <= b <= 12
        candidates = []

        # a=month,b=day
        if a_valid_as_month:
            try:
                candidates.append(("month_first", datetime(y, a, b)))
            except ValueError:
                pass
        # b=month,a=day
        if b_valid_as_month:
            try:
                candidates.append(("day_first", datetime(y, b, a)))
            except ValueError:
                pass

        if not candidates:
            continue

        is_ambiguous = len(candidates) > 1 and candidates[0][1] != candidates[1][1]
        results = [
            _make_date_candidate(dt, False, m.group(0), f"ambiguous_numeric_{label}", "medium", ambiguous=is_ambiguous)
            for label, dt in candidates
        ]
        yield m.start(), m.end(), results


def _bare_year_matches(text):
    pattern = re.compile(r"(?<!\d)(\d{4})(?!\d)")
    for m in pattern.finditer(text):
        y = int(m.group(1))
        if not (_PLAUSIBLE_YEAR_MIN <= y <= _PLAUSIBLE_YEAR_MAX):
            continue
        dt = datetime(y, 1, 1)
        yield m.start(), m.end(), [_make_date_candidate(dt, False, m.group(0), "bare_year", "low")]


# Ordered most-confident first; a later matcher never overrides a span
# already claimed by an earlier one (see find_dates_in_text).
_MATCHERS = [
    _iso_datetime_matches,
    _weekday_month_time_year_matches,
    _month_name_matches,
    _iso_date_matches,
    _ambiguous_numeric_matches,
    _compact_date_matches,
    _bare_year_matches,
]


def find_dates_in_text(text):
    """Return every date candidate found in a single filename or path segment
    (never a full path -- see find_dates_in_path_segments for that), most
    confident first, with no two candidates overlapping the same substring.
    """
    if not text:
        return []

    consumed = []

    def is_free(start, end):
        return all(end <= s or start >= e for s, e in consumed)

    candidates = []
    for matcher in _MATCHERS:
        for start, end, results in matcher(text):
            if not is_free(start, end):
                continue
            consumed.append((start, end))
            candidates.extend(results)
    return candidates


def find_dates_in_path_segments(dir_path):
    """Split a directory path into its individual folder names and run
    find_dates_in_text() on each. Only segments with at least one match are
    returned, as [{"segment": name, "dates": [...]}, ...].
    """
    _drive, tail = os.path.splitdrive(dir_path)
    segments = [seg for seg in re.split(r"[\\/]", tail) if seg]

    results = []
    for segment in segments:
        dates = find_dates_in_text(segment)
        if dates:
            results.append({"segment": segment, "dates": dates})
    return results


def _parse_known_datetime_string(value):
    """Parse a datetime out of whichever of the few string formats this
    project's own records actually use: ISO 8601 (scanner.py's own records),
    EXIF ("YYYY:MM:DD HH:MM:SS"), or hachoir's plain str(datetime) output
    ("YYYY-MM-DD HH:MM:SS"). Returns None for anything else rather than
    raising -- real-world EXIF/video metadata is sometimes malformed.
    """
    if not value or not isinstance(value, str):
        return None
    for parser in (
        datetime.fromisoformat,
        lambda s: datetime.strptime(s, "%Y:%m:%d %H:%M:%S"),
        lambda s: datetime.strptime(s, "%Y-%m-%d %H:%M:%S"),
    ):
        try:
            return parser(value)
        except (ValueError, TypeError):
            continue
    return None


def compute_flags(created_str, modified_str, capture_date_str, reference_time):
    """Flags for the *real* timestamp sources only (filesystem created/modified,
    EXIF/video capture date) -- never applied to fuzzy filename/folder-path text
    matches, since a folder literally named "1970 Archive" isn't evidence of
    anything wrong.

    Deliberately narrow: does NOT flag created > modified or a capture date
    that differs from the file's own timestamps -- both are normal for a
    copied or migrated file and would flag almost every archived photo.
    """
    flags = set()
    for value in (created_str, modified_str, capture_date_str):
        dt = _parse_known_datetime_string(value)
        if dt is None:
            continue
        if reference_time is not None and dt > reference_time:
            flags.add("future_date")
        if any(abs(dt - epoch) <= _EPOCH_TOLERANCE for epoch in _EPOCH_DEFAULTS):
            flags.add("default_epoch_date")
    return sorted(flags)
