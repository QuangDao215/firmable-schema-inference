"""Stage A6: check 20 of the 50 shortlisted datasets against their real data.

The triage model in stage A4 judged from metadata: a title, a description, some
file names. This step does not. It downloads the actual file, reads the real
column names and the first rows, and asks whether a business is named in there.

Three things make the answer defensible:

1. It reads records, not descriptions. A dataset that sounds like a business
   register and turns out to hold totals is caught here and nowhere else.
2. Deterministic evidence is counted before any model sees the file: how many
   values look like an ABN, how many cells contain "Pty Ltd", which columns are
   named like an entity. Those counts go in the output and can be checked by
   hand.
3. The checker is a different model from the one that made the original
   judgment, and it is never told what that judgment was.

It is still a model. The output says so, on every row.

Run:
    python -m src.handcheck
"""

from __future__ import annotations

import csv
import hashlib
import json
import sys
from pathlib import Path

from . import config, fetchfile, llm

SCHEMA = {
    "type": "object",
    "properties": {
        "contains_business_entities": {"type": "boolean"},
        "confidence": {"type": "number"},
        "entity_columns": {"type": "array", "items": {"type": "string"}},
        "example_entity": {"type": "string"},
        "what_a_row_is": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["contains_business_entities", "confidence", "entity_columns",
                 "example_entity", "what_a_row_is", "reason"],
}

PROMPT = """Below is real data from an Australian government dataset: the actual
column names and first rows of the file, not a description of it.

Answer one question. Does this file contain IDENTIFIABLE AUSTRALIAN BUSINESS
ENTITIES? That means a row names a specific company, sole trader, licence
holder, contractor or supplier, in a way that could be looked up.

Say false when:
- rows are counts, totals or averages rather than named organisations
- the only names are people, suburbs, schools, government departments or assets
- the file holds addresses, coordinates or reference codes and no organisation

Say true when a row names a specific business, even if the file is about
something else, such as a building approval that names the builder.

contains_business_entities  true or false
confidence                  0 to 1
entity_columns              the exact column names that carry the business name
                            or identifier. Empty list if there are none.
example_entity              one business name copied exactly from the data.
                            Empty string if there are none.
what_a_row_is               a few words: what one row of this file represents
reason                      one sentence, under 25 words

Dataset title: {title}
Publisher: {publisher}
File: {filename}
What the bytes say it is: {sniffed}

Data:
{data}
"""


def _render(view: dict, limit: int = 2200) -> str:
    """Turn a preview into something readable, whatever the file was."""
    if view.get("kind") == "table":
        lines = ["COLUMNS: " + " | ".join(str(c) for c in view.get("columns", []))]
        for row in view.get("rows", [])[:6]:
            lines.append("ROW: " + " | ".join(str(c) for c in row))
        return "\n".join(lines)[:limit]
    if view.get("kind") == "records":
        return json.dumps(view.get("records", [])[:5], indent=1,
                          default=str)[:limit]
    if view.get("kind") in ("xml", "zip_json"):
        return (f"top tags: {view.get('top_tags')}\n\n"
                + str(view.get("head", ""))[:limit])
    if view.get("kind") == "zip":
        return "archive containing: " + ", ".join(view.get("files", []))
    return json.dumps(view, default=str)[:limit]


def main() -> None:
    settings = config.load()
    # A different model from the one that made the original judgment.
    model = settings["models"]["mapping"]

    sheet = config.path("outputs") / "shortlist_handcheck.csv"
    if not sheet.exists():
        sys.exit("No shortlist_handcheck.csv. Run: python -m src.shortlist")

    rows = list(csv.DictReader(open(sheet)))
    cache_dir = config.ROOT / "data" / "raw" / "handcheck"
    cache_dir.mkdir(parents=True, exist_ok=True)

    print(f"Checking {len(rows)} datasets against their real files, "
          f"using {model}\n")

    results = []
    for i, row in enumerate(rows, 1):
        url = row["download_url"]
        title = row["title"][:46]
        print(f"{i:2d}. {title:46}", end=" ", flush=True)

        if not url:
            print("no download url")
            results.append({**row, "checked": False,
                            "problem": "no download url"})
            continue

        # Cache by url, not by dataset id. When the shortlist picks a
        # different file for a dataset, the old download must not be reused.
        stamp = hashlib.sha1(url.encode()).hexdigest()[:10]
        target = cache_dir / f"{row['dataset_id']}_{stamp}.bin"
        got = target.exists() and {"ok": True, "path": str(target)} or \
            fetchfile.download(url, target)
        if not got.get("ok"):
            print(f"download failed: {got.get('error', '')[:40]}")
            results.append({**row, "checked": False,
                            "problem": f"download failed: {got.get('error')}"})
            continue

        view = fetchfile.preview(target)
        signals = fetchfile.business_signals(view)

        # A zip cannot be opened from a partial download, because its index
        # sits at the end of the file. Say so rather than guessing.
        if view.get("kind") == "truncated_zip":
            print("zip larger than our 8 MB sample cap, cannot open")
            results.append({**row, "checked": False,
                            "problem": "zip exceeds sample cap, not checkable"})
            continue

        if view.get("kind") in ("pdf", "html", "error", "empty"):
            print(f"not readable data ({view.get('kind')})")
            results.append({**row, "checked": False,
                            "problem": f"file is {view.get('kind')}",
                            **{f"signal_{k}": v for k, v in signals.items()}})
            continue

        out = llm.ask(model, PROMPT.format(
            title=row["title"], publisher="", filename=Path(url).name,
            sniffed=view.get("sniffed"), data=_render(view)),
            schema=SCHEMA, step=f"handcheck_{i}")
        verdict = out["data"]

        mark = "YES" if verdict["contains_business_entities"] else "NO "
        print(f"{mark}  {verdict['what_a_row_is'][:34]:34} "
              f"[Pty Ltd x{signals['company_word_hits']}, "
              f"ABN-shaped x{signals['abn_shaped_values']}]")

        results.append({
            **row, "checked": True, "problem": "",
            "verdict_yes_no": "yes" if verdict["contains_business_entities"] else "no",
            "checker_confidence": verdict["confidence"],
            "what_a_row_is": verdict["what_a_row_is"],
            "entity_columns": "|".join(verdict["entity_columns"]),
            "example_entity": verdict["example_entity"],
            "what_you_saw": verdict["reason"],
            "sniffed_format": view.get("sniffed"),
            **{f"signal_{k}": (json.dumps(v) if isinstance(v, list) else v)
               for k, v in signals.items()},
        })

    checked = [r for r in results if r.get("checked")]
    yes = [r for r in checked if r.get("verdict_yes_no") == "yes"]
    unreadable = [r for r in results if not r.get("checked")]

    out_file = config.path("outputs") / "shortlist_handcheck_results.csv"
    fields = sorted({k for r in results for k in r})
    with open(out_file, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(results)

    print()
    print("=" * 62)
    print("False positive rate on the shortlist")
    print("=" * 62)
    print(f"  sampled                  {len(results)}")
    print(f"  file could be read       {len(checked)}")
    print(f"  could not be read        {len(unreadable)}")
    print(f"  contained businesses     {len(yes)}")
    if checked:
        print(f"  precision, readable only {len(yes)}/{len(checked)}"
              f" = {len(yes)/len(checked):.0%}")
    print(f"  precision, counting unreadable as wrong "
          f"{len(yes)}/{len(results)} = {len(yes)/len(results):.0%}")
    print()
    print("  How this number was produced: a second model read the real")
    print("  columns and rows of each file, not its description, and was")
    print("  never shown the original judgment. Deterministic counts of")
    print("  'Pty Ltd' and ABN-shaped values are in the output beside every")
    print("  verdict, so any row can be checked by hand.")
    print()
    print(f"Written to {out_file}")
    print(f"Spend so far: {json.dumps(llm.spend_so_far())}")


if __name__ == "__main__":
    main()
