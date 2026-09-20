"""Step C5: the model is shown what broke and fixes its own config.

This is the step the assignment cares about most: a model correcting its own
output rather than being corrected by a person.

What makes it a correction and not a second opinion is that the report it reads
was produced by running its config over real rows. Nothing in that report is
another model's opinion. A failed ABN checksum is a fact.

At most two rounds. If it still fails, the source is flagged for a human and
the config is kept as a draft rather than thrown away.
"""

from __future__ import annotations

import json

from .. import config, llm
from . import propose, validate

MAX_ROUNDS = 2

# Ops that check a value rather than merely reshape it. Dropping one of these
# makes a config look better and be worse.
VALIDATING_OPS = {"abn_normalize", "acn_normalize", "nzbn_normalize",
                  "postcode_extract", "state_normalize", "parse_date",
                  "parse_datetime", "map_values"}


def removed_checks(before: dict, after: dict) -> list[str]:
    """Validating ops the revision dropped, per canonical field."""
    def ops_by_field(cfg):
        return {m["canonical_field"]: {t["op"] for t in m["transforms"]}
                for m in cfg["field_mappings"]}

    old_ops, new_ops = ops_by_field(before), ops_by_field(after)
    lost = []
    for field, ops in old_ops.items():
        gone = (ops & VALIDATING_OPS) - new_ops.get(field, set())
        lost.extend(f"{field}:{op}" for op in sorted(gone))
    return lost

PROMPT = """You proposed a mapping config. We ran it over real rows you had not
seen. Here is what happened.

{report}

# YOUR CURRENT CONFIG

{current}

# THE TRANSFORMS you may use

{transforms}

# FIX IT

Return the whole config again, corrected. Keep what worked. Change only what
the report says is wrong.

Common fixes, in the order they usually apply:

- "value not in the enum, fell back to default": the sample did not contain
  that value. Add it to the map_values mapping with the right ontology value.
  The report lists the real values that did this.
- "dropped a value the source had": look at the example values. If they are a
  placeholder such as "0", "N/A" or "NULL", add a null_if op BEFORE the rest of
  the chain so the drop is a decision rather than an accident. If they are real
  values your transform cannot handle, fix the transform, for example by adding
  a date format.
- "losing values": the source column has data and your field does not. The
  transform chain is eating it.
- "two mappings write this one canonical field": a canonical field takes one
  mapping. If you want one column with another as a fallback, use a single
  mapping whose first op is coalesce, with the preferred column first, e.g.
  op coalesce with args_json {{"fields": ["Current Name", "Company Name"]}}.
- "produced almost nothing": the mapping is wrong. Either fix it or move the
  field to unfilled_canonical_fields with a reason.

Rules that still apply:

- You pick ops by name from the list above. You never write code.
- Only ontology enum values.
- Lower field_confidence where the report shows you were too sure.
- Moving a field to unfilled_canonical_fields is a correct answer. Guessing is
  not.
"""


def _current_config_text(cfg: dict) -> str:
    """The parts of the config the model is allowed to change."""
    return json.dumps({
        "record_id": cfg["record_id"],
        "observed_at": cfg["observation"]["observed_at"],
        "source_reliability": cfg["observation"]["source_reliability"],
        "field_mappings": [
            {**m, "transforms": [
                {"op": t["op"],
                 "args_json": json.dumps({k: v for k, v in t.items() if k != "op"})
                 if len(t) > 1 else ""}
                for t in m["transforms"]]}
            for m in cfg["field_mappings"]],
        "unmapped_source_fields": cfg["unmapped_source_fields"],
        "unfilled_canonical_fields": cfg["unfilled_canonical_fields"],
    }, indent=1)


def c5_revise(cfg: dict, probe: dict, profile: dict, report: dict,
              source: dict) -> dict:
    """Loop: show the failures, get a fix, test it again. At most twice."""
    model = config.load()["models"]["mapping"]
    rounds = []
    current_cfg, current_report = cfg, report

    for round_number in range(1, MAX_ROUNDS + 1):
        if not current_report["needs_revision"]:
            break

        prompt = PROMPT.format(
            report=validate.failure_report(current_report),
            current=_current_config_text(current_cfg),
            transforms=propose._transforms_text(),
        )
        out = llm.ask(model, prompt, schema=propose.SCHEMA,
                      step=f"c5_revise:{source['source_id']}:round{round_number}",
                      thinking_budget=None)
        draft = propose.unpack(out["data"])

        candidate = validate.build_config(draft, probe, source)
        candidate_report = validate.c4_validate(candidate, probe, profile)

        before = len(current_report["failures"])
        after = len(candidate_report["failures"])
        rounds.append({
            "round": round_number,
            "failures_before": before,
            "failures_after": after,
            "improved": after < before,
            "usd": out["usage"]["usd"],
            "input_tokens": out["usage"]["input_tokens"],
            "output_tokens": out["usage"]["output_tokens"],
            "seconds": out["usage"]["seconds"],
            "unpack_problems": draft.get("_unpack_problems", []),
        })

        weakened = removed_checks(current_cfg, candidate)
        rounds[-1]["removed_checks"] = weakened

        # Only keep a revision that actually made things better. A model can
        # make a config worse, and we would rather ship the earlier one.
        #
        # "Better" is not just fewer failures. A model can pass the test by
        # deleting the test: on one source it removed postcode_extract, which
        # made the failure count zero and let a US zip code through as an
        # Australian postcode. A revision that drops a validating transform is
        # rejected however good its numbers look.
        if weakened:
            rounds[-1]["kept"] = False
            rounds[-1]["why_rejected"] = (
                f"removed validating transforms: {weakened}")
            break
        if after <= before:
            current_cfg, current_report = candidate, candidate_report
        else:
            rounds[-1]["kept"] = False
            break
        rounds[-1]["kept"] = True

    return {
        "config": current_cfg,
        "report": current_report,
        "rounds": rounds,
        "model_calls": len(rounds),
        "usd": round(sum(r["usd"] for r in rounds), 5),
        "still_failing": current_report["needs_revision"],
    }
