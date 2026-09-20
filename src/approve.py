"""The human gate, and step C7.

A config does not ship because a model said it was fine. It ships because a
person read the review card and said yes. This records who, when, and any note
they left, and only then freezes the config into configs/.

    python -m src.approve                      list what is waiting
    python -m src.approve <source_id>          print the review card
    python -m src.approve <id> --yes --by dao  approve and freeze
    python -m src.approve <id> --no  --by dao --note "postcode is wrong"
"""

from __future__ import annotations

import json
import sys
import time

from . import config
from .agent.run import load_sources
from .agent.state import Run


def _decide(run: Run, source: dict, approved: bool, who: str, note: str) -> None:
    cfg = json.loads((run.folder / "config_revised.json").read_text())
    validation = json.loads((run.folder / "validation_after.json").read_text())

    decision = {
        "approved": approved,
        "by": who,
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "note": note,
        "unresolved_failures": len(validation["failures"]),
        "reviewed_card": str(run.folder / "review_card.md"),
    }
    (run.folder / "approval.json").write_text(json.dumps(decision, indent=1))

    run.data["steps"]["c6_review"]["result"]["awaiting_decision"] = False
    run.data["steps"]["c6_review"]["result"]["decision"] = decision

    if not approved:
        run.data["steps"]["c7_freeze"] = {
            "status": "blocked", "reason": f"rejected by {who}: {note}"}

        # A rejection has to undo an earlier approval. Otherwise a config a
        # reviewer has just refused keeps shipping from configs/.
        frozen = config.path("configs") / f"{source['source_id']}.json"
        if frozen.exists():
            frozen.unlink()
            print(f"  removed the previously frozen {frozen.name}")

        # The note is the whole point of rejecting. Keep it so the agent can
        # be rerun with it, and keep every earlier one too.
        notes = run.data.setdefault("reviewer_notes", [])
        if note:
            notes.append({"by": who, "at": decision["at"], "note": note})

        # Clear the steps that depend on the mapping, so a rerun redoes them.
        for step in ("c3_propose", "c4_validate", "c5_revise", "c6_review"):
            run.data["steps"].pop(step, None)
        run.save()

        print(f"Recorded: REJECTED by {who}")
        print(f"  note: {note}")
        print(f"  Steps c3 to c6 cleared. Rerun with your note included:")
        print(f"    python -m src.agent.run {source['source_id']}")
        return

    # C7. Stamp the approval into the config and freeze it.
    cfg["generated_by"] = {
        **cfg.get("generated_by", {}),
        "revisions": len(run.result("c5_revise").get("rounds", [])),
        "approved_by": who,
        "approved_at": decision["at"],
    }
    cfg["validation"] = {
        "rows_tested": validation["holdout_rows"],
        "rows_emitted": validation["rows_out"],
        "failures": [{k: f[k] for k in
                      ("canonical_field", "problem", "count", "examples")}
                     for f in validation["failures"]],
    }

    frozen = config.path("configs") / f"{source['source_id']}.json"
    frozen.write_text(json.dumps(cfg, indent=1))

    run.data["steps"]["c7_freeze"] = {
        "status": "done", "seconds": 0,
        "result": {"config_file": str(frozen),
                   "approved_by": who,
                   "unresolved_failures": len(validation["failures"])}}
    run.save()

    print(f"Recorded: APPROVED by {who}")
    if note:
        print(f"  note: {note}")
    print(f"  frozen to {frozen}")
    if validation["failures"]:
        print(f"  note that {len(validation['failures'])} failure(s) remain "
              f"and you approved anyway. That is recorded in the config.")


def main() -> None:
    args = sys.argv[1:]
    flags = {a for a in args if a.startswith("--")}
    names = [a for a in args if not a.startswith("--")]

    def flag_value(name: str, default: str = "") -> str:
        if name in args:
            index = args.index(name)
            if index + 1 < len(args) and not args[index + 1].startswith("--"):
                return args[index + 1]
        return default

    who = flag_value("--by")
    note = flag_value("--note")
    # --by and --note consume the word after them
    names = [n for n in names if n not in (who, note)]

    sources = {s["source_id"]: s for s in load_sources()}

    if not names:
        print("Sources and where they stand:\n")
        for source_id, source in sources.items():
            run = Run(source_id)
            approval = run.folder / "approval.json"
            if approval.exists():
                d = json.loads(approval.read_text())
                state = (f"approved by {d['by']}" if d["approved"]
                         else f"REJECTED by {d['by']}")
            elif (run.folder / "review_card.md").exists():
                state = "waiting for a decision"
            else:
                state = "not reviewed yet"
            failures = len(json.loads(
                (run.folder / "validation_after.json").read_text())["failures"]
            ) if (run.folder / "validation_after.json").exists() else "?"
            print(f"  {source_id[:44]:44} {str(failures):>2} open failures  "
                  f"{state}")
        print("\n  python -m src.approve <source_id>              read the card")
        print("  python -m src.approve <source_id> --yes --by <name>")
        return

    source_id = names[0]
    if source_id not in sources:
        sys.exit(f"No such source: {source_id}")
    run = Run(source_id)

    if "--yes" not in flags and "--no" not in flags:
        print((run.folder / "review_card.md").read_text())
        return

    if not who:
        sys.exit("Say who is approving: --by <your name>")

    _decide(run, sources[source_id], "--yes" in flags, who, note)


if __name__ == "__main__":
    main()
