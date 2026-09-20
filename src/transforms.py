"""The transforms a mapping config is allowed to use.

The agent picks ops from config/transforms.yaml by name. It never writes code,
so the engine never runs eval() on model output.

Every op here takes the value first and its arguments as keywords, and returns
either a value or None. None means "this field has no value for this record",
which is different from an empty string and is what the ontology asks for:
fields we could not fill are absent, not blank.
"""

from __future__ import annotations

import re
from datetime import datetime

# --------------------------------------------------------------------------
# checksums
# --------------------------------------------------------------------------

_ABN_WEIGHTS = [10, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19]
_ACN_WEIGHTS = [8, 7, 6, 5, 4, 3, 2, 1]


def abn_is_valid(digits: str) -> bool:
    """The published ABN check: subtract 1 from the first digit, then mod 89."""
    if len(digits) != 11 or not digits.isdigit():
        return False
    numbers = [int(d) for d in digits]
    numbers[0] -= 1
    return sum(n * w for n, w in zip(numbers, _ABN_WEIGHTS)) % 89 == 0


def acn_is_valid(digits: str) -> bool:
    """The published ACN check: weighted sum of the first 8, mod 10."""
    if len(digits) != 9 or not digits.isdigit():
        return False
    total = sum(int(d) * w for d, w in zip(digits[:8], _ACN_WEIGHTS))
    return (10 - total % 10) % 10 == int(digits[8])


# --------------------------------------------------------------------------
# ops
# --------------------------------------------------------------------------

def _text(value) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text.strip() else None


def op_strip(value, **_):
    text = _text(value)
    return text.strip() if text else None


def op_collapse_spaces(value, **_):
    text = _text(value)
    return re.sub(r"\s+", " ", text).strip() if text else None


def op_upper(value, **_):
    text = _text(value)
    return text.upper() if text else None


def op_lower(value, **_):
    text = _text(value)
    return text.lower() if text else None


def op_title_case(value, **_):
    text = _text(value)
    return text.title() if text else None


def op_null_if(value, values=(), **_):
    text = _text(value)
    if text is None:
        return None
    return None if text.strip().lower() in {str(v).lower() for v in values} else text


def op_regex_extract(value, pattern="", group=1, **_):
    text = _text(value)
    if not text:
        return None
    found = re.search(pattern, text)
    if not found:
        return None
    try:
        return found.group(group)
    except IndexError:
        return None


def op_split_take(value, separator=",", index=0, **_):
    text = _text(value)
    if not text:
        return None
    pieces = text.split(separator)
    try:
        return pieces[index].strip() or None
    except IndexError:
        return None


def op_digits_only(value, **_):
    text = _text(value)
    if not text:
        return None
    digits = re.sub(r"\D", "", text)
    return digits or None


def op_constant(value, value_to_use=None, **_):
    return value_to_use


def op_abn_normalize(value, **_):
    """Eleven digits with a valid checksum, or nothing. Never a guess."""
    digits = op_digits_only(value)
    return digits if digits and abn_is_valid(digits) else None


def op_acn_normalize(value, **_):
    digits = op_digits_only(value)
    return digits if digits and acn_is_valid(digits) else None


def op_nzbn_normalize(value, **_):
    digits = op_digits_only(value)
    return digits if digits and len(digits) == 13 else None


def op_map_values(value, mapping=None, default="unknown", **_):
    text = _text(value)
    if text is None:
        return default
    lookup = {str(k).strip().lower(): v for k, v in (mapping or {}).items()}
    return lookup.get(text.strip().lower(), default)


_STATES = {
    "nsw": "NSW", "new south wales": "NSW",
    "vic": "VIC", "victoria": "VIC",
    "qld": "QLD", "queensland": "QLD",
    "wa": "WA", "western australia": "WA",
    "sa": "SA", "south australia": "SA",
    "tas": "TAS", "tasmania": "TAS",
    "act": "ACT", "australian capital territory": "ACT",
    "nt": "NT", "northern territory": "NT",
    "nz": "NZ", "new zealand": "NZ",
}


def op_state_normalize(value, **_):
    text = _text(value)
    if not text:
        return None
    return _STATES.get(text.strip().lower(), "OTHER")


def op_postcode_extract(value, **_):
    text = _text(value)
    if not text:
        return None
    found = re.search(r"\b(\d{4})\b", text)
    return found.group(1) if found else None


def op_parse_date(value, formats=(), **_):
    text = _text(value)
    if not text:
        return None
    for fmt in formats:
        try:
            return datetime.strptime(text.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return None


def op_parse_datetime(value, formats=(), **_):
    text = _text(value)
    if not text:
        return None
    for fmt in formats:
        try:
            return datetime.strptime(text.strip(), fmt).isoformat()
        except ValueError:
            continue
    return None


# Ops that need more than one source field are applied by the engine, which
# hands them a dict of the record rather than one value.
def op_concat(record: dict, fields=(), separator=" ", **_):
    pieces = [str(record.get(f, "")).strip() for f in fields]
    joined = separator.join(p for p in pieces if p)
    return joined or None


def op_coalesce(record: dict, fields=(), **_):
    for field in fields:
        text = _text(record.get(field))
        if text and text.strip():
            return text.strip()
    return None


MULTI_FIELD_OPS = {"concat", "coalesce"}

REGISTRY = {name[3:]: func for name, func in list(globals().items())
            if name.startswith("op_")}


def apply_chain(value, chain: list[dict], record: dict | None = None):
    """Run a config's list of ops in order. Unknown op names are rejected."""
    result = value
    for step in chain:
        op = step.get("op")
        if op not in REGISTRY:
            raise ValueError(f"unknown transform '{op}'. "
                             f"Allowed: {sorted(REGISTRY)}")
        args = {k: v for k, v in step.items() if k != "op"}
        if op in MULTI_FIELD_OPS:
            result = REGISTRY[op](record or {}, **args)
        else:
            result = REGISTRY[op](result, **args)
        if result is None:
            return None
    return result
