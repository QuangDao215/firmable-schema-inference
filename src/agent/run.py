"""Run the onboarding agent over one source, or all six.

    python -m src.agent.run                 all six sources
    python -m src.agent.run <source_id>     just one
    python -m src.agent.run --force         ignore saved steps and redo them

Each step writes into runs/<source_id>/state.json. A step already marked done
is skipped, so a crash costs one step rather than the whole run.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from .. import config
from . import inspect as steps
from . import propose
from . import validate
from . import revise
from . import review
from .state import Run


def source_key(source: dict) -> str:
    """A short, stable folder name for a source."""
    base = re.sub(r"[^a-z0-9]+", "-", source["title"].lower()).strip("-")
    return f"{base[:40]}-{source['dataset_id'][:8]}"


def load_sources() -> list[dict]:
    path = config.path("outputs") / "selected_sources.json"
    if not path.exists():
        sys.exit("No selected_sources.json. Run: python -m src.select pick")
    sources = json.load(open(path))["selected"]
    for source in sources:
        source["source_id"] = source_key(source)
    return sources


def onboard(source: dict, force: bool = False) -> Run:
    run = Run(source["source_id"], force=force)
    run.data["source"] = {k: source.get(k) for k in
                          ("dataset_id", "title", "organisation", "licence",
                           "download_url", "download_format", "record_grain",
                           "landing_page")}

    print(f"\n{source['title'][:58]}")
    print(f"  id: {source['source_id']}")

    with run.step("c1_probe") as result:
        if result is not None:
            result.update(steps.c1_probe(source))
            print(f"  c1_probe: {result['real_format'] if 'real_format' in result else result['shape']['real_format']}"
                  f", {result['records_pulled']} records, "
                  f"{len(result['columns'])} columns"
                  + (", catalogue format was wrong"
                     if result["catalogue_format_was_wrong"] else ""))

    with run.step("c2_profile") as result:
        if result is not None:
            probe = run.result("c1_probe")
            profile = steps.c2_profile(source, probe)
            # The full column profile is big. Keep it beside the state file
            # rather than inside it, so state.json stays readable.
            out = run.folder / "column_profile.json"
            out.write_text(json.dumps(profile, indent=1))
            result.update({"columns_profiled": profile["columns_profiled"],
                           "rows_sampled": profile["rows_sampled"],
                           "profile_file": str(out)})
            interesting = [c for c in profile["columns"]
                           if c["looks_like"].get("abn_valid", 0) > 0.5
                           or c["looks_like"].get("acn_valid", 0) > 0.5]
            print(f"  c2_profile: {profile['columns_profiled']} columns from "
                  f"{profile['rows_sampled']} rows"
                  + (f", identifier columns found: "
                     f"{[c['name'] for c in interesting]}" if interesting else ""))

    with run.step("c3_propose") as result:
        if result is not None:
            probe = run.result("c1_probe")
            profile = json.load(open(run.result("c2_profile")["profile_file"]))
            # A reviewer who rejected an earlier attempt said why. Put that
            # in front of the model rather than expecting it to guess again.
            notes = run.data.get("reviewer_notes") or []
            extra = ""
            if notes:
                extra = ("\n\n# A HUMAN REVIEWER REJECTED YOUR EARLIER "
                         "ATTEMPT\n\nFix these before anything else. They "
                         "outrank your own judgement.\n\n"
                         + "\n".join(f"  - {n['note']}" for n in notes))
                print(f"  c3_propose: including {len(notes)} reviewer note(s)")
            out = propose.c3_propose(probe, profile, source, extra=extra)
            draft_file = run.folder / "draft_config.json"
            draft_file.write_text(json.dumps(out.pop("draft"), indent=1))
            result.update({**out, "draft_file": str(draft_file)})

            draft = json.loads(draft_file.read_text())
            print(f"  c3_propose: {len(draft['field_mappings'])} fields mapped, "
                  f"{len(draft['unmapped_source_fields'])} columns left alone, "
                  f"{len(draft['unfilled_canonical_fields'])} ontology fields "
                  f"it says this source cannot fill")
            print(f"              {out['input_tokens']}+{out['output_tokens']} "
                  f"tokens, {out['seconds']}s, ${out['usd']:.4f}")
            if draft.get("_unpack_problems"):
                for problem in draft["_unpack_problems"]:
                    print(f"              ARG PROBLEM: {problem}")

    with run.step("c4_validate") as result:
        if result is not None:
            probe = run.result("c1_probe")
            draft = json.loads(Path(run.result("c3_propose")["draft_file"]).read_text())
            cfg = validate.build_config(draft, probe, {
                **source,
                "_model": run.result("c3_propose").get("model", ""),
                "_generated_at": run.data["steps"]["c3_propose"].get("started", ""),
            })
            (run.folder / "config_draft_full.json").write_text(
                json.dumps(cfg, indent=1))

            profile = json.load(open(run.result("c2_profile")["profile_file"]))
            report = validate.c4_validate(cfg, probe, profile)
            (run.folder / "validation.json").write_text(json.dumps(report, indent=1))
            (run.folder / "failure_report.txt").write_text(
                validate.failure_report(report))

            result.update({k: report[k] for k in
                           ("holdout_rows", "rows_out", "serious_failures",
                            "dead_fields", "losing_values",
                            "needs_revision")})
            print(f"  c4_validate: {report['rows_out']} observations from "
                  f"{report['holdout_rows']} held-out rows")
            for c in report["coverage"]:
                column = c.get("source_column_filled")
                mark = ("  <-- nothing" if c["fill_rate"] < 0.05 else
                        "  <-- losing values"
                        if column is not None and column - c["fill_rate"] > 0.15
                        else "")
                print(f"               {c['canonical_field']:24} "
                      f"column {('%3.0f%%' % (column*100)) if column is not None else '  ?'}"
                      f" -> field {c['fill_rate']:>4.0%}{mark}")
            for f in report["failures"][:4]:
                print(f"               ! {f['canonical_field']}: {f['problem']} "
                      f"({f['share_of_rows']:.0%})")
            print(f"               needs revision: {report['needs_revision']}")

    with run.step("c5_revise") as result:
        if result is not None:
            probe = run.result("c1_probe")
            profile = json.load(open(run.result("c2_profile")["profile_file"]))
            cfg = json.loads((run.folder / "config_draft_full.json").read_text())
            report = json.loads((run.folder / "validation.json").read_text())

            out = revise.c5_revise(cfg, probe, profile, report, {
                **source,
                "_model": run.result("c3_propose").get("model", ""),
            })
            (run.folder / "config_revised.json").write_text(
                json.dumps(out["config"], indent=1))
            (run.folder / "validation_after.json").write_text(
                json.dumps(out["report"], indent=1))

            result.update({k: out[k] for k in
                           ("rounds", "model_calls", "usd", "still_failing")})
            if not out["rounds"]:
                print("  c5_revise: nothing to fix, skipped")
            for r in out["rounds"]:
                print(f"  c5_revise: round {r['round']}, failures "
                      f"{r['failures_before']} -> {r['failures_after']}, "
                      f"{'kept' if r.get('kept') else 'rejected, made it worse'}, "
                      f"{r['seconds']}s, ${r['usd']:.4f}")
            if out["still_failing"]:
                print("             still failing, needs a human")

    with run.step("c6_review") as result:
        if result is not None:
            probe = run.result("c1_probe")
            cfg = json.loads((run.folder / "config_revised.json").read_text())
            validation = json.loads((run.folder / "validation_after.json").read_text())
            rounds = run.result("c5_revise").get("rounds", [])

            records = [json.loads(line) for line in open(probe["records_file"])]
            holdout = records[400:405]

            text = review.card(source, cfg, validation, rounds, holdout)
            card_file = run.folder / "review_card.md"
            card_file.write_text(text)
            result.update({"card_file": str(card_file),
                           "lines": len(text.splitlines()),
                           "awaiting_decision": True})
            print(f"  c6_review: card written to {card_file}")
            print(f"             approve with: python -m src.approve "
                  f"{source['source_id']} --yes --by <name>")

    return run


def main() -> None:
    force = "--force" in sys.argv
    wanted = [a for a in sys.argv[1:] if not a.startswith("--")]

    sources = load_sources()
    if wanted:
        sources = [s for s in sources if s["source_id"] in wanted]
        if not sources:
            sys.exit(f"No such source. Available:\n  " +
                     "\n  ".join(s["source_id"] for s in load_sources()))

    runs = []
    for source in sources:
        try:
            runs.append(onboard(source, force))
        except Exception as err:
            print(f"  FAILED: {type(err).__name__}: {err}")
            print(f"  state kept in runs/{source['source_id']}/state.json")

    print("\n" + "=" * 62)
    for run in runs:
        s = run.summary()
        print(f"  {s['source_id'][:42]:42} {s['steps_done']} steps, "
              f"{s['seconds']}s, ${s['usd']}"
              + (f"  FAILED: {s['steps_failed']}" if s["steps_failed"] else ""))


if __name__ == "__main__":
    main()
