"""The one engine. It reads a mapping config and emits canonical observations.

There are six configs and one engine, not six scripts. The engine knows nothing
about any particular source. Everything source-specific lives in the config.

It makes no model calls at all. That is what lets us report a per-record cost
of zero: the money is spent once, when the agent writes the config, and never
again per record.

The same code runs twice:

  in C4, over rows the profile was not built from, to find what a draft config
  gets wrong
  in stage D, over a full sample, to produce the deliverable

so what the agent was checked against is exactly what ships.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone

from .transforms import REGISTRY, apply_chain

ENGINE_VERSION = "0.1"


class FieldFailure(Exception):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------
# one field
# --------------------------------------------------------------------------

def apply_mapping(record: dict, mapping: dict) -> tuple:
    """Return (value, problem). problem is None when nothing went wrong.

    A problem is not an exception. A failed ABN checksum is a normal thing for
    a real file to contain, and we want it counted rather than raised.
    """
    source_field = mapping.get("source_field") or ""
    chain = mapping.get("transforms") or []

    multi_field_start = chain and chain[0]["op"] in ("concat", "coalesce", "constant")
    if source_field and not multi_field_start and source_field not in record:
        return None, {"problem": "source column is not in the file",
                      "detail": source_field}

    raw = record.get(source_field) if source_field else None
    had_input = bool(str(raw).strip()) if raw is not None else False

    for step in chain:
        if step["op"] not in REGISTRY:
            return None, {"problem": "unknown transform", "detail": step["op"]}

    try:
        value = apply_chain(raw, chain, record)
    except Exception as err:
        return None, {"problem": "transform raised",
                      "detail": f"{type(err).__name__}: {err}",
                      "value": str(raw)[:60]}

    # The important signal. The source had something and we produced nothing,
    # which usually means a failed checksum or a date format that did not
    # match. Silence here is how a bad mapping hides.
    #
    # Unless the config says so on purpose. A null_if that names this exact
    # value is a recorded decision about a publisher's placeholder, not an
    # accident, so it is reported separately and does not count as a failure.
    if value is None and had_input:
        declared = {str(v).strip().lower()
                    for step in chain if step["op"] == "null_if"
                    for v in step.get("values", [])}
        if str(raw).strip().lower() in declared:
            return None, None
        return None, {"problem": "dropped a value the source had",
                      "value": str(raw)[:60]}

    # map_values falling through to its default is not a failure, but it is
    # worth counting: a high rate means the config's enum is incomplete.
    last = chain[-1] if chain else {}
    if last.get("op") == "map_values" and had_input \
            and value == last.get("default", "unknown"):
        return value, {"problem": "value not in the enum, fell back to default",
                       "value": str(raw)[:60]}

    return value, None


# --------------------------------------------------------------------------
# one record
# --------------------------------------------------------------------------

def build_record_id(record: dict, spec: dict, row_number: int) -> str:
    """A stable id that traces back to the exact raw record."""
    strategy = spec.get("strategy", "row_number")
    fields = spec.get("fields") or []

    if strategy == "source_field" and fields:
        value = str(record.get(fields[0], "")).strip()
        if value:
            return value
        # Fall through rather than emitting an empty id.
    if strategy == "hash_of_fields" and fields:
        joined = "|".join(str(record.get(f, "")).strip() for f in fields)
        if joined.strip("|"):
            return hashlib.sha1(joined.encode()).hexdigest()[:16]
    return f"row{row_number}"


def _observed_at(record: dict, spec: dict, fallback: str) -> str:
    field = (spec or {}).get("from_field") or ""
    if field and record.get(field):
        parsed = apply_chain(record[field],
                             [{"op": "parse_datetime", "formats": spec.get("formats", [])}])
        if parsed:
            return parsed
        parsed = apply_chain(record[field],
                             [{"op": "parse_date", "formats": spec.get("formats", [])}])
        if parsed:
            return parsed
    return fallback


def _date_from(record: dict, spec: dict) -> str | None:
    """A date the source states, or nothing. Never a guess."""
    field = (spec or {}).get("from_field") or ""
    if not field or not record.get(field):
        return None
    return apply_chain(record[field],
                       [{"op": "parse_date", "formats": spec.get("formats", [])}])


def extract_one(record: dict, cfg: dict, row_number: int,
                ingested_at: str) -> tuple:
    """Turn one raw record into one canonical observation."""
    problems = []
    entity, address, field_confidence = {}, {}, {}

    for mapping in cfg.get("field_mappings", []):
        value, problem = apply_mapping(record, mapping)
        if problem:
            problems.append({**problem,
                             "canonical_field": mapping["canonical_field"],
                             "source_field": mapping.get("source_field", "")})
        if value is None or value == "":
            continue
        section, name = mapping["canonical_field"].split(".", 1)
        (entity if section == "entity" else address)[name] = value
        field_confidence[mapping["canonical_field"]] = mapping["field_confidence"]

    observation = {
        # The envelope. The ontology says every record carries it, no exceptions.
        "source_id": cfg["source_id"],
        "source_record_id": build_record_id(record, cfg.get("record_id", {}),
                                            row_number),
        "observed_at": _observed_at(record, cfg.get("observation", {}).get("observed_at"),
                                    cfg.get("observation", {}).get(
                                        "observed_at", {}).get("fallback_constant")
                                    or ingested_at),
        "ingested_at": ingested_at,
        "licence": cfg.get("observation", {}).get("licence", "unknown"),
        "extractor_version": f"engine{ENGINE_VERSION}/config{cfg.get('config_version','?')}",
        # Three kinds of confidence, kept apart. link_confidence appears in
        # part 3 and belongs on links, not here.
        "confidence": {
            "source_reliability": cfg.get("observation", {}).get("source_reliability"),
            "field_confidence": field_confidence,
        },
    }
    # When the claim became true, and stopped being true, in the world. The
    # ontology says a null valid_to means still current as far as this source
    # knows, so we leave it out rather than writing null.
    observation_config = cfg.get("observation", {})
    valid_from = _date_from(record, observation_config.get("valid_from"))
    valid_to = _date_from(record, observation_config.get("valid_to"))
    if valid_from:
        observation["valid_from"] = valid_from
    if valid_to:
        observation["valid_to"] = valid_to

    # Fields we could not fill are absent, not blank and not guessed.
    if entity:
        observation["entity"] = entity
    if address:
        observation["address"] = address

    return observation, problems


# --------------------------------------------------------------------------
# a whole file
# --------------------------------------------------------------------------

def run(cfg: dict, records: list[dict]) -> dict:
    """Extract every record and report what went wrong, field by field."""
    ingested_at = _now()
    observations, all_problems = [], []

    for row_number, record in enumerate(records):
        try:
            observation, problems = extract_one(record, cfg, row_number, ingested_at)
        except Exception as err:
            all_problems.append({"canonical_field": "(whole record)",
                                 "problem": "record raised",
                                 "detail": f"{type(err).__name__}: {err}",
                                 "source_field": ""})
            continue
        observations.append(observation)
        all_problems.extend(problems)

    return {
        "observations": observations,
        "failures": summarise(all_problems, len(records)),
        "coverage": coverage(observations, cfg, len(records)),
        "rows_in": len(records),
        "rows_out": len(observations),
    }


def summarise(problems: list[dict], rows: int) -> list[dict]:
    """Group problems so the model gets a short report, not 10,000 lines."""
    grouped: dict[tuple, dict] = {}
    for problem in problems:
        key = (problem["canonical_field"], problem["problem"])
        entry = grouped.setdefault(key, {
            "canonical_field": problem["canonical_field"],
            "source_field": problem.get("source_field", ""),
            "problem": problem["problem"],
            "count": 0,
            "examples": [],
        })
        entry["count"] += 1
        example = problem.get("value") or problem.get("detail")
        if example and len(entry["examples"]) < 6 and example not in entry["examples"]:
            entry["examples"].append(example)

    report = sorted(grouped.values(), key=lambda e: -e["count"])
    for entry in report:
        entry["share_of_rows"] = round(entry["count"] / (rows or 1), 3)
    return report


def duplicate_fields(cfg: dict) -> list[dict]:
    """Two mappings writing the same canonical field.

    The engine applies mappings in order, so the last one with a value wins.
    That happens to give the right answer sometimes, which is worse than
    getting it wrong: the behaviour is invisible and nobody chose it. A config
    that wants "this column, or that one if it is empty" must say so with
    coalesce.
    """
    seen: dict[str, list[str]] = {}
    for mapping in cfg.get("field_mappings", []):
        seen.setdefault(mapping["canonical_field"], []).append(
            mapping.get("source_field", "(built)"))
    return [{"canonical_field": field, "source_fields": fields}
            for field, fields in seen.items() if len(fields) > 1]


def coverage(observations: list[dict], cfg: dict, rows: int) -> list[dict]:
    """How often each mapped field actually produced a value."""
    filled = Counter()
    for observation in observations:
        for section in ("entity", "address"):
            for name in observation.get(section, {}):
                filled[f"{section}.{name}"] += 1

    return [{
        "canonical_field": mapping["canonical_field"],
        "source_field": mapping.get("source_field", ""),
        "filled": filled.get(mapping["canonical_field"], 0),
        "of_rows": rows,
        "fill_rate": round(filled.get(mapping["canonical_field"], 0) / (rows or 1), 3),
        "claimed_confidence": mapping["field_confidence"],
    } for mapping in cfg.get("field_mappings", [])]
