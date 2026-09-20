"""Stage A5 and A6: build the shortlist of 50, and the hand-check sheet.

What we keep:

  entity     one row is one business. Obviously keep.
  event      one row is a building approval, a contract, a prosecution. These
             name a business per row, and the assignment lists them as useful
             sources, so we keep them.
  aggregate  one row is a count or an average. No business to identify. Drop.
  unknown    the model could not tell. Drop, and say how many we dropped.

Ranking is by the model's confidence, with our rule score only as a tie-break.
The two numbers stay in separate columns. They measure different things and
blending them would hide which one was wrong.

Run:
    python -m src.shortlist
"""

from __future__ import annotations

import csv
import json
import random
import re
import sys

from . import config

KEEP_GRAINS = {"entity", "event"}
HANDCHECK_SAMPLE = 20
HANDCHECK_SEED = 20260919        # fixed, so the sample is reproducible

COLUMNS = [
    "rank", "dataset_id", "title", "organisation", "formats",
    "confidence", "reason", "record_grain", "likely_fields",
    "rule_score", "licence", "found_by", "modified", "landing_page",
    "download_format", "resource_name", "download_url",
]


# Which file to hand to Part 2, in order of how little work it takes to read.
# A dataset often ships a PDF help file first and the real data second, so
# "the first resource" is the wrong answer.
FORMAT_PREFERENCE = ["CSV", "TSV", "XLSX", "XLS", "JSON", "GEOJSON", "XML", "ZIP"]


# Files that describe the data rather than being the data. A dataset often
# ships "Company Dataset - Help File.pdf" and "bulkextract.xsd" next to the
# real records, and both are machine-readable formats.
DOCUMENTATION = re.compile(
    r"schema|help|dictionary|readme|guide|notes|glossary|metadata|"
    r"specification|\.xsd|licence agreement|user manual|"
    r"resource list|file list|list of files|index of|"
    r"codeset|code set|code list|lookup|reference data|taxonomy", re.I)


def best_resource(resources: list[dict]) -> dict:
    """The most readable file in a dataset, not just the first one listed."""
    from .formats import normalise
    ranked = []
    for resource in resources:
        if not resource.get("url"):
            continue
        token = normalise(resource.get("format"))
        if token not in FORMAT_PREFERENCE:
            continue
        looks_like_docs = bool(DOCUMENTATION.search(
            f"{resource.get('name', '')} {resource.get('url', '')}"))
        # Documentation sorts last whatever its format, so a .xsd never beats
        # the zip holding the actual records.
        ranked.append((looks_like_docs, FORMAT_PREFERENCE.index(token), resource))
    if not ranked:
        return {}
    ranked.sort(key=lambda row: (row[0], row[1]))
    return ranked[0][2]


def _row_for_output(row: dict, rank: int) -> dict:
    chosen = best_resource(row["resources"])
    return {
        "rank": rank,
        "dataset_id": row["dataset_id"],
        "title": row["title"],
        "organisation": row["organisation_title"],
        "formats": "|".join(row["readable_formats"]),
        "confidence": row["model_confidence"],
        "reason": row["model_reason"],
        "record_grain": row["model_grain"],
        "likely_fields": "|".join(row["model_likely_fields"]),
        "rule_score": row["rule_score"],
        "licence": row["licence_title"] or row["licence_id"] or "unknown",
        "found_by": row["found_by"],
        # When the publisher last changed this dataset. The ontology needs
        # observed_at, "when the source stated it", and for a source with no
        # date column this is the best answer we have.
        "modified": row.get("modified") or "",
        "landing_page": row["landing_page"],
        "download_url": chosen.get("url", ""),
        "download_format": chosen.get("format", ""),
        "resource_name": chosen.get("name", ""),
    }


def main() -> None:
    settings = config.load()["catalogue"]
    wanted = settings["shortlist_size"]

    in_file = config.path("interim") / "triaged.jsonl"
    if not in_file.exists():
        sys.exit("No triaged.jsonl. Run: python -m src.triage")

    rows = [json.loads(line) for line in open(in_file)]

    kept = [r for r in rows if r["model_grain"] in KEEP_GRAINS]
    dropped: dict[str, int] = {}
    for r in rows:
        if r["model_grain"] not in KEEP_GRAINS:
            dropped[r["model_grain"]] = dropped.get(r["model_grain"], 0) + 1

    # Model confidence first, our rule score only to break ties.
    kept.sort(key=lambda r: (r["model_confidence"], r["rule_score"]), reverse=True)
    shortlist = kept[:wanted]

    if len(kept) < wanted:
        print(f"WARNING: only {len(kept)} datasets survived, wanted {wanted}")

    out = config.path("outputs")
    records = [_row_for_output(r, i) for i, r in enumerate(shortlist, 1)]

    with open(out / "shortlist.jsonl", "w") as f:
        for record in records:
            f.write(json.dumps(record) + "\n")

    with open(out / "shortlist.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(records)

    # A6. Twenty at random, fixed seed, for a human to check by hand. The
    # verdict column is left blank on purpose. This is the one number the
    # assignment says it wants honest rather than high.
    sample = random.Random(HANDCHECK_SEED).sample(records, HANDCHECK_SAMPLE)
    sample.sort(key=lambda r: r["rank"])
    check_file = out / "shortlist_handcheck.csv"
    if check_file.exists():
        print(f"\n{check_file.name} already exists, left alone so your "
              f"verdicts are not overwritten")
    else:
        with open(check_file, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "rank", "dataset_id", "title", "landing_page", "download_url",
                "model_confidence", "model_reason",
                "verdict_yes_no", "what_you_saw"])
            writer.writeheader()
            for r in sample:
                writer.writerow({
                    "rank": r["rank"], "dataset_id": r["dataset_id"],
                    "title": r["title"], "landing_page": r["landing_page"],
                    "download_url": r["download_url"],
                    "model_confidence": r["confidence"],
                    "model_reason": r["reason"],
                    "verdict_yes_no": "", "what_you_saw": "",
                })

    print(f"Judged: {len(rows)}")
    for grain, count in sorted(dropped.items(), key=lambda kv: -kv[1]):
        print(f"  dropped as {grain}: {count}")
    print(f"Kept (entity or event): {len(kept)}")
    print(f"Shortlist: {len(shortlist)}")
    print()
    print(f"Confidence range on the shortlist: "
          f"{shortlist[-1]['model_confidence']:.2f} to "
          f"{shortlist[0]['model_confidence']:.2f}")
    grains: dict[str, int] = {}
    for r in shortlist:
        grains[r["model_grain"]] = grains.get(r["model_grain"], 0) + 1
    print(f"Shortlist by grain: {grains}")
    formats: dict[str, int] = {}
    for r in shortlist:
        for fmt in r["readable_formats"]:
            formats[fmt] = formats.get(fmt, 0) + 1
    print(f"Shortlist by format: {dict(sorted(formats.items(), key=lambda kv: -kv[1]))}")
    print()
    print(f"Written: {out/'shortlist.csv'}")
    print(f"         {out/'shortlist.jsonl'}")
    print(f"         {check_file}   <- fill in verdict_yes_no by hand")
    print()
    print("Top 12:")
    for r in records[:12]:
        print(f"  {r['rank']:2d}. {r['confidence']:.2f} {r['record_grain']:9} "
              f"{r['title'][:52]}")


if __name__ == "__main__":
    main()
