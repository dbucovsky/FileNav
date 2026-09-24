"""Loads and validates a JSON options file for the --config CLI flag.

Kept generic (schema passed in by the caller) rather than hardcoding
filenav.py's own option set here, so the two stay in one place (filenav.py)
without this module needing to know about argparse at all.
"""

import json


class ConfigError(Exception):
    pass


def load_config(path, schema):
    """schema: dict of option name -> expected type (str/int/float/bool/list,
    where list always means "list of strings"). Returns a dict of validated,
    type-coerced values (JSON ints are widened to float where the schema
    calls for float). Raises ConfigError with a human-readable message on any
    problem: missing file, invalid JSON, an unknown key, or a wrong type.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except OSError as exc:
        raise ConfigError(f"could not read config file {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"config file {path} is not valid JSON: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"config file {path} must contain a JSON object at the top level")

    result = {}
    for key, value in raw.items():
        if key not in schema:
            raise ConfigError(f"unknown option {key!r} in config file {path}")
        expected = schema[key]

        if expected is list and isinstance(value, str):
            value = [value]  # a bare string is accepted as shorthand for a one-item list

        if expected is bool:
            ok = isinstance(value, bool)
        elif expected in (int, float):
            ok = isinstance(value, (int, float)) and not isinstance(value, bool)
        elif expected is list:
            ok = isinstance(value, list) and all(isinstance(v, str) for v in value)
        else:
            ok = isinstance(value, expected)

        if not ok:
            raise ConfigError(
                f"option {key!r} in config file {path} must be a {expected.__name__}, got {value!r}"
            )

        if expected is float:
            value = float(value)

        result[key] = value

    return result
