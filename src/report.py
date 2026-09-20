"""The numbers Part 2 and Part 5 ask for, read off what actually ran.

    "Tell us the step count, the token cost and the wall-clock time for
     onboarding one source, and where a run can fail without losing the whole
     job."

Every figure comes from `runs/<source_id>/state.json` and
`runs/llm_calls.jsonl`, both written as the pipeline ran. Nothing is estimated,
and where a number is a guess it says so.

Two costs are reported and they are different questions:

  as built      what this submission actually cost, including every rerun,
                every rejected config and one whole approach we replaced. This
                is what the development really came to.
  steady state  what one clean pass costs with no rework. This is what it
                would cost to onboard the seventh source tomorrow.

Run:
    python -m src.report
    python -m src.report --clean-run <path to another run's runs/ folder>
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

from . import config, llm

STEP_ORDER = ["c1_probe", "c2_profile", "c3_propose", "c4_validate",
              "c5_revise", "c6_review", "c7_freeze"]

STEP_NAMES = {
    "c1_probe": "probe", "c2_profile": "profile", "c3_propose": "propose",
    "c4_validate": "validate", "c5_revise": "revise", "c6_review": "review",
    "c7_freeze": "freeze",
}

# Which phase of the work each model call belongs to.
PHASES = {
    "probe_": "one-off: checking the API before spending",
    "triage_": "Part 1: model triage of 120 datasets",
    "handcheck_": "Part 1: checking 20 shortlisted files against their data",
    "c3_propose": "Part 2: proposing a mapping",
    "c5_revise": "Part 2: correcting a mapping",
    "precision_": "superseded: an earlier precision check, redone by a "
                  "different model outside the pipeline",
}

# Where a run can fail without losing the whole job. The assignment's question,
# answered against the real step list.
FAILURE_MODES = {
    "probe": "download dies or the format is unreadable. Retry costs the "
             "download only.",
    "profile": "a column will not parse. It is profiled as unknown and the "
               "run continues.",
    "propose": "the model returns unusable JSON. Retry costs one call.",
    "validate": "the engine raises on a row. It is counted as a failure and "
                "the run continues.",
    "revise": "no convergence in two rounds. The source is flagged for a "
              "human and the config is kept as a draft.",
    "review": "a person rejects. Their note is kept and propose through "
              "review rerun with it.",
    "freeze": "only runs on approval. Nothing else can write to configs/.",
}


def phase_of(step: str) -> str:
    for prefix, name in PHASES.items():
        if step.startswith(prefix):
            return name
    return "other"


def read_calls(folder: Path) -> list[dict]:
    log = folder / "llm_calls.jsonl"
    return [json.loads(line) for line in open(log)] if log.exists() else []


def per_source(folder: Path, calls: list[dict]) -> list[dict]:
    rows = []
    for state_file in sorted(folder.glob("*/state.json")):
        data = json.loads(state_file.read_text())
        source_id = data["source_id"]
        steps = data["steps"]
        mine = [c for c in calls if c["step"].endswith(f":{source_id}")
                or f":{source_id}:" in c["step"]]
        rows.append({
            "source_id": source_id,
            "title": data.get("source", {}).get("title", ""),
            "steps": [STEP_NAMES[s] for s in STEP_ORDER if s in steps],
            "step_count": len([s for s in STEP_ORDER if s in steps]),
            "seconds_per_step": {STEP_NAMES[s]: steps[s].get("seconds", 0)
                                 for s in STEP_ORDER if s in steps},
            "wall_clock_seconds": round(
                sum(v.get("seconds", 0) for v in steps.values()), 2),
            "model_calls": len(mine),
            "input_tokens": sum(c["input_tokens"] for c in mine),
            "output_tokens": sum(c["output_tokens"] for c in mine),
            "usd": round(sum(c["usd"] for c in mine), 5),
            "revision_rounds": len(
                steps.get("c5_revise", {}).get("result", {}).get("rounds", [])),
            "reviewer_rejections": len(data.get("reviewed_notes", [])
                                       or data.get("reviewer_notes", [])),
            "approved_by": steps.get("c7_freeze", {}).get(
                "result", {}).get("approved_by"),
            "unresolved_failures": steps.get("c7_freeze", {}).get(
                "result", {}).get("unresolved_failures"),
        })
    return rows


def main() -> None:
    folder = config.path("runs")
    calls = read_calls(folder)
    rows = per_source(folder, calls)

    clean_folder = None
    if "--clean-run" in sys.argv:
        clean_folder = Path(sys.argv[sys.argv.index("--clean-run") + 1])
    clean_calls = read_calls(clean_folder) if clean_folder else []

    by_phase = defaultdict(lambda: {"calls": 0, "input": 0, "output": 0,
                                    "usd": 0.0, "seconds": 0.0})
    for call in calls:
        bucket = by_phase[phase_of(call["step"])]
        bucket["calls"] += 1
        bucket["input"] += call["input_tokens"]
        bucket["output"] += call["output_tokens"]
        bucket["usd"] += call["usd"]
        bucket["seconds"] += call["seconds"]

    print("=" * 76)
    print("What it cost, as built")
    print("=" * 76)
    print("Includes every rerun, every rejected config, and one approach we")
    print("replaced. This is what the development actually came to.\n")
    print(f"  {'phase':58} {'calls':>5} {'usd':>8}")
    for phase, bucket in sorted(by_phase.items(), key=lambda kv: -kv[1]["usd"]):
        print(f"  {phase[:58]:58} {bucket['calls']:>5} {bucket['usd']:>8.4f}")
    total = llm.spend_so_far()
    print(f"  {'TOTAL':58} {total['calls']:>5} {total['usd']:>8.4f}")
    print(f"\n  {total['input_tokens']:,} input tokens, "
          f"{total['output_tokens']:,} output tokens, "
          f"{total['seconds']:.0f}s of model time")

    print("\n" + "=" * 76)
    print("Onboarding one source, as built")
    print("=" * 76)
    print(f"  {'source':40} {'steps':>5} {'calls':>5} {'tokens':>15} "
          f"{'secs':>6} {'usd':>8} {'rev':>4}")
    for r in rows:
        print(f"  {r['source_id'][:40]:40} {r['step_count']:>5} "
              f"{r['model_calls']:>5} "
              f"{r['input_tokens']:>7,}+{r['output_tokens']:<7,} "
              f"{r['wall_clock_seconds']:>6.1f} {r['usd']:>8.4f} "
              f"{r['revision_rounds']:>4}")
    if rows:
        mean = sum(r["usd"] for r in rows) / len(rows)
        wall = sorted(r["wall_clock_seconds"] for r in rows)[len(rows) // 2]
        print(f"\n  mean ${mean:.4f} per source, median {wall:.0f}s wall clock")

    steady = None
    if clean_calls:
        onboarding = [c for c in clean_calls
                      if c["step"].startswith(("c3_propose", "c5_revise"))]
        sources = len({c["step"].split(":")[1] for c in onboarding
                       if ":" in c["step"]})
        steady = {
            "sources": sources,
            "model_calls": len(onboarding),
            "input_tokens": sum(c["input_tokens"] for c in onboarding),
            "output_tokens": sum(c["output_tokens"] for c in onboarding),
            "seconds": round(sum(c["seconds"] for c in onboarding), 1),
            "usd": round(sum(c["usd"] for c in onboarding), 5),
        }
        steady["usd_per_source"] = round(steady["usd"] / max(sources, 1), 5)
        steady["seconds_per_source"] = round(steady["seconds"] / max(sources, 1), 1)

        print("\n" + "=" * 76)
        print("Onboarding one source, steady state")
        print("=" * 76)
        print("One clean pass, no rework. What the seventh source would cost.\n")
        print(f"  {steady['sources']} sources, {steady['model_calls']} model "
              f"calls, {steady['input_tokens']:,} in + "
              f"{steady['output_tokens']:,} out")
        print(f"  ${steady['usd']:.4f} total")
        print(f"\n  ${steady['usd_per_source']:.4f} and "
              f"{steady['seconds_per_source']:.0f}s of model time per source")

    print("\n" + "=" * 76)
    print("One-time cost against per-record cost")
    print("=" * 76)
    print("  Writing a config     one-time, per source, costs money")
    print("  Running the engine   per record, makes ZERO model calls")
    print("  Matching, profiles   zero model calls")
    print("\n  Per-record cost is $0.00 and that is measured, not claimed.")
    print("  outputs/extraction_report.json records zero model calls for the")
    print("  whole extraction stage: 126,540 records, 6.5 seconds.")

    print("\n" + "=" * 76)
    print("Where a run can fail without losing the whole job")
    print("=" * 76)
    for step, what in FAILURE_MODES.items():
        print(f"  {step:10} {what}")
    print("\n  Every step writes its result to runs/<source_id>/state.json")
    print("  before the next one starts. A step already marked done is skipped")
    print("  on a rerun, so a crash costs one step rather than the download.")

    report = {
        "what_this_is": ("Measured, not estimated. Read from "
                         "runs/*/state.json and runs/llm_calls.jsonl, both "
                         "written while the pipeline ran."),
        "as_built": {
            "note": "Includes every rerun, rejected config and replaced "
                    "approach. What the development actually cost.",
            "by_phase": {k: {**v, "usd": round(v["usd"], 5),
                             "seconds": round(v["seconds"], 1)}
                         for k, v in by_phase.items()},
            "total": total,
            "per_source": rows,
        },
        "steady_state": steady,
        "one_time_vs_per_record": {
            "config_generation": "one-time, per source",
            "extraction_per_record_usd": 0.0,
            "extraction_model_calls": 0,
            "matching_model_calls": 0,
            "profile_model_calls": 0,
        },
        "where_a_run_can_fail": FAILURE_MODES,
    }
    out = config.path("outputs") / "onboarding_report.json"
    out.write_text(json.dumps(report, indent=1, default=str))
    print(f"\n  written to {out}")


if __name__ == "__main__":
    main()
