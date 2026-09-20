"""Step C3: the model reads the profile and proposes a mapping config.

This is the first model call in Part 2. It is given:

  - the canonical ontology, as field names with their notes and allowed values
  - the closed list of transforms it may use
  - what C1 found out about the file
  - what C2 found out about every column

It never sees the raw file. It sees the profile, which is about thirty times
smaller and says more: null rates, distinct counts, complete enums, and whether
a column passes the ABN or ACN checksum.

It returns a draft mapping config. Nothing is trusted yet. C4 runs it over rows
the sample did not come from and counts what breaks.
"""

from __future__ import annotations

import json

import yaml

from .. import config, llm

ONTOLOGY_FILE = config.ROOT / "firmable_ontology.yaml"
TRANSFORMS_FILE = config.ROOT / "config" / "transforms.yaml"

CANONICAL_FIELDS = [
    "entity.legal_name", "entity.trading_name", "entity.abn", "entity.acn",
    "entity.nzbn", "entity.entity_type", "entity.status", "entity.website",
    "entity.industry_code", "entity.date_registered",
    "address.full", "address.locality", "address.state", "address.postcode",
    "address.country",
]

SCHEMA = {
    "type": "object",
    "properties": {
        "record_id": {
            "type": "object",
            "properties": {
                "strategy": {"type": "string",
                             "enum": ["source_field", "hash_of_fields", "row_number"]},
                "fields": {"type": "array", "items": {"type": "string"}},
                "note": {"type": "string"},
            },
            "required": ["strategy", "fields", "note"],
        },
        "observed_at": {
            "type": "object",
            "properties": {
                "from_field": {"type": "string"},
                "formats": {"type": "array", "items": {"type": "string"}},
                "note": {"type": "string"},
            },
            "required": ["from_field", "formats", "note"],
        },
        "valid_from": {
            "type": "object",
            "properties": {
                "from_field": {"type": "string"},
                "formats": {"type": "array", "items": {"type": "string"}},
                "note": {"type": "string"},
            },
            "required": ["from_field", "formats", "note"],
        },
        "valid_to": {
            "type": "object",
            "properties": {
                "from_field": {"type": "string"},
                "formats": {"type": "array", "items": {"type": "string"}},
                "note": {"type": "string"},
            },
            "required": ["from_field", "formats", "note"],
        },
        "source_reliability": {"type": "number"},
        "field_mappings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "canonical_field": {"type": "string", "enum": CANONICAL_FIELDS},
                    "source_field": {"type": "string"},
                    "transforms": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "op": {"type": "string"},
                                "args_json": {
                                    "type": "string",
                                    "description": "Arguments as a JSON object string, e.g. {\"formats\": [\"%d/%m/%Y\"]}. Empty string when the op takes none.",
                                },
                            },
                            "required": ["op", "args_json"],
                        },
                    },
                    "field_confidence": {"type": "number"},
                    "note": {"type": "string"},
                },
                "required": ["canonical_field", "source_field", "transforms",
                             "field_confidence", "note"],
            },
        },
        "unmapped_source_fields": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source_field": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["source_field", "reason"],
            },
        },
        "unfilled_canonical_fields": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "canonical_field": {"type": "string", "enum": CANONICAL_FIELDS},
                    "reason": {"type": "string"},
                },
                "required": ["canonical_field", "reason"],
            },
        },
    },
    "required": ["record_id", "observed_at", "valid_from", "valid_to",
                 "source_reliability",
                 "field_mappings", "unmapped_source_fields",
                 "unfilled_canonical_fields"],
}

PROMPT = """You map a government dataset onto a canonical business ontology.

You are given a profile of the file, not the file itself. Every number in it
was computed from {rows} real rows. Example values are real values.

# THE ONTOLOGY you are mapping onto

{ontology}

# THE TRANSFORMS you may use

You pick ops from this list by name. You never write code. An op not on this
list is rejected before it runs.

{transforms}

# THE FILE

{shape}

# THE COLUMNS

{columns}

# WHAT TO RETURN

record_id
  How to identify one raw record. Prefer strategy "source_field" with one
  column that is unique and never empty. If none exists use "hash_of_fields"
  and list the columns to hash. Explain the choice in `note`.

observed_at
  When the source STATED these facts. Pick a date column if one exists and give
  its strptime formats. If none exists use an empty from_field and say so. We
  fall back to the dataset's own last-modified date, so an empty answer here is
  fine.

valid_from
  When the claim became TRUE IN THE WORLD, if the source says. This is not the
  same as observed_at. For a company register it is usually the registration
  date. For a licence it is the licence start date. For a contract it is the
  contract start date. Empty from_field if the source does not say.

valid_to
  When the claim STOPPED being true, if the source says. A deregistration date,
  a licence expiry, a contract end date. Null means still current as far as
  this source knows, so an empty from_field is a normal answer.

  These three are different questions and a source often answers only one of
  them. Do not reuse the same column for two of them unless it genuinely means
  both.

source_reliability
  0 to 1. How much a company database should trust this publisher on this kind
  of claim. A national regulator's own register is high. A one-off spreadsheet
  is lower.

field_mappings
  One entry per canonical field you can fill. For each:
    canonical_field   from the ontology, exactly
    source_field      the column name, exactly as given
    transforms        ops in order. args_json carries arguments as a JSON
                      object string, e.g. {{"formats": ["%d/%m/%Y"]}}, or "" if
                      the op takes none.
    field_confidence  0 to 1, how sure you are this mapping and parse are right
    note              one short sentence

  Rules that matter:
  - A column with abn_valid near 1.0 is an ABN. Use op abn_normalize.
    Same for acn_valid and acn_normalize. Do not map a column as an identifier
    when its valid rate is low.
  - For entity_type and status, use map_values and cover every value listed in
    all_values. Set default to "unknown". Never invent a category.
  - Only use the enum values the ontology allows.
  - For dates, use parse_date with the formats the profile detected.
  - Build address.full with concat only when separate address parts exist.
  - Do not map a column just because its name looks right. Check the examples.

unmapped_source_fields
  Every source column you did NOT map, with one reason each. Be complete.

unfilled_canonical_fields
  Every ontology field this source cannot fill, with a reason. If a value could
  only be produced by guessing, it belongs here, not in field_mappings.

Leaving something unmapped is a correct answer. Guessing is not.
"""


def _ontology_text() -> str:
    """The ontology as the model needs to see it: fields, notes, allowed values."""
    data = yaml.safe_load(open(ONTOLOGY_FILE))
    lines = []
    for section in ("entity", "address"):
        lines.append(f"{section}:")
        for name, spec in data[section].items():
            bits = [f"  {section}.{name}", f"({spec.get('type')})"]
            if spec.get("values"):
                bits.append(f"one of {spec['values']}")
            if spec.get("pattern"):
                bits.append(f"pattern {spec['pattern']}")
            if spec.get("notes"):
                bits.append(f"- {spec['notes'].strip()}")
            lines.append(" ".join(bits))
    return "\n".join(lines)


def _transforms_text() -> str:
    data = yaml.safe_load(open(TRANSFORMS_FILE))
    lines = []
    for name, spec in data["ops"].items():
        args = f"({', '.join(spec['args'])})" if spec["args"] else "()"
        lines.append(f"  {name}{args}: {' '.join(spec['does'].split())}")
    return "\n".join(lines)


def _columns_text(profile: dict) -> str:
    """The profile, trimmed to what actually helps and nothing else."""
    lines = []
    for column in profile["columns"]:
        looks = {k: v for k, v in column["looks_like"].items()
                 if k != "date_formats" and isinstance(v, (int, float)) and v >= 0.1}
        dates = column["looks_like"].get("date_formats") or []
        parts = [
            f'- "{column["name"]}"',
            f'null_rate={column["null_rate"]}',
            f'distinct={column["distinct"]}{"+" if column["distinct_is_capped"] else ""}',
            f'max_len={column["max_length"]}',
        ]
        if looks:
            parts.append("looks_like=" + json.dumps(looks))
        if dates:
            parts.append("date_formats=" + json.dumps(
                [d["format"] for d in dates if d["share"] > 0.5]))
        lines.append("  ".join(parts))
        if column["all_values"]:
            lines.append(f'    all_values={json.dumps(column["all_values"])}')
        else:
            lines.append(f'    examples={json.dumps(column["examples"][:6], default=str)}')
    return "\n".join(lines)


def build_prompt(probe: dict, profile: dict) -> str:
    shape = {k: v for k, v in probe["shape"].items() if k != "url"}
    shape["records_available"] = probe["records_pulled"]
    return PROMPT.format(
        rows=profile["rows_sampled"],
        ontology=_ontology_text(),
        transforms=_transforms_text(),
        shape=json.dumps(shape, indent=1),
        columns=_columns_text(profile),
    )


def unpack(draft: dict) -> dict:
    """Turn the model's reply into the config shape the engine reads.

    The only change is args_json, which we ask for as a string because a
    response schema cannot describe a free-form object. We parse it here and
    drop any op whose arguments will not parse, rather than letting a broken
    argument reach the engine.
    """
    problems = []
    for mapping in draft.get("field_mappings", []):
        chain = []
        for step in mapping.get("transforms", []):
            args = {}
            raw = (step.get("args_json") or "").strip()
            if raw:
                try:
                    args = json.loads(raw)
                except json.JSONDecodeError:
                    problems.append(
                        f"{mapping['canonical_field']}: could not parse args "
                        f"for op {step['op']}: {raw[:60]}")
                    continue
            chain.append({"op": step["op"], **args})
        mapping["transforms"] = chain
    draft["_unpack_problems"] = problems
    return draft


def c3_propose(probe: dict, profile: dict, source: dict,
               extra: str = "") -> dict:
    """One model call. Returns the draft config and what it cost."""
    model = config.load()["models"]["mapping"]
    prompt = build_prompt(probe, profile) + extra

    out = llm.ask(model, prompt, schema=SCHEMA,
                  step=f"c3_propose:{source['source_id']}",
                  thinking_budget=None)
    draft = unpack(out["data"])

    return {
        "draft": draft,
        "prompt_chars": len(prompt),
        "model": model,
        "model_calls": 1,
        "usd": out["usage"]["usd"],
        "input_tokens": out["usage"]["input_tokens"],
        "output_tokens": out["usage"]["output_tokens"],
        "seconds": out["usage"]["seconds"],
    }
