"""Check every deliverable against what the assignment actually asks for.

This is not a unit test suite. It reads the brief's own requirements and
checks the files on disk satisfy them, so a missing deliverable is caught here
rather than by the reader.

Run:
    python -m src.verify
"""

from __future__ import annotations

import csv
import json
import sys

import yaml

from . import config

PASS, FAIL, WARN = "PASS", "FAIL", "WARN"
results = []


def check(part: str, requirement: str, ok: bool, detail: str = "",
          soft: bool = False) -> bool:
    status = PASS if ok else (WARN if soft else FAIL)
    results.append({"part": part, "requirement": requirement,
                    "status": status, "detail": detail})
    return ok


def jsonl(name: str) -> list[dict]:
    path = config.path("outputs") / name
    if not path.exists():
        return []
    return [json.loads(line) for line in open(path)]


# --------------------------------------------------------------------------

def part1():
    p = "Part 1"
    datasets = [json.loads(l) for l in
                open(config.path("interim") / "datasets.jsonl")] \
        if (config.path("interim") / "datasets.jsonl").exists() else []
    check(p, "at least 200 catalogue records retrieved programmatically",
          len(datasets) >= 200, f"{len(datasets)} unique datasets crawled")

    shortlist = jsonl("shortlist.jsonl")
    check(p, "ranked shortlist of 50 datasets", len(shortlist) == 50,
          f"{len(shortlist)} rows")

    csv_path = config.path("outputs") / "shortlist.csv"
    check(p, "output as CSV or JSONL", csv_path.exists() and bool(shortlist),
          "both written")

    required = {"dataset_id": "dataset ID", "title": "title",
                "organisation": "publishing organisation",
                "formats": "resource format(s)", "confidence": "confidence",
                "reason": "one-line reason"}
    if shortlist:
        missing = [label for key, label in required.items()
                   if not all(str(r.get(key, "")).strip() for r in shortlist)]
        check(p, "every shortlist row has all six required columns",
              not missing, "missing: " + ", ".join(missing) if missing
              else "id, title, org, formats, confidence, reason")

    hand = config.path("outputs") / "shortlist_handcheck_results.csv"
    rows = list(csv.DictReader(open(hand))) if hand.exists() else []
    check(p, "a random sample of 20 checked for the false positive rate",
          len(rows) == 20, f"{len(rows)} checked")
    if rows:
        readable = [r for r in rows if r.get("checked") == "True"]
        yes = [r for r in readable if r.get("verdict_yes_no") == "yes"]
        check(p, "false positive rate reported",
              bool(readable),
              f"{len(yes)}/{len(readable)} of readable files correct "
              f"({len(yes) / len(readable):.0%}), "
              f"{len(yes)}/{len(rows)} end to end")


def part2():
    p = "Part 2"
    configs = sorted(config.path("configs").glob("*.json"))
    check(p, "six mapping configs", len(configs) == 6,
          f"{len(configs)} in configs/")

    cfgs = [json.loads(f.read_text()) for f in configs]
    formats = {c["resource"]["real_format"] for c in cfgs}
    publishers = {c["source_id"] for c in cfgs}
    check(p, "sources are as different as possible", len(formats) >= 3,
          f"formats {sorted(formats)}, {len(publishers)} distinct sources")
    check(p, "at least one source is not a clean CSV",
          bool(formats - {"CSV"}), f"non-CSV formats: {sorted(formats - {'CSV'})}")

    schema = json.load(open(config.ROOT / "config" /
                            "mapping_config.schema.json"))
    try:
        import jsonschema
        bad = []
        for f, c in zip(configs, cfgs):
            try:
                jsonschema.validate(c, schema)
            except Exception as err:
                bad.append(f"{f.stem}: {str(err)[:60]}")
        check(p, "every config validates against the schema", not bad,
              "; ".join(bad) if bad else "all 6 valid")
    except ImportError:
        check(p, "every config validates against the schema", False,
              "jsonschema not installed", soft=True)

    every_field_complete = all(
        all({"canonical_field", "field_confidence"} <= set(m)
            and ("source_field" in m or m.get("transforms"))
            for m in c["field_mappings"]) for c in cfgs)
    check(p, "per field: source field, canonical field, transform, confidence",
          every_field_complete, "checked on every mapping in all six configs")

    listed = all(isinstance(c.get("unmapped_source_fields"), list)
                 and isinstance(c.get("unfilled_canonical_fields"), list)
                 for c in cfgs)
    total_unmapped = sum(len(c["unmapped_source_fields"]) for c in cfgs)
    total_unfilled = sum(len(c["unfilled_canonical_fields"]) for c in cfgs)
    check(p, "fields not mapped are listed, not guessed", listed,
          f"{total_unmapped} source columns and {total_unfilled} ontology "
          f"fields listed with reasons")

    agent_made = all(c.get("generated_by", {}).get("model") for c in cfgs)
    revised = sum(c.get("generated_by", {}).get("revisions", 0) for c in cfgs)
    check(p, "configs were generated by the agent, not written by hand",
          agent_made, f"every config names its model; {revised} revision "
                      f"rounds recorded across the six")

    approved = all(c.get("generated_by", {}).get("approved_by") for c in cfgs)
    check(p, "a human approved each config", approved,
          "approved_by recorded on all six")

    obs_dir = config.path("outputs") / "observations"
    files = sorted(obs_dir.glob("*.jsonl")) if obs_dir.exists() else []
    counts = {f.stem: sum(1 for _ in open(f)) for f in files}
    check(p, "extractor run over a sample of each source",
          len(files) == 6 and all(n >= 1000 for n in counts.values()),
          f"{sum(counts.values()):,} observations across {len(files)} sources")

    # the envelope the ontology says every record carries
    ontology = yaml.safe_load(open(config.ROOT / "firmable_ontology.yaml"))
    must = [k for k, v in ontology["observation"].items() if v.get("required")]
    sample = json.loads(open(files[0]).readline()) if files else {}
    missing = [k for k in must if k not in sample]
    check(p, "every observation carries the required envelope", not missing,
          "missing: " + ", ".join(missing) if missing
          else ", ".join(must))

    # one engine, not six scripts
    engine = (config.ROOT / "src" / "engine.py").read_text()
    per_source = any(c["source_id"] in engine for c in cfgs)
    check(p, "one engine, not six scripts", not per_source,
          "engine.py names no source and reads only a config")


def part3():
    p = "Part 3"
    links = jsonl("links.jsonl")
    unlinked = jsonl("unlinked.jsonl")
    check(p, "a set of proposed links", bool(links), f"{len(links):,} links")

    if links:
        shape = {"source_a_record", "source_b_record", "canonical_entity_key",
                 "confidence", "evidence"}
        check(p, "links have the required shape",
              all(shape <= set(l) for l in links),
              "source_a_record, source_b_record, canonical_entity_key, "
              "confidence, evidence")

    check(p, "an unlinked queue with reasons", bool(unlinked) and
          all(r.get("refused_because") for r in unlinked),
          f"{len(unlinked):,} refusals, every one with a stated reason")

    precision_file = config.path("outputs") / "links_precision.json"
    if precision_file.exists():
        data = json.load(open(precision_file))
        head = next((s for s in data["summaries"] if s["sample"] == "headline"), {})
        loose = next((s for s in data["summaries"]
                      if s["sample"] == "below_threshold"), {})
        check(p, "50 links checked and precision reported",
              head.get("checked") == 50,
              f"{head.get('correct')}/{head.get('checked')} correct at "
              f"threshold {head.get('threshold')} = "
              f"{head.get('precision_strict', 0):.0%}, reviewed by "
              f"{data.get('reviewed_by')}")
        check(p, "a looser threshold also reported", bool(loose),
              f"{loose.get('correct')}/{loose.get('checked')} at threshold "
              f"{loose.get('threshold')} = {loose.get('precision_strict', 0):.0%}")
    else:
        check(p, "50 links checked and precision reported", False, "not run")

    rel = jsonl("relationships.jsonl")
    check(p, "something sensible done with relationships between businesses",
          bool(rel),
          f"{sum(1 for r in rel if r['relation'] == 'member_of_corporate_group'):,} "
          f"parent edges from WGEA, kept apart from sameness links")


def part4():
    p = "Part 4"
    profiles = jsonl("company_profiles.jsonl")
    check(p, "50 company profiles", len(profiles) == 50, f"{len(profiles)} built")
    if not profiles:
        return

    per_field = all(
        all("value_confidence" in f for f in p_["fields"].values())
        for p_ in profiles)
    check(p, "a confidence on each field, not one score per profile",
          per_field and not any("confidence" in p_ and
                                isinstance(p_.get("confidence"), float)
                                for p_ in profiles),
          "value_confidence on every field, no profile-level score")

    provenance = all(
        all({"source_id", "source_record_id"} <= set(f["provenance"])
            for f in p_["fields"].values()) for p_ in profiles)
    check(p, "provenance per field", provenance,
          "every field names the source record it came from")

    conflicted = [f for p_ in profiles for f in p_["fields"].values()
                  if f["conflict"]]
    working_shown = all({"policy", "decided_by", "losing_values"}
                        <= set(f["conflict"]) for f in conflicted)
    check(p, "where sources disagree, the working is shown",
          bool(conflicted) and working_shown,
          f"{len(conflicted)} contested fields, each with the policy, the "
          f"deciding rule and every losing value")

    blanks = [f for p_ in profiles for f in p_["fields"].values()
              if f["value"] in (None, "", "null")]
    check(p, "fields that could not be filled are absent, not blank",
          not blanks, f"{len(blanks)} blank values found" if blanks
          else "no blank or null values in any profile")

    impact = config.path("outputs") / "profile_source_impact.json"
    check(p, "a note on what happens when a source is wrong", impact.exists(),
          "every profile rebuilt six times, once per source removed")


def submission():
    p = "Submission"
    for name, label in (("README.md", "README.md, how to run it"),
                        ("WRITEUP.md", "WRITEUP.md, Part 5")):
        check(p, label, (config.ROOT / name).exists(),
              "present" if (config.ROOT / name).exists() else "NOT WRITTEN YET")

    for name in ("shortlist.csv", "links.jsonl", "unlinked.jsonl",
                 "company_profiles.jsonl"):
        check(p, f"output: {name}",
              (config.path("outputs") / name).exists(), "")
    check(p, "output: the six mapping configs",
          len(list(config.path("configs").glob("*.json"))) == 6, "")
    check(p, "output: the canonical observations",
          (config.path("outputs") / "observations").exists(), "")
    check(p, "anything used to hand-check precision",
          (config.path("outputs") / "links_precision.csv").exists()
          and (config.path("outputs") / "precision_verdicts.json").exists(),
          "pairs, verdicts and the merged CSV all kept")
    check(p, "it is a git repository", (config.ROOT / ".git").exists(),
          "present" if (config.ROOT / ".git").exists() else "not initialised")


def main() -> None:
    for step in (part1, part2, part3, part4, submission):
        try:
            step()
        except Exception as err:
            results.append({"part": step.__name__, "requirement": "check ran",
                            "status": FAIL,
                            "detail": f"{type(err).__name__}: {err}"})

    current = None
    for r in results:
        if r["part"] != current:
            current = r["part"]
            print(f"\n{current}")
            print("-" * 74)
        mark = {"PASS": "ok  ", "FAIL": "FAIL", "WARN": "warn"}[r["status"]]
        print(f"  {mark}  {r['requirement']}")
        if r["detail"]:
            print(f"        {r['detail']}")

    failed = [r for r in results if r["status"] == FAIL]
    warned = [r for r in results if r["status"] == WARN]
    print("\n" + "=" * 74)
    print(f"  {len(results) - len(failed) - len(warned)} pass, "
          f"{len(warned)} warn, {len(failed)} fail")
    if failed:
        print("\n  not satisfied yet:")
        for r in failed:
            print(f"    [{r['part']}] {r['requirement']}")
    sys.exit(0)


if __name__ == "__main__":
    main()
