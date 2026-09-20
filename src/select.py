"""Stage B: probe all 50 shortlisted datasets, then pick six for Part 2.

No model calls. This is the programmed check the agent depends on.

Two steps:

  probe   Download the head of every shortlisted file and find out what it
          really is. About a third of catalogue links do not serve a file on
          demand, so this is where that gets measured rather than assumed.

  pick    Choose six that are as different from each other as we can manage:
          different publishers, different formats, different ontology fields,
          and at least one that is not a clean CSV.

The selection rule is written down rather than chosen by taste, because the
assignment asks whether the solution generalises.

Run:
    python -m src.select probe
    python -m src.select pick
    python -m src.select pick --pinned     use the fixed six, skip discovery

The default path discovers its own six from a live catalogue, so a rerun on a
different day can legitimately pick different datasets. That is the system
working, but it makes a result hard to reproduce. `--pinned` reads
`config/pinned_sources.json` and uses the exact six this submission was built
on, so everything from Part 2 onward is repeatable.
"""

from __future__ import annotations

import csv
import hashlib
import json
import sys
from pathlib import Path

import requests

from . import config, fetchfile

PROBE_BYTES = 3_000_000
PROBE_CACHE = config.ROOT / "data" / "raw" / "probe"
WANTED = 6

# A clean CSV is the easy case. The assignment asks for at least one source
# that is not one.
NOT_PLAIN_CSV = {"XLSX", "XLS", "JSON", "JSONL", "XML", "ZIP", "TSV"}


def _candidate_resources(row: dict, full: dict) -> list[dict]:
    """The files worth probing for one dataset.

    The shortlist picked the easiest file, which is nearly always a CSV. For
    Part 2 we also need sources that are not clean CSVs, and many datasets
    publish the same records as XLSX, JSON, XML or a zip. So we probe the
    easiest file plus one alternative per other format, and let the selector
    choose which one it wants.
    """
    from .formats import MACHINE_READABLE, normalise
    from .shortlist import DOCUMENTATION

    chosen = [{"url": row["download_url"], "format": row["download_format"],
               "name": row["resource_name"], "is_primary": True}]
    seen_formats = {normalise(row["download_format"])}

    for resource in full.get("resources", []):
        if not resource.get("url"):
            continue
        token = normalise(resource.get("format"))
        if token not in MACHINE_READABLE or token in seen_formats:
            continue
        if DOCUMENTATION.search(f"{resource.get('name','')} {resource['url']}"):
            continue
        seen_formats.add(token)
        chosen.append({"url": resource["url"], "format": resource.get("format"),
                       "name": resource.get("name"), "is_primary": False})
    return chosen


def probe() -> None:
    sheet = config.path("outputs") / "shortlist.csv"
    if not sheet.exists():
        sys.exit("No shortlist.csv. Run: python -m src.shortlist")

    shortlist = list(csv.DictReader(open(sheet)))
    full_by_id = {json.loads(l)["dataset_id"]: json.loads(l)
                  for l in open(config.path("interim") / "triaged.jsonl")}

    rows = []
    for row in shortlist:
        full = full_by_id.get(row["dataset_id"], {})
        for resource in _candidate_resources(row, full):
            rows.append({**row, "download_url": resource["url"],
                         "download_format": resource["format"] or "",
                         "resource_name": resource["name"] or "",
                         "is_primary_resource": resource["is_primary"]})

    PROBE_CACHE.mkdir(parents=True, exist_ok=True)
    results = []

    print(f"Probing {len(rows)} files across {len(shortlist)} datasets\n")
    for row in rows:
        url = row["download_url"]
        tag = "" if row["is_primary_resource"] else f" [{row['download_format']}]"
        print(f"{int(row['rank']):2d}. {(row['title'][:40] + tag)[:48]:48}",
              end=" ", flush=True)

        if not url:
            print("no url")
            results.append({**row, "usable": False, "problem": "no download url"})
            continue

        stamp = hashlib.sha1(url.encode()).hexdigest()[:10]
        target = PROBE_CACHE / f"{row['dataset_id']}_{stamp}.bin"
        got = ({"ok": True} if target.exists()
               else fetchfile.download_for_preview(url, target, PROBE_BYTES))

        if not got.get("ok"):
            print(f"{got.get('error', '')[:44]}")
            results.append({**row, "usable": False,
                            "problem": got.get("error", "download failed")})
            continue

        view = fetchfile.preview(target, rows=12)
        signals = fetchfile.business_signals(view)
        kind = view.get("kind")

        columns = view.get("columns") or [t for t, _ in view.get("top_tags", [])]
        # A file that parses to one or two columns has not really parsed. It
        # is usually the wrong delimiter or a title row we did not skip.
        usable = kind in ("table", "records", "xml") and len(columns) >= 3

        if not usable:
            print(f"not usable ({kind}, {len(columns)} cols)")
        else:
            print(f"{view.get('sniffed'):5} {len(columns):3d} cols  "
                  f"[PtyLtd x{signals['company_word_hits']}, "
                  f"ABN x{signals['abn_shaped_values']}]")

        results.append({
            **row,
            "usable": usable,
            "problem": "" if usable else f"file is {kind}",
            "real_format": view.get("sniffed", ""),
            "preview_kind": kind,
            "column_count": len(columns),
            "columns": json.dumps(columns[:40]),
            "inner_file": view.get("inner_file", ""),
            "local_path": str(target),
            **{f"signal_{k}": (json.dumps(v) if isinstance(v, list) else v)
               for k, v in signals.items()},
        })

    out = config.path("interim") / "probed.jsonl"
    with open(out, "w") as f:
        for r in results:
            f.write(json.dumps(r) + "\n")

    usable = [r for r in results if r["usable"]]
    datasets_ok = {r["dataset_id"] for r in usable}
    print(f"\nUsable files: {len(usable)} of {len(results)}")
    print(f"Datasets with at least one usable file: {len(datasets_ok)} of "
          f"{len({r['dataset_id'] for r in results})}")
    formats: dict[str, int] = {}
    for r in usable:
        formats[r["real_format"]] = formats.get(r["real_format"], 0) + 1
    print(f"Real formats: {dict(sorted(formats.items(), key=lambda kv: -kv[1]))}")
    print(f"Written to {out}")
    print("Run: python -m src.select pick")


# --------------------------------------------------------------------------
# picking six
# --------------------------------------------------------------------------

def _diversity_key(row: dict) -> tuple:
    return (row["organisation"], row["real_format"], row["record_grain"])


def pick_pinned() -> None:
    """Use the fixed six instead of discovering them.

    Everything downstream reads outputs/selected_sources.json, so this writes
    the same file. Only how the six were chosen differs.
    """
    pinned_file = config.ROOT / "config" / "pinned_sources.json"
    if not pinned_file.exists():
        sys.exit(f"No {pinned_file}")

    pinned = json.loads(pinned_file.read_text())
    chosen = pinned["selected"]

    print(f"Using the pinned six from {pinned_file.name} "
          f"(pinned {pinned['pinned_on']})\n")
    print(f"  {'#':2} {'fmt':6} {'grain':7}  {'publisher':34} title")
    for i, r in enumerate(chosen, 1):
        print(f"  {i:2d} {r['real_format']:6} {r['record_grain']:7}  "
              f"{(r['organisation'] or '')[:34]:34} {r['title'][:38]}")

    # A pinned URL is still a live government link. Check each one serves
    # something before the agent spends money finding out.
    print("\n  checking the pinned links still serve a file:")
    broken = []
    for r in chosen:
        reply = requests.head(r["download_url"], timeout=30,
                              allow_redirects=True,
                              headers={"User-Agent": fetchfile.USER_AGENT})
        ok = reply.status_code in (200, 202, 302, 405)
        print(f"    {'ok  ' if ok else 'DEAD'}  HTTP {reply.status_code}  "
              f"{r['title'][:44]}")
        if not ok:
            broken.append(r["title"])

    out = config.path("outputs") / "selected_sources.json"
    out.write_text(json.dumps({
        "rule": ["Pinned. See config/pinned_sources.json."],
        "pinned": True,
        "pinned_on": pinned["pinned_on"],
        "links_checked_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%S"),
        "broken_links": broken,
        "selected": chosen,
    }, indent=1))

    if broken:
        print(f"\n  WARNING: {len(broken)} pinned link(s) no longer serve a "
              f"file. The agent will report the failure rather than skip it.")
    print(f"\n  Written to {out}")
    print(f"  Next: python -m src.agent.run")


def pick() -> None:
    in_file = config.path("interim") / "probed.jsonl"
    if not in_file.exists():
        sys.exit("No probed.jsonl. Run: python -m src.select probe")

    rows = [json.loads(line) for line in open(in_file)]
    usable = [r for r in rows if r["usable"]]

    # One dataset can have several usable files, such as a CSV and an XLSX of
    # the same records. Keep them all for now; the rule below decides which
    # one to take, because format spread is part of what we are selecting for.
    by_dataset: dict[str, list[dict]] = {}
    for row in usable:
        by_dataset.setdefault(row["dataset_id"], []).append(row)
    for variants in by_dataset.values():
        # Best parse first, so a fallback pick is never the broken one.
        variants.sort(key=lambda r: -r["column_count"])

    order = sorted(by_dataset.values(), key=lambda v: int(v[0]["rank"]))

    # The rule, in full:
    #
    #  1. Walk the shortlist in rank order, one dataset at a time.
    #  2. Skip a dataset whose publisher is already chosen. Six registers from
    #     one publisher would look diverse and would not be.
    #  3. From that dataset's usable files, take the one whose format we do
    #     not have yet. If they are all formats we have, take the best parse.
    #  4. Afterwards, make sure at least one pick is not a plain CSV, and at
    #     least one is event-shaped rather than a register of entities. Swap
    #     the lowest-ranked pick if not.
    chosen: list[dict] = []
    publishers: set[str] = set()
    formats: set[str] = set()

    for variants in order:
        if len(chosen) >= WANTED:
            break
        if variants[0]["organisation"] in publishers:
            continue
        fresh = [v for v in variants if v["real_format"] not in formats]
        pick_this = fresh[0] if fresh else variants[0]
        chosen.append(pick_this)
        publishers.add(pick_this["organisation"])
        formats.add(pick_this["real_format"])

    def swap_in(test, why: str) -> None:
        """Replace the lowest-ranked pick to satisfy a requirement."""
        if any(test(r) for r in chosen):
            return
        for variants in order:
            for variant in variants:
                if test(variant) and variant["organisation"] not in publishers:
                    dropped = chosen.pop()
                    publishers.discard(dropped["organisation"])
                    chosen.append(variant)
                    publishers.add(variant["organisation"])
                    print(f"Swapped '{dropped['title'][:36]}' for "
                          f"'{variant['title'][:36]}' {why}\n")
                    return
        print(f"Could not satisfy: {why}\n")

    swap_in(lambda r: r["real_format"] in NOT_PLAIN_CSV,
            "to get a source that is not a plain CSV")
    swap_in(lambda r: r["record_grain"] == "event",
            "to get an event-shaped source, not only registers")

    out = config.path("outputs") / "selected_sources.json"
    out.write_text(json.dumps({
        "rule": [
            "Walk the shortlist in rank order, one dataset at a time.",
            "Skip a dataset whose publisher is already chosen.",
            "From its usable files, take a format we do not have yet.",
            "Ensure at least one pick is not a plain CSV.",
            "Ensure at least one pick is event-shaped, not only registers.",
        ],
        "usable_files": len(usable),
        "usable_datasets": len(by_dataset),
        "selected": chosen,
    }, indent=1))

    print(f"Six sources for Part 2, from {len(by_dataset)} usable datasets "
          f"({len(usable)} files):\n")
    print(f"  {'#':2} {'fmt':6} {'grain':7} {'cols':>4}  {'publisher':32} title")
    for i, r in enumerate(chosen, 1):
        print(f"  {i:2d} {r['real_format']:6} {r['record_grain']:7} "
              f"{r['column_count']:4d}  {(r['organisation'] or '')[:32]:32} "
              f"{r['title'][:38]}")

    print(f"\nSpread:")
    print(f"  publishers   : {len({r['organisation'] for r in chosen})} distinct")
    print(f"  formats      : {sorted({r['real_format'] for r in chosen})}")
    print(f"  grains       : {sorted({r['record_grain'] for r in chosen})}")
    print(f"  not plain CSV: "
          f"{sum(1 for r in chosen if r['real_format'] in NOT_PLAIN_CSV)}")
    print(f"  columns      : {sorted(r['column_count'] for r in chosen)}")
    print(f"\nWritten to {out}")


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "probe":
        probe()
    elif command == "pick":
        pick_pinned() if "--pinned" in sys.argv else pick()
    else:
        sys.exit("Usage: python -m src.select [probe|pick|pick --pinned]")
