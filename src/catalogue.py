"""Stage A1 and A2: find dataset records on data.gov.au and flatten them.

We search on purpose. An engineer who knows the domain picks the terms, and
CKAN only returns datasets that ship a file we can actually parse. Nothing is
crawled in the hope that something useful turns up.

Two search channels, because they find different things:

  Channel 1, package_search. Free text over a dataset's title, description and
  tags. Finds "ASIC - Company Dataset".

  Channel 2, resource_search. Searches the names of the files inside a dataset.
  Finds a dataset called "Annual Report 2023" that happens to ship a file
  called "licensed_contractors.csv", which channel 1 would miss.

Both channels are filtered server-side to machine-readable formats.

Commands:
    python -m src.catalogue fetch      channels 1 and 2, the real pipeline
    python -m src.catalogue flatten    one tidy row per dataset
    python -m src.catalogue baseline   see below, not part of the pipeline

`baseline` crawls with no search terms at all, `q=*:*`. It exists only to
answer one question for the write-up: what would we have got if we had not
chosen keywords? Its output lives in a separate folder and `flatten` ignores
it. See DESIGN.md entry 13.

Fetching is safe to re-run. Cached pages are reused, so a crash costs one page.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import requests

from . import config

USER_AGENT = "firmable-assignment/0.1 (schema inference take-home)"


# --------------------------------------------------------------------------
# talking to CKAN
# --------------------------------------------------------------------------

def _call(base_url: str, action: str, params: dict,
          timeout: int, max_retries: int) -> dict:
    """One CKAN call. Retries a few times, then gives up."""
    last_error = None
    for attempt in range(max_retries):
        try:
            reply = requests.get(
                f"{base_url}/{action}", params=params, timeout=timeout,
                headers={"User-Agent": USER_AGENT},
            )
            reply.raise_for_status()
            body = reply.json()
            if not body.get("success"):
                raise RuntimeError(f"CKAN said success=false: {str(body)[:200]}")
            return body["result"]
        except Exception as err:              # network blips are normal
            last_error = err
            time.sleep(2 ** attempt)
    raise RuntimeError(f"{action} failed after {max_retries} tries: {last_error}")


def _collect(base_url: str, folder: Path, label: str, params_for: callable,
             wanted: int, settings: dict) -> int:
    """Page through one search until we have `wanted` records or run out.

    Pages are named by start offset and size, so a cached page is only reused
    when it is exactly the slice being asked for. Raising `wanted` later
    fetches the extra records instead of stopping early on a short page.
    """
    rows = settings["rows_per_page"]
    got = 0
    while got < wanted:
        asking_for = min(rows, wanted - got)
        out_file = folder / f"{label}__start{got:05d}_rows{asking_for:03d}.json"

        if out_file.exists():
            found = json.loads(out_file.read_text()).get("results", [])
        else:
            result = _call(base_url, "package_search", params_for(got, asking_for),
                           settings["timeout_seconds"], settings["max_retries"])
            found = result.get("results", [])
            out_file.write_text(json.dumps(result, indent=1))
            print(f"  {label}: +{len(found)} (total {got + len(found)} of {wanted})")
            time.sleep(settings["polite_delay_seconds"])

        got += len(found)
        if len(found) < asking_for:           # the catalogue ran out
            break
    return got


# --------------------------------------------------------------------------
# A1: fetch
# --------------------------------------------------------------------------

def _channel_one(settings: dict, folder: Path) -> int:
    """Free-text search over dataset titles, descriptions and tags."""
    base_url = settings["base_url"]
    fmt = settings["format_filter"]
    total = 0
    print(f"\nChannel 1: package_search, {len(settings['queries'])} queries "
          f"x {settings['records_per_query']} records, machine-readable only")
    for i, query in enumerate(settings["queries"]):
        label = f"pkg{i:02d}_" + query.replace(" ", "_")[:24]
        total += _collect(
            base_url, folder, label,
            lambda start, rows, q=query: {"q": q, "fq": fmt, "rows": rows, "start": start},
            settings["records_per_query"], settings)
    return total


def _channel_two(settings: dict, folder: Path) -> int:
    """Search the names of the files inside datasets, then fetch those datasets."""
    base_url = settings["base_url"]
    fmt = settings["format_filter"]
    limit = settings["resource_records_per_query"]

    print(f"\nChannel 2: resource_search, {len(settings['resource_queries'])} "
          f"queries x {limit} resources")

    package_ids: list[str] = []
    for query in settings["resource_queries"]:
        result = _call(base_url, "resource_search",
                       {"query": [query], "limit": limit},
                       settings["timeout_seconds"], settings["max_retries"])
        found = [r["package_id"] for r in result.get("results", []) if r.get("package_id")]
        print(f"  {query}: {len(found)} resources")
        package_ids.extend(found)
        time.sleep(settings["polite_delay_seconds"])

    # Resource hits are noisy. The same dataset shows up many times, so dedupe
    # before spending a lookup on it.
    unique_ids = list(dict.fromkeys(package_ids))
    print(f"  {len(package_ids)} resource hits -> {len(unique_ids)} unique datasets")

    # Look them up in batches of 30. One call per dataset would be 200 calls.
    total = 0
    for batch_number, start in enumerate(range(0, len(unique_ids), 30)):
        batch = unique_ids[start:start + 30]
        label = f"res_lookup{batch_number:02d}"
        out_file = folder / f"{label}__start00000_rows{len(batch):03d}.json"
        if out_file.exists():
            found = json.loads(out_file.read_text()).get("results", [])
        else:
            result = _call(
                base_url, "package_search",
                {"q": "*:*", "fq": f"id:({' OR '.join(batch)}) +{fmt}",
                 "rows": len(batch)},
                settings["timeout_seconds"], settings["max_retries"])
            found = result.get("results", [])
            out_file.write_text(json.dumps(result, indent=1))
            time.sleep(settings["polite_delay_seconds"])
        total += len(found)

    print(f"  {total} of those datasets also pass the format filter")
    return total


def fetch() -> None:
    settings = config.load()["catalogue"]
    folder = config.path("raw_catalogue")

    total = _channel_one(settings, folder)
    total += _channel_two(settings, folder)

    print(f"\nDone. {total} records saved to {folder}")
    print("Run: python -m src.catalogue flatten")


def baseline() -> None:
    """Crawl with no search terms. Not part of the pipeline.

    This answers one question for the write-up: what would we have found if we
    had skipped the keyword work and pulled an arbitrary slice of the
    catalogue? The answer is the comparison in DESIGN.md entry 13.
    """
    settings = config.load()["catalogue"]
    folder = config.ROOT / "data" / "raw" / "baseline"
    folder.mkdir(parents=True, exist_ok=True)

    print("Baseline crawl: q=*:* , no keywords, no format filter.")
    print("This is the comparison, not the pipeline.\n")
    got = _collect(settings["base_url"], folder, "broad",
                   lambda start, rows: {"q": "*:*", "sort": "id asc",
                                        "rows": rows, "start": start},
                   300, settings)
    print(f"\n{got} records in {folder}. Run: python -m src.baseline_report")


# --------------------------------------------------------------------------
# A2: flatten
# --------------------------------------------------------------------------

def _tidy_resource(resource: dict) -> dict:
    return {
        "resource_id": resource.get("id"),
        "name": (resource.get("name") or "").strip(),
        "format": (resource.get("format") or "").strip().upper(),
        "url": resource.get("url"),
        "size": resource.get("size"),
        "mimetype": resource.get("mimetype"),
        "last_modified": resource.get("last_modified") or resource.get("created"),
    }


def tidy_dataset(record: dict, found_by: str) -> dict:
    """One CKAN dataset record, reduced to the fields the pipeline uses.

    This is the only place we read raw CKAN shapes. Everything downstream reads
    the flat row instead.
    """
    organisation = record.get("organization") or {}
    resources = [_tidy_resource(r) for r in record.get("resources") or []]

    return {
        "dataset_id": record.get("id"),
        "name": record.get("name"),
        "title": (record.get("title") or "").strip(),
        "description": (record.get("notes") or "").strip(),
        "organisation": organisation.get("name"),
        "organisation_title": organisation.get("title"),
        "tags": [t.get("name") for t in record.get("tags") or [] if t.get("name")],
        # The ontology needs a licence on every observation, so we keep it from
        # the first step. Backfilling it later means crawling twice.
        "licence_id": record.get("license_id"),
        "licence_title": record.get("license_title"),
        "landing_page": f"https://data.gov.au/dataset/{record.get('name')}",
        "created": record.get("metadata_created"),
        "modified": record.get("metadata_modified"),
        "resource_count": len(resources),
        "formats": sorted({r["format"] for r in resources if r["format"]}),
        "resources": resources,
        "found_by": found_by,
    }


def load_pages(folder: Path) -> dict[str, dict]:
    """Read every cached page in a folder into one dict keyed by dataset id."""
    seen: dict[str, dict] = {}
    for page in sorted(folder.glob("*.json")):
        label = page.stem.split("__")[0]
        channel = "resource_name" if label.startswith("res_lookup") else (
            "baseline" if label == "broad" else "dataset_text")
        for record in json.loads(page.read_text()).get("results", []):
            row = tidy_dataset(record, channel)
            key = row["dataset_id"]
            if key not in seen:
                seen[key] = row
            elif seen[key]["found_by"] != channel:
                seen[key]["found_by"] = "both_channels"
    return seen


def flatten() -> None:
    folder = config.path("raw_catalogue")
    out_file = config.path("interim") / "datasets.jsonl"

    if not list(folder.glob("*.json")):
        sys.exit("No raw pages found. Run: python -m src.catalogue fetch")

    seen = load_pages(folder)
    with open(out_file, "w") as f:
        for row in seen.values():
            f.write(json.dumps(row) + "\n")

    by_channel: dict[str, int] = {}
    for row in seen.values():
        by_channel[row["found_by"]] = by_channel.get(row["found_by"], 0) + 1

    print(f"Unique datasets: {len(seen)}")
    for channel, count in sorted(by_channel.items()):
        print(f"  found by {channel}: {count}")
    print(f"Written to {out_file}")


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "fetch":
        fetch()
    elif command == "flatten":
        flatten()
    elif command == "baseline":
        baseline()
    else:
        sys.exit("Usage: python -m src.catalogue [fetch|flatten|baseline]")
