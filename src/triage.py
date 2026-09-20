"""Stage A4: the model reads the top candidates and judges each one.

The scorer before this is keyword matching. It cannot tell "one row per
company" from "counts of companies by state", because both are full of
business words. That judgment is what we pay a model for.

The model never sees our rule score. If we tell it what we already think, it
agrees with us and the two signals stop being independent.

Batches are cached to runs/triage/. A failure on batch 4 does not re-spend
batches 1 to 3.

Run:
    python -m src.triage
"""

from __future__ import annotations

import json
import sys
import time

from . import config, llm

BATCH_SIZE = 20

SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "dataset_id": {"type": "string"},
                    "record_grain": {
                        "type": "string",
                        "enum": ["entity", "aggregate", "event", "unknown"],
                    },
                    "confidence": {"type": "number"},
                    "likely_fields": {"type": "array", "items": {"type": "string"}},
                    "reason": {"type": "string"},
                },
                "required": ["dataset_id", "record_grain", "confidence",
                             "likely_fields", "reason"],
            },
        }
    },
    "required": ["results"],
}

PROMPT = """You are triaging Australian government datasets for a company database.

We are building a graph of Australian businesses. We need datasets where a ROW
IS A BUSINESS: a named company, a licence holder, a contractor, a supplier.

For each dataset below, decide:

record_grain - what one row of this dataset most likely is:
  "entity"    one row is one business or organisation
  "aggregate" one row is a total, a count or an average across many businesses
  "event"     one row is an occurrence, such as a single building approval or
              contract, which usually names a business
  "unknown"   not enough information to say

  This is the judgment that matters most. "ASIC Company Dataset" is entity.
  "Business counts by state and industry" is aggregate, even though every word
  in it is about business.

confidence - 0 to 1, how sure you are that this dataset names identifiable
  Australian businesses. Be willing to use low numbers.

likely_fields - which of these canonical fields the dataset probably carries.
  Use only these names, and only ones you actually expect:
  legal_name, trading_name, abn, acn, entity_type, status, website,
  industry_code, date_registered, address_full, locality, state, postcode

reason - one sentence, under 20 words, saying why.

Return one result per dataset. Copy dataset_id back exactly as given.

Datasets:
{items}
"""


def _describe(row: dict) -> str:
    """What the model gets to see about one dataset."""
    return json.dumps({
        "dataset_id": row["dataset_id"],
        "title": row["title"],
        "publisher": row["organisation_title"],
        "formats": row["readable_formats"],
        # File names often say more than the dataset page does.
        "resource_names": [r["name"] for r in row["resources"][:6] if r["name"]],
        # Descriptions run to thousands of characters. The first 400 say what
        # the dataset is, and input tokens are cheap but not free.
        "description": (row["description"] or "")[:400],
    }, ensure_ascii=False)


def _run_batch(model: str, rows: list[dict], number: int, cache_dir) -> list[dict]:
    """One model call, cached to disk so a rerun costs nothing."""
    cache_file = cache_dir / f"batch_{number:02d}.json"
    if cache_file.exists():
        print(f"  batch {number}: cached")
        return json.loads(cache_file.read_text())["results"]

    prompt = PROMPT.format(items="\n".join(_describe(r) for r in rows))
    out = llm.ask(model, prompt, schema=SCHEMA, step=f"triage_batch_{number}")
    results = out["data"]["results"]

    # Match by id, never by position. A reply one item short would otherwise
    # shift every judgment onto the wrong dataset.
    wanted = {r["dataset_id"] for r in rows}
    returned = {r["dataset_id"] for r in results}
    missing = wanted - returned
    extra = returned - wanted
    if missing:
        print(f"  batch {number}: WARNING {len(missing)} datasets got no answer")
    if extra:
        print(f"  batch {number}: WARNING {len(extra)} unknown ids returned, dropped")
        results = [r for r in results if r["dataset_id"] in wanted]

    u = out["usage"]
    print(f"  batch {number}: {len(results)}/{len(rows)} judged, "
          f"{u['input_tokens']}+{u['output_tokens']} tokens, "
          f"{u['seconds']}s, ${u['usd']:.4f}")

    cache_file.write_text(json.dumps({"results": results, "usage": u}, indent=1))
    return results


def main() -> None:
    settings = config.load()
    model = settings["models"]["triage"]
    pool_size = settings["catalogue"]["candidate_pool"]

    scored_file = config.path("interim") / "scored.jsonl"
    if not scored_file.exists():
        sys.exit("No scored.jsonl. Run: python -m src.score")

    rows = [json.loads(line) for line in open(scored_file)]
    candidates = [r for r in rows if r["is_primary_edition"]][:pool_size]
    by_id = {r["dataset_id"]: r for r in candidates}

    cache_dir = config.path("runs") / "triage"
    cache_dir.mkdir(parents=True, exist_ok=True)

    print(f"Triaging {len(candidates)} datasets with {model}, "
          f"{BATCH_SIZE} per call")
    started = time.time()

    judgments: dict[str, dict] = {}
    for number, start in enumerate(range(0, len(candidates), BATCH_SIZE), 1):
        batch = candidates[start:start + BATCH_SIZE]
        for result in _run_batch(model, batch, number, cache_dir):
            judgments[result["dataset_id"]] = result

    out_file = config.path("interim") / "triaged.jsonl"
    with open(out_file, "w") as f:
        for dataset_id, row in by_id.items():
            judgment = judgments.get(dataset_id)
            f.write(json.dumps({
                **row,
                "model_grain": judgment["record_grain"] if judgment else "unknown",
                # Kept separate from rule_score on purpose. Two different kinds
                # of uncertainty do not belong in one number.
                "model_confidence": judgment["confidence"] if judgment else 0.0,
                "model_likely_fields": judgment["likely_fields"] if judgment else [],
                "model_reason": judgment["reason"] if judgment
                                else "no answer from the model",
                "model_answered": judgment is not None,
            }) + "\n")

    grains: dict[str, int] = {}
    for j in judgments.values():
        grains[j["record_grain"]] = grains.get(j["record_grain"], 0) + 1

    print(f"\nJudged {len(judgments)} of {len(candidates)} in "
          f"{time.time() - started:.0f}s")
    print("What one row of each dataset is:")
    for grain, count in sorted(grains.items(), key=lambda kv: -kv[1]):
        print(f"  {grain:10} {count}")
    print(f"\nWritten to {out_file}")
    print(f"Spend so far: {json.dumps(llm.spend_so_far())}")


if __name__ == "__main__":
    main()
