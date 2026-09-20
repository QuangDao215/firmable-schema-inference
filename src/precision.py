"""Part 3: measure how many proposed links are actually correct.

Two commands:

    python -m src.precision prepare   write the sampled pairs for a reviewer
    python -m src.precision merge     read the verdicts back and report

The reviewer is deliberately outside this pipeline. The matcher is rules, the
extraction was Gemini, and letting the same model family grade its own
family's work is not a check. The verdicts file records who judged.

Three samples, because one random sample of 50 would not tell you much.

  headline   50 links drawn at random from everything at or above the
             threshold. This is the number the assignment asks for.
  weak tier  the name-and-geography links, which carry no identifier and are
             where errors will actually be. A random 50 would contain about
             two of them.
  loose      links we refused for being below the threshold, so we can say
             what a looser cut would have bought.

The checker is a model reading both records side by side. It is not told which
method matched them, what confidence we assigned, or that we linked them at
all. Beside every verdict we record deterministic facts anyone can check: do
the ABNs match, do the normalised names match, do the addresses agree.

Run:
    python -m src.precision
"""

from __future__ import annotations

import csv
import json
import random
import sys
from collections import defaultdict

from . import config, llm, names
from .match import LOOSE_THRESHOLD, THRESHOLD

SEED = 20260920
HEADLINE_SAMPLE = 50
WEAK_SAMPLE = 50
LOOSE_SAMPLE = 50
BATCH = 10

SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "pair_id": {"type": "string"},
                    "same_business": {
                        "type": "string",
                        "enum": ["yes", "no", "cannot_tell"],
                    },
                    "reason": {"type": "string"},
                },
                "required": ["pair_id", "same_business", "reason"],
            },
        }
    },
    "required": ["results"],
}

PROMPT = """Below are pairs of records about Australian organisations, taken
from different government datasets.

For each pair, decide whether the two records describe THE SAME BUSINESS.

same_business
  "yes"          the same legal entity
  "no"           different entities, including a parent and its subsidiary,
                 two schools with the same name in different states, or a
                 person and a company
  "cannot_tell"  not enough in the records to decide

reason  one sentence, under 20 words

Things that matter:
- An Australian Business Number identifies one entity. Two records with the
  same valid ABN are the same entity.
- The same name does NOT mean the same entity. Australia has many
  organisations sharing a name across states.
- A subsidiary is not its parent, even when the names are similar.

Copy pair_id back exactly.

Pairs:
{pairs}
"""


def observations_by_id() -> dict:
    index = {}
    folder = config.path("outputs") / "observations_deep"
    for f in sorted(folder.glob("*.jsonl")):
        for line in open(f):
            row = json.loads(line)
            index[(row["source_id"], row["source_record_id"])] = row
    return index


def _side(row: dict) -> dict:
    entity, address = row.get("entity") or {}, row.get("address") or {}
    return {
        "dataset": row["source_id"].rsplit("-", 1)[0],
        **{k: v for k, v in entity.items()},
        **{f"address_{k}": v for k, v in address.items()},
        "observed_at": (row.get("observed_at") or "")[:10],
    }


def facts(a: dict, b: dict) -> dict:
    """Deterministic evidence, computed before any model sees the pair."""
    ea, eb = a.get("entity") or {}, b.get("entity") or {}
    aa, ab = a.get("address") or {}, b.get("address") or {}
    return {
        "abn_equal": bool(ea.get("abn") and ea.get("abn") == eb.get("abn")),
        "acn_equal": bool(ea.get("acn") and ea.get("acn") == eb.get("acn")),
        "name_equal_normalised": (names.normalise(ea.get("legal_name"))
                                  == names.normalise(eb.get("legal_name"))
                                  and bool(ea.get("legal_name"))),
        "state_conflict": bool(aa.get("state") and ab.get("state")
                               and aa["state"] != ab["state"]),
        "postcode_equal": bool(aa.get("postcode")
                               and aa.get("postcode") == ab.get("postcode")),
    }


def as_pair(pair: dict, index: dict, label: str) -> dict:
    """One pair, as a reviewer needs to see it. No hint of our own verdict."""
    a = index[(pair["source_a_record"]["source_id"],
               pair["source_a_record"]["source_record_id"])]
    b = index[(pair["source_b_record"]["source_id"],
               pair["source_b_record"]["source_record_id"])]
    return {
        "pair_id": pair["pair_id"],
        "sample": label,
        "record_a": _side(a),
        "record_b": _side(b),
        # Deterministic facts, computed before anyone looks. They are here so
        # a verdict can be audited without trusting the reviewer.
        "facts": facts(a, b),
        # Kept for the merge step, not shown in the question.
        "_method": pair["evidence"]["method"],
        "_our_confidence": pair["confidence"],
        "_canonical_entity_key": pair["canonical_entity_key"],
    }


def summarise(checked: list[dict], label: str, threshold: float) -> dict:
    rows = [c for c in checked if c["sample"] == label]
    yes = sum(1 for c in rows if c["verdict"] == "yes")
    no = sum(1 for c in rows if c["verdict"] == "no")
    unsure = sum(1 for c in rows if c["verdict"] == "cannot_tell")
    decided = yes + no
    return {
        "sample": label, "threshold": threshold, "checked": len(rows),
        "correct": yes, "wrong": no, "cannot_tell": unsure,
        "precision_strict": round(yes / len(rows), 3) if rows else 0,
        "precision_of_decided": round(yes / decided, 3) if decided else 0,
    }


QUESTION = """For each pair below, decide whether the two records describe THE
SAME BUSINESS.

  "yes"          the same legal entity
  "no"           different entities. A parent and its subsidiary are different.
                 A foundation and its college are different. Two schools with
                 the same name in different states are different. A person and
                 a company are different.
  "cannot_tell"  not enough in the records to decide

An Australian Business Number identifies one entity: two records with the same
valid ABN are the same entity. The same NAME does not mean the same entity.
"""


def prepare() -> None:
    links_file = config.path("outputs") / "links.jsonl"
    unlinked_file = config.path("outputs") / "unlinked.jsonl"
    if not links_file.exists():
        sys.exit("No links.jsonl. Run: python -m src.match")

    links = [json.loads(line) for line in open(links_file)]
    unlinked = [json.loads(line) for line in open(unlinked_file)]
    for i, row in enumerate(links):
        row["pair_id"] = f"L{i:05d}"
    for i, row in enumerate(unlinked):
        row["pair_id"] = f"U{i:05d}"

    rng = random.Random(SEED)
    headline = rng.sample(links, min(HEADLINE_SAMPLE, len(links)))
    weak = [r for r in links if r["evidence"]["method"] != "abn_exact"]
    weak = rng.sample(weak, min(WEAK_SAMPLE, len(weak)))
    loose = [r for r in unlinked
             if r["would_have_been"] >= LOOSE_THRESHOLD
             and r["refused_because"][0].startswith("confidence")]
    loose = rng.sample(loose, min(LOOSE_SAMPLE, len(loose)))

    index = observations_by_id()
    pairs = []
    for label, chosen in (("headline", headline), ("weak_tier", weak),
                          ("below_threshold", loose)):
        pairs.extend(as_pair(p, index, label) for p in chosen)

    out = config.path("outputs") / "precision_pairs.json"
    out.write_text(json.dumps({
        "question": QUESTION,
        "seed": SEED,
        "matcher_version": links[0]["matcher_version"],
        "answer_format": {"pair_id": "copy exactly",
                          "same_business": "yes | no | cannot_tell",
                          "reason": "one sentence, under 20 words"},
        "write_answers_to": "outputs/precision_verdicts.json",
        "pairs": pairs,
    }, indent=1, default=str))

    print(f"  {len(pairs)} pairs written to {out}")
    for label in ("headline", "weak_tier", "below_threshold"):
        print(f"    {label:17} {sum(1 for p in pairs if p['sample'] == label)}")
    print("\n  A reviewer answers into outputs/precision_verdicts.json, then:")
    print("    python -m src.precision merge")


def merge() -> None:
    pairs_file = config.path("outputs") / "precision_pairs.json"
    verdicts_file = config.path("outputs") / "precision_verdicts.json"
    if not verdicts_file.exists():
        sys.exit(f"No verdicts at {verdicts_file}. Run prepare first, then "
                 f"have a reviewer answer.")

    pairs = json.loads(pairs_file.read_text())["pairs"]
    answers = json.loads(verdicts_file.read_text())
    reviewer = answers.get("reviewed_by", "unrecorded")
    by_id = {a["pair_id"]: a for a in answers["verdicts"]}

    checked = []
    for pair in pairs:
        answer = by_id.get(pair["pair_id"], {})
        checked.append({
            "sample": pair["sample"],
            "pair_id": pair["pair_id"],
            "method": pair["_method"],
            "our_confidence": pair["_our_confidence"],
            "canonical_entity_key": pair["_canonical_entity_key"],
            "name_a": pair["record_a"].get("legal_name", ""),
            "name_b": pair["record_b"].get("legal_name", ""),
            "source_a": pair["record_a"].get("dataset", ""),
            "source_b": pair["record_b"].get("dataset", ""),
            "verdict": answer.get("same_business", "no_answer"),
            "checker_reason": answer.get("reason", ""),
            "reviewed_by": reviewer,
            **{f"fact_{k}": v for k, v in pair["facts"].items()},
        })

    out = config.path("outputs") / "links_precision.csv"
    fields = sorted({k for c in checked for k in c})
    with open(out, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(checked)

    summaries = [summarise(checked, "headline", THRESHOLD),
                 summarise(checked, "weak_tier", 0.75),
                 summarise(checked, "below_threshold", LOOSE_THRESHOLD)]
    (config.path("outputs") / "links_precision.json").write_text(
        json.dumps({"seed": SEED, "reviewed_by": reviewer,
                    "summaries": summaries}, indent=1))

    print("=" * 68)
    print(f"Link precision, reviewed by {reviewer}")
    print("=" * 68)
    for s in summaries:
        print(f"  {s['sample']:17} threshold {s['threshold']:<5} "
              f"checked {s['checked']:3d}  correct {s['correct']:3d}  "
              f"wrong {s['wrong']:3d}  unsure {s['cannot_tell']:3d}  "
              f"precision {s['precision_strict']:.0%}")

    by_method: dict[str, list] = defaultdict(list)
    for c in checked:
        by_method[c["method"]].append(c["verdict"])
    print("\n  by matching method, across all three samples:")
    for method, verdicts in sorted(by_method.items()):
        yes = verdicts.count("yes")
        print(f"    {method:18} {yes:3d}/{len(verdicts):3d} correct "
              f"= {yes / len(verdicts):.0%}")

    wrong = [c for c in checked if c["verdict"] == "no"]
    print(f"\n  called wrong: {len(wrong)}")
    for c in wrong[:12]:
        print(f"    [{c['method']}] {str(c['name_a'])[:30]:30} | "
              f"{str(c['name_b'])[:30]:30}")
        print(f"        {c['checker_reason'][:76]}")
    print(f"\n  written to {out}")


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "prepare":
        prepare()
    elif command == "merge":
        merge()
    else:
        sys.exit("Usage: python -m src.precision [prepare|merge]")


if __name__ == "__main__":
    main()
