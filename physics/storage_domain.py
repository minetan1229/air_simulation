"""Storage domain -- JSON / CSV export, import and on-disk artefact management.

Implements AGENTS.md 2.3 and 7.5 (persistence + export):

* **Flight log** -- ``CSV`` (RFC 4180: ``CRLF`` terminators, minimal quoting,
  doubled embedded quotes) and ``JSON`` round-trips of
  :class:`physics.config_schema.FlightLogSample`.  The CSV header is always
  :data:`physics.config_schema.FLIGHT_LOG_COLUMNS` in declaration order.
* **Optimiser results** -codex approval
- flattened to one CSV row per history entry with the
  header ``evaluation,score,<objectives...>,<design variables...>``.
* **Configuration bundles** -- validated against the contract dataclasses
  (``AircraftConfig`` / ``EnvironmentConfig`` / ``LaunchConfig`` /
  ``SimulationSettings``) so a malformed preset can never reach the solvers.
* :class:`StorageManager` -- thread-safe, ``utf-8`` + ``newline=""`` file I/O
  below a single root directory with a **rejecting** path-traversal policy
  (see :func:`sanitise_artefact_name`).

Everything is pure-Python (no numpy/pandas dependency) so the module works on
the same interpreter that serves the Flask API and the pytest suite.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import threading
from dataclasses import MISSING
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .config_schema import (
    DESIGN_VARIABLE_CATALOGUE,
    FLIGHT_LOG_COLUMNS,
    OBJECTIVE_DIRECTIONS,
    AircraftConfig,
    EnvironmentConfig,
    FlightLogSample,
    LaunchConfig,
    SimulationSettings,
    as_dict,
    default_config_bundle,
    from_dict,
)

__all__ = [
    "CSV_MIMETYPE",
    "JSON_MIMETYPE",
    "CONFIG_SUFFIX",
    "RESULT_SUFFIX",
    "FLIGHT_SUFFIX",
    "CONFIG_SECTIONS",
    "flight_log_to_rows",
    "sample_from_row",
    "rows_to_csv",
    "rows_to_json",
    "parse_csv",
    "parse_json",
    "export_flight_log",
    "export_optimizer_result",
    "optimizer_result_columns",
    "optimizer_result_to_rows",
    "export_config",
    "validate_config_bundle",
    "normalise_bundle",
    "objective_directions",
    "unknown_objectives",
    "unknown_design_variables",
    "config_bundle_to_rows",
    "sanitise_artefact_name",
    "utc_stamp",
    "StorageManager",
    "STORAGE",
]

CSV_MIMETYPE = "text/csv"
JSON_MIMETYPE = "application/json"

#: On-disk suffixes -- the middle token keeps configs and optimiser results
#: distinguishable while both remain plain ``.json`` files.
CONFIG_SUFFIX = ".config.json"
RESULT_SUFFIX = ".result.json"
FLIGHT_SUFFIX = ".csv"

#: Sections of a configuration bundle and the dataclass that validates each.
CONFIG_SECTIONS: Tuple[Tuple[str, Any], ...] = (
    ("aircraft", AircraftConfig),
    ("environment", EnvironmentConfig),
    ("launch", LaunchConfig),
    ("simulation", SimulationSettings),
)

_KNOWN_SUFFIXES = (CONFIG_SUFFIX, RESULT_SUFFIX, FLIGHT_SUFFIX, ".json", ".csv", ".txt")

# Characters that must never reach a file name (Windows reserved set plus the
# POSIX separators and the NUL byte).
_ILLEGAL_CHARACTERS = frozenset('<>:"/\\|?*') | {"\x00"}

# Reserved device names on Windows (still worth rejecting everywhere so that a
# bundle exported on Linux can be unpacked on Windows).
_RESERVED_STEMS = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{index}" for index in range(1, 10)}
    | {f"LPT{index}" for index in range(1, 10)}
)

# Characters kept verbatim once the rejecting checks above have passed.
_SAFE_PUNCTUATION = frozenset("-_.()[]+")

_INT_PATTERN = re.compile(r"[+-]?\d+")
_FLOAT_PATTERN = re.compile(
    r"[+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?|[+-]?(?:inf(?:inity)?|nan)",
    re.IGNORECASE,
)
_WHITESPACE_PATTERN = re.compile(r"\s+")


def utc_stamp() -> str:
    """Filename-safe UTC timestamp, e.g. ``20260901T094514Z``."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _json_default(value: Any) -> str:
    """Last-resort encoder hook so numpy scalars / arrays never break export."""
    item = getattr(value, "item", None)
    if callable(item):          # numpy scalar -> python scalar
        return _json_default(item())
    to_list = getattr(value, "tolist", None)
    if callable(to_list):       # numpy array -> nested lists
        return _json_default(to_list())
    return str(value)


# --------------------------------------------------------------------------
# CSV primitives
# --------------------------------------------------------------------------
def _encode_value(value: Any) -> str:
    """Render one cell deterministically so :func:`parse_csv` can decode it."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        # repr() is the shortest string that round-trips a float64 exactly,
        # which is what makes save_flight_log -> load_flight_log bit-stable.
        return repr(value)
    if isinstance(value, (list, tuple, dict)):
        return json.dumps(as_dict(value), ensure_ascii=False, sort_keys=True)
    text = str(value)
    stripped = text.lstrip()
    if stripped and stripped[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + text
    return text


def _decode_value(text: str) -> Any:
    """Inverse of :func:`_encode_value` for the scalar column types.

    ``true``/``false`` -> ``bool``, ``-12`` -> ``int``, ``1.5e-3`` -> ``float``,
    anything else -> the untouched ``str``.  A *quoted* cell whose text happens
    to look numeric is decoded to the numeric type as well: that ambiguity is
    inherent to RFC 4180 (no type tags), so the flight-log loader converts back
    to the dataclass types with ``from_dict(FlightLogSample, ...)`` instead of
    relying on this best-effort inference.
    """
    if text.startswith("'"):
        stripped = text[1:].lstrip()
        if stripped and stripped[0] in ("=", "+", "-", "@", "\t", "\r"):
            text = text[1:]
    if text == "true":
        return True
    if text == "false":
        return False
    if text:
        if _INT_PATTERN.fullmatch(text):
            return int(text)
        if _FLOAT_PATTERN.fullmatch(text):
            return float(text)
    return text


def _resolve_columns(
    rows: Sequence[Mapping[str, Any]], columns: Optional[Sequence[str]] = None
) -> List[str]:
    """Explicit column list, or ``FLIGHT_LOG_COLUMNS`` order + sorted extras."""
    if columns is not None:
        resolved = [str(name) for name in columns]
        if len(set(resolved)) != len(resolved):
            raise ValueError(f"duplicate CSV column names: {resolved}")
        return resolved
    present: List[str] = []
    for row in rows:
        for key in row:
            name = str(key)
            if name not in present:
                present.append(name)
    if not present:
        return []
    ordered = [name for name in FLIGHT_LOG_COLUMNS if name in present]
    extras = sorted(name for name in present if name not in set(FLIGHT_LOG_COLUMNS))
    return ordered + extras


def rows_to_csv(rows: Sequence[Mapping[str, Any]], columns: Optional[Sequence[str]] = None) -> str:
    """Serialise ``rows`` to an RFC 4180 CSV string (CRLF, minimal quoting).

    ``columns`` pins the header order (used by the optimiser export, whose
    header is ``evaluation,score,<objectives>,<design variables>``); without it
    the header follows :data:`FLIGHT_LOG_COLUMNS` for the keys that occur.
    """
    materialised = [dict(row) for row in (rows or [])]
    header = _resolve_columns(materialised, columns)
    if not header:
        return ""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(header)
    for row in materialised:
        writer.writerow([_encode_value(row.get(name, "")) for name in header])
    return buffer.getvalue()


def parse_csv(text: str) -> List[Dict[str, Any]]:
    """Decode a CSV string produced by :func:`rows_to_csv`.

    Correctly handles quoted fields containing the delimiter, doubled embedded
    quotes and embedded ``CRLF`` (the text is fed to :mod:`csv` through a
    ``StringIO(newline="")`` so line endings are never translated), which makes
    ``parse_csv(rows_to_csv(rows)) == rows`` hold for every value the encoder
    can produce.
    """
    if text is None:
        return []
    payload = str(text)
    if not payload:
        return []
    reader = csv.reader(io.StringIO(payload, newline=""), delimiter=",", quotechar='"')
    records = [row for row in reader]
    if not records:
        return []
    header = [str(name) for name in records[0]]
    if not header:
        return []
    return [
        {name: _decode_value(values[index]) if index < len(values) else "" for index, name in enumerate(header)}
        for values in records[1:]
    ]


def _sanitize_json_leaves(value: Any) -> Any:
    """Recursively convert NaN/Inf floats into None (JSON null) to guarantee exportability."""
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return None
        return value
    if isinstance(value, Mapping):
        return {str(k): _sanitize_json_leaves(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize_json_leaves(item) for item in value]
    return value


def rows_to_json(rows: Sequence[Mapping[str, Any]], indent: Optional[int] = 2) -> str:
    """Serialise ``rows`` to JSON (UTF-8 friendly, never ASCII-escaped)."""
    data = as_dict(list(rows or []))
    data = _sanitize_json_leaves(data)
    return json.dumps(
        data, ensure_ascii=False, indent=indent, default=_json_default, allow_nan=False
    )


def parse_json(text: str) -> Dict[str, Any]:
    """Decode a JSON object; raises :class:`ValueError` on bad input."""
    if text is None or str(text).strip() == "":
        raise ValueError("cannot parse an empty JSON document")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON document: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"expected a JSON object, got {type(payload).__name__}")
    return payload


# --------------------------------------------------------------------------
# Flight log
# --------------------------------------------------------------------------
#: Resolve column types from defaults, including legacy metadata defaults.
_BOOL_COLUMNS = frozenset(
    name
    for name, spec in FlightLogSample.__dataclass_fields__.items()
    if isinstance(spec.default, bool)
)
_STRING_COLUMNS = frozenset(
    name for name, spec in FlightLogSample.__dataclass_fields__.items()
    if isinstance(spec.default, str)
)


def flight_log_to_rows(samples: Any) -> List[Dict[str, Any]]:
    """Normalise flight-log samples to row dicts keyed by ``FLIGHT_LOG_COLUMNS``.

    Accepts :class:`FlightLogSample` instances, plain mappings or any mixture of
    the two; missing columns fall back to the dataclass default so the key set
    of every returned row is exactly ``set(FLIGHT_LOG_COLUMNS)``.
    """
    rows: List[Dict[str, Any]] = []
    for sample in samples or []:
        if isinstance(sample, FlightLogSample):
            payload = as_dict(sample)
        elif isinstance(sample, Mapping):
            payload = {str(key): value for key, value in sample.items()}
        else:
            payload = getattr(sample, "__dict__", None)
            if payload is None:
                raise TypeError(
                    "flight log samples must be FlightLogSample instances or mappings, "
                    f"got {type(sample).__name__}"
                )
            payload = dict(payload)
        row: Dict[str, Any] = {}
        for column in FLIGHT_LOG_COLUMNS:
            if column in payload:
                row[column] = payload[column]
            else:
                row[column] = FlightLogSample.__dataclass_fields__[column].default
        rows.append(row)
    return rows


def sample_from_row(row: Mapping[str, Any], index: int = -1) -> FlightLogSample:
    """Rebuild a typed :class:`FlightLogSample` from one decoded CSV row.

    ``from_dict`` cannot be relied on for coercion here because the contract
    module uses PEP 563 annotations (``spec.type`` is the *string* ``"float"``),
    so the column types are applied explicitly from ``FLIGHT_LOG_COLUMNS``.
    An empty cell falls back to the dataclass default; a cell that is present
    but unparsable raises :class:`ValueError` naming the offending row/column
    instead of fabricating a number.
    """
    values: Dict[str, Any] = {}
    for column in FLIGHT_LOG_COLUMNS:
        default = FlightLogSample.__dataclass_fields__[column].default
        raw = row.get(column, "")
        if column in _STRING_COLUMNS:
            values[column] = default if raw is None or raw == "" else str(raw)
            continue
        if column in _BOOL_COLUMNS:
            values[column] = raw if isinstance(raw, bool) else bool(_decode_value(str(raw))) if str(raw) != "" else default
            continue
        if raw == "" or raw is None:
            values[column] = float(default) if default is not MISSING and default is not None else 0.0
            continue
        try:
            values[column] = float(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"flight log row {index} column '{column}' holds {raw!r}, which is not a number"
            ) from exc
    return FlightLogSample(**values)


def export_flight_log(samples: Any, fmt: str = "csv") -> Tuple[str, str, str]:
    """Return ``(payload, mimetype, filename)`` for a flight log.

    ``fmt`` is ``"csv"`` (``text/csv``) or ``"json"`` (``application/json``);
    anything else raises :class:`ValueError`.
    """
    rows = flight_log_to_rows(samples)
    style = str(fmt or "").strip().lower()
    stamp = utc_stamp()
    if style == "csv":
        return rows_to_csv(rows, columns=list(FLIGHT_LOG_COLUMNS)), CSV_MIMETYPE, f"flight_log_{stamp}.csv"
    if style == "json":
        return rows_to_json(rows), JSON_MIMETYPE, f"flight_log_{stamp}.json"
    raise ValueError(f"unsupported flight log format '{fmt}' (expected 'csv' or 'json')")


# --------------------------------------------------------------------------
# Optimiser results
# --------------------------------------------------------------------------
def _named_list(value: Any) -> List[str]:
    """Coerce an objective / design-variable declaration into a list of names."""
    names: List[str] = []
    if isinstance(value, Mapping):
        candidates: Iterable[Any] = value.keys()
    elif isinstance(value, (list, tuple, set)):
        candidates = value
    elif value is None:
        candidates = ()
    else:
        candidates = (value,)
    for item in candidates:
        if isinstance(item, Mapping):
            text = str(item.get("name", "")).strip()
        else:
            text = str(item).strip()
        if text and text not in names:
            names.append(text)
    return names


def objective_directions(names: Any) -> Dict[str, int]:
    """``{objective: +1 maximise / -1 minimise}`` for the AGENTS.md 8.3 catalogue.

    Names outside the catalogue are omitted -- the UI uses this to label the
    Pareto-front axes and the ``max_*`` / ``min_*`` badges.
    """
    return {
        name: OBJECTIVE_DIRECTIONS[name]
        for name in _named_list(names)
        if name in OBJECTIVE_DIRECTIONS
    }


def unknown_objectives(names: Any) -> List[str]:
    """Objective names that are *not* part of the AGENTS.md 8.3 catalogue."""
    return [name for name in _named_list(names) if name not in OBJECTIVE_DIRECTIONS]


def unknown_design_variables(names: Any) -> List[str]:
    """Design-variable names that are not in ``DESIGN_VARIABLE_CATALOGUE``."""
    catalogue = {variable.name for variable in DESIGN_VARIABLE_CATALOGUE}
    return [name for name in _named_list(names) if name not in catalogue]


def _objective_names(payload: Mapping[str, Any], history: Sequence[Any]) -> List[str]:
    declared = _named_list(payload.get("objectives"))
    if declared:
        return declared
    collected: List[str] = []
    for entry in history:
        if isinstance(entry, Mapping):
            values = entry.get("objectives")
            if isinstance(values, Mapping):
                for key in values:
                    name = str(key)
                    if name not in collected:
                        collected.append(name)
            elif isinstance(values, (list, tuple)) and values:
                for index in range(len(values)):
                    name = f"objective_{index}"
                    if name not in collected:
                        collected.append(name)
    if collected:
        return collected
    best = payload.get("best_objectives")
    if isinstance(best, Mapping):
        return [str(key) for key in best]
    return []


def _design_variable_names(payload: Mapping[str, Any], history: Sequence[Any]) -> List[str]:
    declared = _named_list(payload.get("design_variables"))
    if declared:
        return declared
    best = payload.get("best_params")
    if isinstance(best, Mapping):
        return [str(key) for key in best]
    for entry in history:
        if isinstance(entry, Mapping):
            for key in ("params", "variables", "design"):
                block = entry.get(key)
                if isinstance(block, Mapping):
                    return [str(name) for name in block]
    vector = payload.get("best_vector")
    if isinstance(vector, (list, tuple)) and vector:
        return [f"var_{index}" for index in range(len(vector))]
    return []


def optimizer_result_columns(result: Any) -> List[str]:
    """``["evaluation", "score", *objectives, *design_variables]``."""
    payload: Mapping[str, Any] = result if isinstance(result, Mapping) else {}
    history = payload.get("history")
    entries: Sequence[Any] = history if isinstance(history, (list, tuple)) else []
    return (
        ["evaluation", "score"]
        + _objective_names(payload, entries)
        + _design_variable_names(payload, entries)
    )


def _cell(entry: Any, name: str, index: int, blocks: Sequence[str]) -> Any:
    """Pull ``name`` out of one history entry (mapping, nested map or vector)."""
    if not isinstance(entry, Mapping):
        return ""
    for block_name in blocks:
        block = entry.get(block_name)
        if isinstance(block, Mapping) and name in block:
            return block[name]
        if isinstance(block, (list, tuple)) and index < len(block):
            return block[index]
    if name in entry:
        return entry[name]
    return ""


def optimizer_result_to_rows(
    result: Any, columns: Optional[Sequence[str]] = None
) -> List[Dict[str, Any]]:
    """Flatten an optimiser result into one row per history evaluation."""
    payload: Mapping[str, Any] = result if isinstance(result, Mapping) else {}
    history = payload.get("history")
    entries: Sequence[Any] = history if isinstance(history, (list, tuple)) else []
    header = list(columns) if columns is not None else optimizer_result_columns(payload)
    objectives = header[2 : 2 + len(_objective_names(payload, entries))]
    variables = header[2 + len(objectives) :]
    rows: List[Dict[str, Any]] = []
    for index, entry in enumerate(entries):
        evaluation: Any = index
        score: Any = ""
        if isinstance(entry, Mapping):
            for key in ("evaluation", "eval", "index"):
                if key in entry:
                    evaluation = entry[key]
                    break
            for key in ("score", "cost", "fitness", "objective_value"):
                if key in entry:
                    score = entry[key]
                    break
        row: Dict[str, Any] = {"evaluation": evaluation, "score": score}
        for offset, name in enumerate(objectives):
            row[name] = _cell(entry, name, offset, ("objectives", "fitness", "scores"))
        for offset, name in enumerate(variables):
            row[name] = _cell(entry, name, offset, ("params", "vector", "variables", "design"))
        rows.append(row)
    return rows


def export_optimizer_result(result: Any, fmt: str = "csv") -> Tuple[str, str, str]:
    """Return ``(payload, mimetype, filename)`` for an optimiser result."""
    payload: Mapping[str, Any] = result if isinstance(result, Mapping) else {}
    style = str(fmt or "").strip().lower()
    stamp = utc_stamp()
    if style == "csv":
        columns = optimizer_result_columns(payload)
        rows = optimizer_result_to_rows(payload, columns=columns)
        return rows_to_csv(rows, columns=columns), CSV_MIMETYPE, f"optimizer_{stamp}.csv"
    if style == "json":
        data = as_dict(dict(payload))
        data = _sanitize_json_leaves(data)
        return (
            json.dumps(data, ensure_ascii=False, indent=2, default=_json_default, allow_nan=False),
            JSON_MIMETYPE,
            f"optimizer_{stamp}.json",
        )
    raise ValueError(f"unsupported optimiser result format '{fmt}' (expected 'csv' or 'json')")


# --------------------------------------------------------------------------
# Configuration bundles
# --------------------------------------------------------------------------
def normalise_bundle(bundle: Any) -> Dict[str, Any]:
    """Fill missing sections from :func:`default_config_bundle`.

    Sections that *are* present are copied verbatim -- including an invalid one
    -- so :func:`validate_config_bundle` can report the offending section
    instead of silently replacing user data with defaults.
    """
    if not isinstance(bundle, Mapping):
        raise ValueError(
            f"configuration bundle must be a mapping, got {type(bundle).__name__}"
        )
    normalised: Dict[str, Any] = {}
    for key, value in bundle.items():
        name = str(key)
        normalised[name] = dict(value) if isinstance(value, Mapping) else value
    for section, default in default_config_bundle().items():
        if section not in normalised:
            normalised[section] = dict(default)
    return normalised


def _assert_json_finite(path: str, value: Any) -> None:
    """Reject NaN/Inf and non JSON leaves anywhere in a section."""
    if isinstance(value, bool) or value is None or isinstance(value, (str, int)):
        return
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError(f"{path} must be a finite number, got {value!r}")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            _assert_json_finite(f"{path}.{key}", item)
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _assert_json_finite(f"{path}[{index}]", item)
        return
    raise ValueError(
        f"{path} must be a JSON compatible value, got {type(value).__name__}"
    )


def _validate_optimizer_section(payload: Mapping[str, Any]) -> None:
    """Check the optional ``optimizer`` section against the 8.3 catalogue.

    ``default_config_bundle()`` ships without this section, so a preset that
    declares one must name real objectives and real design variables -- an
    unknown name would be silently dropped by ``OptimizerConfig
    .active_objectives()`` and the UI checkbox would do nothing.
    """
    _assert_json_finite("optimizer", payload)
    objectives = payload.get("objectives")
    if objectives is not None:
        if not isinstance(objectives, (list, tuple)):
            raise ValueError(
                f"optimizer.objectives must be a list of names, got {type(objectives).__name__}"
            )
        unknown = unknown_objectives(objectives)
        if unknown:
            raise ValueError(
                f"optimizer.objectives contains unknown objective name(s) {unknown}; "
                f"expected a subset of {sorted(OBJECTIVE_DIRECTIONS)}"
            )
    variables = payload.get("design_variables")
    if variables is not None:
        if not isinstance(variables, (list, tuple)):
            raise ValueError(
                f"optimizer.design_variables must be a list of names, got {type(variables).__name__}"
            )
        unknown = unknown_design_variables(variables)
        if unknown:
            raise ValueError(
                f"optimizer.design_variables contains unknown design variable name(s) "
                f"{unknown}; expected a subset of "
                f"{sorted(item.name for item in DESIGN_VARIABLE_CATALOGUE)}"
            )


def validate_config_bundle(bundle: Any) -> Dict[str, Any]:
    """Validate a bundle against the contract dataclasses.

    Raises :class:`ValueError` when the bundle is not a mapping, when a section
    is not a mapping itself, when a leaf is not a finite JSON value, when a
    section carries keys the dataclass does not declare, or when an optional
    ``optimizer`` section names an objective / design variable outside the
    AGENTS.md 8.3 catalogue.  Returns the normalised bundle (every section
    coerced to plain JSON types).
    """
    normalised = normalise_bundle(bundle)
    validated: Dict[str, Any] = {}
    for section, dataclass_type in CONFIG_SECTIONS:
        payload = normalised.get(section)
        if not isinstance(payload, Mapping):
            raise ValueError(
                f"configuration section '{section}' must be a mapping, "
                f"got {type(payload).__name__}"
            )
        _assert_json_finite(section, payload)
        validated[section] = as_dict(
            from_dict(dataclass_type, dict(payload), strict=True)
        )
    for key, value in normalised.items():
        if key in validated:
            continue
        if key == "optimizer" and isinstance(value, Mapping):
            _validate_optimizer_section(value)
        validated[key] = value
    return validated


def config_bundle_to_rows(bundle: Any) -> List[Dict[str, Any]]:
    """Flatten a bundle to ``section``/``parameter``/``value`` rows for CSV."""
    normalised = normalise_bundle(bundle)
    rows: List[Dict[str, Any]] = []
    for section in [name for name, _ in CONFIG_SECTIONS] + [
        key for key in normalised if key not in dict(CONFIG_SECTIONS)
    ]:
        payload = normalised.get(section)
        if not isinstance(payload, Mapping):
            rows.append({"section": section, "parameter": "", "value": payload})
            continue
        for key, value in payload.items():
            rows.append(
                {
                    "section": section,
                    "parameter": str(key),
                    "value": _encode_value(value),
                }
            )
    return rows


def export_config(bundle: Any, fmt: str = "json") -> Tuple[str, str, str]:
    """Return ``(payload, mimetype, filename)`` for a configuration bundle."""
    style = str(fmt or "").strip().lower()
    stamp = utc_stamp()
    if style == "json":
        return (
            json.dumps(validate_config_bundle(bundle), ensure_ascii=False, indent=2),
            JSON_MIMETYPE,
            f"config_{stamp}.json",
        )
    if style == "csv":
        rows = config_bundle_to_rows(bundle)
        return (
            rows_to_csv(rows, columns=["section", "parameter", "value"]),
            CSV_MIMETYPE,
            f"config_{stamp}.csv",
        )
    raise ValueError(f"unsupported config format '{fmt}' (expected 'csv' or 'json')")


# --------------------------------------------------------------------------
# Artefact naming (path traversal policy)
# --------------------------------------------------------------------------
def sanitise_artefact_name(name: Any, kind: str = "artefact") -> str:
    """Validate and normalise an artefact file stem.

    **Policy: reject, do not silently repair.**  Anything that could escape the
    storage root raises :class:`ValueError`:

    * empty / whitespace-only names;
    * any of ``< > : " / \\ | ? *`` or a NUL byte (Windows reserved set + POSIX
      separators), so no sub-directory, drive letter or ADS stream can appear;
    * any ``..`` sequence, or a name whose final path component differs from the
      name itself;
    * absolute paths (``C:\\...``, ``/etc/passwd``) and names starting with a
      dot (hidden/system files);
    * names ending in ``.`` or a space, and the Windows device names
      (``CON``, ``NUL``, ``COM1`` ... ``LPT9``).

    Names that are merely untidy are normalised: whitespace runs collapse to a
      single ``_``, characters outside ``[alnum] + ``-_.()[]+`` become ``_``,
    and the stem is truncated to 96 characters.  Unicode letters (Japanese
    preset names) survive because :meth:`str.isalnum` accepts them.
    """
    raw = "" if name is None else str(name)
    candidate = raw.strip()
    if not candidate:
        raise ValueError(f"{kind} name must be a non-empty string")
    offending = sorted({character for character in candidate if character in _ILLEGAL_CHARACTERS})
    if offending:
        raise ValueError(
            f"{kind} name {raw!r} contains illegal character(s) "
            f"{''.join(offending)!r}; path separators, drive letters and "
            "wildcards are rejected"
        )
    if ".." in candidate:
        raise ValueError(f"{kind} name {raw!r} contains a parent-directory reference '..'")
    if os.path.isabs(candidate) or candidate != Path(candidate).name:
        raise ValueError(f"{kind} name {raw!r} is not a bare file name")
    if candidate.startswith("."):
        raise ValueError(f"{kind} name {raw!r} must not start with a dot")
    if candidate.endswith(".") or candidate.endswith(" "):
        raise ValueError(f"{kind} name {raw!r} must not end with '.' or a space")
    if candidate.split(".")[0].upper() in _RESERVED_STEMS:
        raise ValueError(f"{kind} name {raw!r} is a reserved device name")
    safe = _WHITESPACE_PATTERN.sub("_", candidate)
    safe = "".join(
        character if (character.isalnum() or character in _SAFE_PUNCTUATION) else "_"
        for character in safe
    )
    if len(safe) > 96:
        safe = safe[:96].rstrip("._-")
    if not safe or safe in {".", ".."}:
        raise ValueError(f"{kind} name {raw!r} does not contain any usable character")
    return safe


def _strip_suffix(name: str) -> str:
    for suffix in _KNOWN_SUFFIXES:
        if name.lower().endswith(suffix):
            return name[: -len(suffix)]
    return name


class StorageManager:
    """Thread-safe JSON/CSV artefact store rooted at a single directory."""

    def __init__(self, root: str = "runs") -> None:
        self._lock = threading.RLock()
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    # -- paths -------------------------------------------------------------
    @property
    def root(self) -> str:
        """Absolute path of the storage root (created in ``__init__``)."""
        return str(self._root.resolve())

    def _stem(self, name: Any, kind: str) -> str:
        raw = "" if name is None else str(name).strip()
        return sanitise_artefact_name(_strip_suffix(raw), kind=kind)

    def _path_for(self, name: Any, suffix: str, kind: str = "artefact") -> Path:
        stem = self._stem(name, kind)
        candidate = self._root / f"{stem}{suffix}"
        resolved_root = self._root.resolve()
        resolved = candidate.resolve()
        if resolved.parent != resolved_root:
            raise ValueError(f"{kind} {name!r} would escape the storage root")
        return candidate

    @staticmethod
    def _write_text(path: Path, text: str) -> Path:
        # newline="" keeps the CRLF terminators of csv.writer byte-exact on
        # Windows; utf-8 without BOM keeps the JSON round-trip lossless.
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        return path

    @staticmethod
    def _read_text(path: Path) -> str:
        with open(path, "r", encoding="utf-8", newline="") as handle:
            return handle.read()

    # -- configurations ----------------------------------------------------
    def save_config(self, bundle: Any, name: str = "preset") -> str:
        """Validate and persist a configuration bundle as ``<name>.config.json``."""
        validated = validate_config_bundle(bundle)
        with self._lock:
            path = self._path_for(name, CONFIG_SUFFIX, kind="configuration name")
            self._write_text(path, json.dumps(validated, ensure_ascii=False, indent=2) + "\n")
            return str(path)

    def load_config(self, name: str) -> Dict[str, Any]:
        """Read a configuration bundle back; :class:`KeyError` when absent."""
        with self._lock:
            path = self._path_for(name, CONFIG_SUFFIX, kind="configuration name")
            if not path.is_file():
                raise KeyError(f"no configuration named '{name}' under {self.root}")
            return validate_config_bundle(parse_json(self._read_text(path)))

    def list_configs(self) -> List[Dict[str, Any]]:
        """Metadata for every stored configuration, sorted by name."""
        with self._lock:
            return [
                self._describe(path)
                for path in sorted(self._root.glob(f"*{CONFIG_SUFFIX}"), key=lambda item: item.name)
            ]

    def delete_config(self, name: str) -> bool:
        """Delete a stored configuration; ``False`` when it did not exist."""
        with self._lock:
            path = self._path_for(name, CONFIG_SUFFIX, kind="configuration name")
            if not path.is_file():
                return False
            path.unlink()
            return True

    # -- flight logs -------------------------------------------------------
    def save_flight_log(self, samples: Any, name: str = "flight") -> str:
        """Persist a flight log as CSV (exact float round-trip)."""
        rows = flight_log_to_rows(samples)
        text = rows_to_csv(rows, columns=list(FLIGHT_LOG_COLUMNS))
        with self._lock:
            path = self._path_for(name, FLIGHT_SUFFIX, kind="flight log name")
            self._write_text(path, text)
            return str(path)

    def load_flight_log(self, name: str) -> List[FlightLogSample]:
        """Read a flight log back into typed :class:`FlightLogSample` objects."""
        with self._lock:
            path = self._path_for(name, FLIGHT_SUFFIX, kind="flight log name")
            if not path.is_file():
                raise KeyError(f"no flight log named '{name}' under {self.root}")
            rows = parse_csv(self._read_text(path))
            return [sample_from_row(row, index) for index, row in enumerate(rows)]

    # -- optimiser results -------------------------------------------------
    def save_optimizer_result(self, result: Any, name: str = "optimization") -> str:
        """Persist an optimiser result as ``<name>.result.json``."""
        if result is not None and not isinstance(result, Mapping):
            raise ValueError(
                f"optimiser result must be a mapping, got {type(result).__name__}"
            )
        with self._lock:
            path = self._path_for(name, RESULT_SUFFIX, kind="optimiser result name")
            self._write_text(
                path,
                json.dumps(
                    as_dict(dict(result or {})), ensure_ascii=False, indent=2, default=_json_default
                )
                + "\n",
            )
            return str(path)

    def load_optimizer_result(self, name: str) -> Dict[str, Any]:
        """Read an optimiser result back; :class:`KeyError` when absent."""
        with self._lock:
            path = self._path_for(name, RESULT_SUFFIX, kind="optimiser result name")
            if not path.is_file():
                raise KeyError(f"no optimiser result named '{name}' under {self.root}")
            return parse_json(self._read_text(path))

    def delete(self, name: str) -> bool:
        """Delete any artefact with the given stem, whatever its suffix."""
        removed = False
        with self._lock:
            stem = self._stem(name, kind="artefact name")
            for suffix in (CONFIG_SUFFIX, RESULT_SUFFIX, FLIGHT_SUFFIX):
                candidate = self._root / f"{stem}{suffix}"
                if candidate.is_file():
                    candidate.unlink()
                    removed = True
            return removed

    def list_artefacts(self) -> List[Dict[str, Any]]:
        """Metadata for every file in the root, sorted by name."""
        with self._lock:
            return [
                self._describe(path)
                for path in sorted(self._root.iterdir(), key=lambda item: item.name)
                if path.is_file()
            ]

    def _describe(self, path: Path) -> Dict[str, Any]:
        name = path.name
        if name.endswith(CONFIG_SUFFIX):
            kind = "config"
        elif name.endswith(RESULT_SUFFIX):
            kind = "optimizer_result"
        elif name.endswith(FLIGHT_SUFFIX):
            kind = "flight_log"
        else:
            kind = "other"
        stat = path.stat()
        return {
            "name": name,
            "stem": _strip_suffix(name),
            "path": str(path),
            "kind": kind,
            "format": kind,
            "size_bytes": int(stat.st_size),
            "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"),
        }


#: Process wide store used by the Flask layer (``runs/`` below the CWD).
STORAGE = StorageManager()
