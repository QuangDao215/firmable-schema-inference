"""The comparison: what if we had not chosen search terms?

This is not part of the pipeline. It answers one question for the write-up.

A system that searches for nothing in particular and hopes the results are
useful is not a system. To show what that looks like, we crawled 300 datasets
with `q=*:*` and no format filter, scored them with the same scorer, and
compared the top 50 against the top 50 the real pipeline produces.

Run:
    python -m src.catalogue baseline     (once, to fetch)
    python -m src.baseline_report
"""

from __future__ import annotations

import json
import statistics
import sys

from . import config
from .catalogue import load_pages
from .score import score_all

BASELINE_FOLDER = config.ROOT / "data" / "raw" / "baseline"


def main() -> None:
    if not BASELINE_FOLDER.exists() or not list(BASELINE_FOLDER.glob("*.json")):
        sys.exit("No baseline crawl. Run: python -m src.catalogue baseline")

    scored_file = config.path("interim") / "scored.jsonl"
    if not scored_file.exists():
        sys.exit("No scored.jsonl. Run: python -m src.score")

    baseline = score_all(list(load_pages(BASELINE_FOLDER).values()))
    pipeline = [json.loads(line) for line in open(scored_file)]

    k = config.load()["catalogue"]["shortlist_size"]

    def summarise(name: str, rows: list[dict]) -> dict:
        top = rows[:k]
        return {
            "name": name,
            "crawled": len(rows),
            "machine_readable": sum(1 for r in rows if r["passes_format_gate"]),
            "top_k_readable": sum(1 for r in top if r["passes_format_gate"]),
            "top_k_best": top[0]["rule_score"] if top else 0,
            "top_k_median": statistics.median(r["rule_score"] for r in top) if top else 0,
            "top_k_worst": top[-1]["rule_score"] if top else 0,
            "top_k_zero": sum(1 for r in top if r["rule_score"] == 0),
        }

    rows = [summarise("no search terms", baseline),
            summarise("chosen search terms", pipeline)]

    print("What choosing search terms is worth")
    print("=" * 70)
    print(f"Both columns scored by the same scorer. Top k = {k}.\n")

    labels = [
        ("crawled", "datasets crawled"),
        ("machine_readable", "of those, machine-readable"),
        ("top_k_readable", f"top {k}: machine-readable"),
        ("top_k_best", f"top {k}: best score"),
        ("top_k_median", f"top {k}: median score"),
        ("top_k_worst", f"top {k}: worst score"),
        ("top_k_zero", f"top {k}: scored zero"),
    ]
    print(f"  {'':34}{'no search terms':>18}{'chosen terms':>16}")
    for key, label in labels:
        a, b = rows[0][key], rows[1][key]
        fmt = (lambda v: f"{v:.2f}") if "score" in key else (lambda v: f"{v}")
        print(f"  {label:34}{fmt(a):>18}{fmt(b):>16}")

    print()
    print(f"Best 5 datasets an unaimed crawl would have handed us:")
    for r in baseline[:5]:
        print(f"  {r['rule_score']:.2f}  {r['title'][:62]}")

    print()
    print("Best 5 from the real pipeline:")
    for r in pipeline[:5]:
        print(f"  {r['rule_score']:.2f}  {r['title'][:62]}")

    out = config.path("outputs") / "baseline_comparison.json"
    out.write_text(json.dumps({
        "what_this_is": "Comparison only. The pipeline does not use an unaimed crawl.",
        "top_k": k,
        "no_search_terms": rows[0],
        "chosen_search_terms": rows[1],
        "unaimed_top_5": [{"score": r["rule_score"], "title": r["title"]}
                          for r in baseline[:5]],
    }, indent=1))
    print(f"\nWritten to {out}")


if __name__ == "__main__":
    main()
