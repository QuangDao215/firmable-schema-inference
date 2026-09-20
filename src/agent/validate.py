"""Step C4: run the draft config over rows the model never saw, and count.

No model call. This is the step that makes C5 a correction rather than a
second opinion. The model is handed real parse failures and real checksum
failures, not another model's opinion of its work.

Two things are measured:

  failures  where a mapping dropped a value the source had, fell back to an
            enum default, named a column that does not exist, or raised.
  coverage  how often each mapped field actually produced a value, against the
            confidence the model claimed for it.

The useful comparison is fill rate against how full the source column is, not
against the confidence the model claimed.

Those are different things. `field_confidence` means "we are sure this mapping
and parse are right". Fill rate means "this column has data". A charity's
"other names" column is empty 87% of the time, and a mapping of it can still be
perfectly correct. Flagging that as overclaimed would send the model chasing a
problem that is not there.

What is a real problem: a source column that is 99% full producing a canonical
field that fills 13%. That means the transform is eating values.
"""

from __future__ import annotations

import json

from .. import config
from ..engine import run as run_engine

# Rows to test against. These start after the sample the profile was built
# from, so the model has never seen them.
HOLDOUT_START = 400
HOLDOUT_ROWS = 800


def build_config(draft: dict, probe: dict, source: dict) -> dict:
    """Assemble a full mapping config from the model's draft and what we know.

    The model proposes the mapping. It does not get to decide the source id,
    the licence or how the file is opened. Those are facts we already hold.
    """
    return {
        "config_version": "1.0",
        "source_id": source["source_id"],
        "generated_by": {
            "agent_version": "0.1",
            "model": source.get("_model", ""),
            "generated_at": source.get("_generated_at", ""),
        },
        "resource": probe["shape"],
        "record_id": draft["record_id"],
        "observation": {
            "licence": source.get("licence") or "unknown",
            "source_reliability": draft["source_reliability"],
            "observed_at": {
                **draft["observed_at"],
                # Used when the source has no date column of its own. The
                # ontology insists observed_at and ingested_at are different
                # things, so falling back to "now" would be a lie: it would
                # say the publisher stated this fact at the moment we read it.
                "fallback_constant": source.get("modified") or "",
                "fallback_source": ("dataset metadata_modified from CKAN"
                                    if source.get("modified") else "none"),
            },
            # When the claim became true, and stopped being true, in the world.
            # Different questions from observed_at, and a source often answers
            # only one of them. Empty is a normal answer.
            "valid_from": draft.get("valid_from", {}),
            "valid_to": draft.get("valid_to", {}),
        },
        "field_mappings": draft["field_mappings"],
        "unmapped_source_fields": draft["unmapped_source_fields"],
        "unfilled_canonical_fields": draft["unfilled_canonical_fields"],
    }


def c4_validate(cfg: dict, probe: dict, profile: dict) -> dict:
    """Run the config over held-out rows and report what broke."""
    records = [json.loads(line) for line in open(probe["records_file"])]
    holdout = records[HOLDOUT_START:HOLDOUT_START + HOLDOUT_ROWS]

    if not holdout:
        # A small file has nothing left over. Say so rather than silently
        # testing on the rows the profile came from.
        holdout = records[-min(len(records) // 4 or 1, len(records)):]
        note = (f"file has only {len(records)} records, so the holdout "
                f"overlaps the sample")
    else:
        note = ""

    from ..engine import duplicate_fields
    result = run_engine(cfg, holdout)

    # A config with two mappings for one field is ambiguous whatever the rows
    # say, so this failure is added before any row is looked at.
    for dup in duplicate_fields(cfg):
        result["failures"].insert(0, {
            "canonical_field": dup["canonical_field"],
            "source_field": ", ".join(dup["source_fields"]),
            "problem": "two mappings write this one canonical field",
            "count": len(holdout),
            "share_of_rows": 1.0,
            "examples": dup["source_fields"],
        })

    # How full each source column is, so we compare like with like.
    column_fill = {c["name"]: round(1 - c["null_rate"], 3)
                   for c in profile["columns"]}
    for c in result["coverage"]:
        c["source_column_filled"] = column_fill.get(c["source_field"])

    # A failure that hits most rows is a broken mapping. One that hits a few
    # is a messy source. The model needs to be told which is which.
    serious = [f for f in result["failures"] if f["share_of_rows"] >= 0.10]
    dead = [c for c in result["coverage"] if c["fill_rate"] < 0.05]

    # The real signal: the source column has data and our field does not.
    losing_values = [
        c for c in result["coverage"]
        if c["source_column_filled"] is not None
        and c["source_column_filled"] - c["fill_rate"] > 0.15
        and c not in dead
    ]

    return {
        "holdout_rows": len(holdout),
        "holdout_note": note,
        "rows_out": result["rows_out"],
        "failures": result["failures"],
        "coverage": result["coverage"],
        "serious_failures": len(serious),
        "dead_fields": [c["canonical_field"] for c in dead],
        "losing_values": [
            {"canonical_field": c["canonical_field"],
             "source_column_filled": c["source_column_filled"],
             "fill_rate": c["fill_rate"]}
            for c in losing_values],
        # Revise whenever anything failed at all, not only when a lot did.
        # A 1.5% enum fallback is a genuinely incomplete mapping: the sample
        # simply did not contain that value. A 4% drop turned out to be a
        # publisher using "0" as a null. Both are worth one more round.
        "needs_revision": bool(result["failures"] or dead or losing_values),
        "sample_observations": result["observations"][:3],
    }


def failure_report(validation: dict) -> str:
    """The text C5 gets. Short, concrete, and all of it measured."""
    lines = [f"Your config was run over {validation['holdout_rows']} rows that "
             f"the profile was NOT built from. Here is what happened."]
    if validation["holdout_note"]:
        lines.append(f"Note: {validation['holdout_note']}")

    lines.append("\nFILL RATES. 'column' is how full the source column is, "
                 "'field' is how often your mapping produced a value.")
    lines.append("A field well below its column means the transforms are "
                 "eating values.")
    for c in validation["coverage"]:
        column = c.get("source_column_filled")
        flag = ""
        if c["fill_rate"] < 0.05:
            flag = "  <-- produced almost nothing, fix or drop this mapping"
        elif column is not None and column - c["fill_rate"] > 0.15:
            flag = "  <-- losing values"
        lines.append(
            f"  {c['canonical_field']:26} from \"{c['source_field']}\" "
            f"column {('%.0f%%' % (column * 100)) if column is not None else '  ?'} "
            f"-> field {c['fill_rate']:.0%}{flag}")

    if validation["failures"]:
        lines.append("\nFAILURES:")
        for f in validation["failures"]:
            lines.append(
                f"  {f['canonical_field']:26} {f['problem']} "
                f"on {f['share_of_rows']:.0%} of rows ({f['count']} times)")
            if f["examples"]:
                lines.append(f"      real values that did this: "
                             f"{json.dumps(f['examples'][:5], default=str)}")
    else:
        lines.append("\nNo failures.")

    return "\n".join(lines)
