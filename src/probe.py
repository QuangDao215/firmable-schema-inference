"""A cheap dry run before we spend anything at scale.

Makes three calls:
  1. A hello, to prove the key works.
  2. One realistic triage batch of 20 datasets, on the cheap model.
  3. The same batch on the mapping model, so we can compare.

Then it prints what a full run would cost, based on those real numbers rather
than a guess.

Run:
    python -m src.probe
"""

from __future__ import annotations

import json

from . import config, llm

TRIAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "dataset_id": {"type": "string"},
                    "confidence": {"type": "number"},
                    "reason": {"type": "string"},
                },
                "required": ["dataset_id", "confidence", "reason"],
            },
        }
    },
    "required": ["results"],
}

PROMPT = """You are triaging Australian government datasets for a company database.

For each dataset below, judge how likely it is to contain records about
identifiable Australian businesses: a named company, a licence holder, a
contractor, a supplier. A dataset about weather, geology or individual people
is not a business dataset.

Return one result per dataset. Copy the dataset_id back exactly.
confidence is 0 to 1. reason is one short sentence, under 20 words.

Datasets:
{items}
"""


def _batch_text(rows: list[dict]) -> str:
    lines = []
    for r in rows:
        lines.append(json.dumps({
            "dataset_id": r["dataset_id"],
            "title": r["title"],
            "publisher": r["organisation_title"],
            "formats": r["readable_formats"],
            # Descriptions run to thousands of characters. Input is cheap but
            # not free, and the first 400 say what the dataset is.
            "description": (r["description"] or "")[:400],
        }, ensure_ascii=False))
    return "\n".join(lines)


def main() -> None:
    settings = config.load()
    scored_file = config.path("interim") / "scored.jsonl"
    rows = [json.loads(line) for line in open(scored_file)]
    candidates = [r for r in rows if r["passes_format_gate"]][:20]

    print("=" * 66)
    print("1. Hello call, cheapest model")
    print("=" * 66)
    hello = llm.ask(settings["models"]["triage"],
                    "Reply with the single word: ready",
                    step="probe_hello", thinking_budget=None)
    print(f"   reply  : {str(hello['data']).strip()[:40]}")
    print(f"   usage  : {hello['usage']['input_tokens']} in, "
          f"{hello['usage']['output_tokens']} out, "
          f"{hello['usage']['seconds']}s, ${hello['usage']['usd']:.6f}")

    prompt = PROMPT.format(items=_batch_text(candidates))
    print(f"\n   batch prompt is {len(prompt):,} characters "
          f"({len(prompt) // 4:,} tokens, roughly)")

    results = {}
    for label in ["triage", "mapping"]:
        model = settings["models"][label]
        print()
        print("=" * 66)
        print(f"2. Real triage batch of 20, on {model}  (settings.models.{label})")
        print("=" * 66)
        try:
            out = llm.ask(model, prompt, schema=TRIAGE_SCHEMA,
                          step=f"probe_batch_{label}")
        except Exception as err:
            print(f"   FAILED: {type(err).__name__}: {str(err)[:200]}")
            continue

        u = out["usage"]
        items = out["data"]["results"]
        results[model] = u

        print(f"   usage  : {u['input_tokens']} in, {u['output_tokens']} out, "
              f"{u['seconds']}s, ${u['usd']:.6f}")
        print(f"   got    : {len(items)} results for {len(candidates)} datasets")
        ids_back = {i['dataset_id'] for i in items}
        missing = [c['dataset_id'] for c in candidates if c['dataset_id'] not in ids_back]
        print(f"   ids    : {'all returned' if not missing else f'{len(missing)} MISSING'}")
        print("   sample :")
        for item in items[:4]:
            title = next((c["title"] for c in candidates
                          if c["dataset_id"] == item["dataset_id"]), "?")
            print(f"     {item['confidence']:.2f}  {title[:40]:40}  {item['reason'][:44]}")

    print()
    print("=" * 66)
    print("What a full run would cost, from the numbers above")
    print("=" * 66)
    for model, u in results.items():
        batches = 6                       # 120 datasets, 20 per batch
        print(f"   {model}")
        print(f"     triage of 120 datasets ({batches} batches): "
              f"${u['usd'] * batches:.4f}, about {u['seconds'] * batches:.0f}s")

    print()
    print("Running total across every call made so far:")
    print(f"   {json.dumps(llm.spend_so_far())}")


if __name__ == "__main__":
    main()
