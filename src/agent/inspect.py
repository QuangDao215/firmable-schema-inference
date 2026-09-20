"""Steps C1 and C2: work out what the file is, then describe its columns.

Neither step calls a model. Together they turn a URL into a short, honest
summary that the model in step C3 can read in a few thousand tokens instead of
being handed a 200 MB file.

C1 probe    download, find out what the file really is from its bytes, and
            pull records out into one flat form.
C2 profile  per column: how full it is, how varied, what it looks like, and
            what real values it holds.

The records C1 writes are split in two. C2 profiles the first part, and the
validation step C4 tests against the second part, which the model never sees.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from .. import config, fetchfile
from ..transforms import abn_is_valid, acn_is_valid

RECORDS_TO_PULL = 2000
SAMPLE_ROWS = 400          # what the model gets to see
EXAMPLES_PER_COLUMN = 8
DISTINCT_CAP = 500

DOWNLOADS = config.ROOT / "data" / "raw" / "sources"

DATE_FORMATS = [
    ("%d/%m/%Y", r"^\d{1,2}/\d{1,2}/\d{4}$"),
    ("%Y-%m-%d", r"^\d{4}-\d{2}-\d{2}$"),
    ("%d-%m-%Y", r"^\d{1,2}-\d{1,2}-\d{4}$"),
    ("%d %b %Y", r"^\d{1,2} [A-Za-z]{3} \d{4}$"),
    ("%Y%m%d", r"^\d{8}$"),
    ("%Y-%m-%d %H:%M:%S", r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$"),
    ("%Y-%m-%dT%H:%M:%S", r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"),
]

STATE_WORDS = re.compile(
    r"^(nsw|vic|qld|wa|sa|tas|act|nt|new south wales|victoria|queensland|"
    r"western australia|south australia|tasmania|northern territory|"
    r"australian capital territory)$", re.I)


# --------------------------------------------------------------------------
# C1
# --------------------------------------------------------------------------

def c1_probe(source: dict) -> dict:
    """Download the file and work out how to read it."""
    url = source["download_url"]
    stamp = hashlib.sha1(url.encode()).hexdigest()[:10]
    local = DOWNLOADS / f"{source['dataset_id']}_{stamp}.bin"

    if not local.exists():
        got = fetchfile.download_for_preview(url, local)
        if not got.get("ok"):
            raise RuntimeError(f"download failed: {got.get('error')}")

    view = fetchfile.preview(local, rows=12)
    real_format = view.get("sniffed", "CSV")

    shape = {
        "url": url,
        "catalogue_format": source.get("download_format", ""),
        "real_format": real_format,
        "encoding": "utf-8",
        "header_row": view.get("header_row", 0),
    }
    if real_format == "TSV":
        shape["delimiter"] = "\t"
    elif real_format == "CSV":
        shape["delimiter"] = ","
    if view.get("sheet"):
        shape["sheet"] = view["sheet"]
    if view.get("inner_file"):
        shape["zip_member"] = view["inner_file"]

    records = fetchfile.read_records(local, shape, RECORDS_TO_PULL)
    if not records:
        raise RuntimeError("file opened but no records came out")

    out = config.path("runs") / source["source_id"] / "records.jsonl"
    with open(out, "w") as f:
        for record in records:
            f.write(json.dumps(record, default=str) + "\n")

    return {
        "local_path": str(local),
        "bytes": local.stat().st_size,
        "shape": shape,
        "records_pulled": len(records),
        "sample_rows": min(SAMPLE_ROWS, len(records)),
        "holdout_rows": max(0, len(records) - SAMPLE_ROWS),
        "columns": list(records[0].keys()),
        "records_file": str(out),
        # Worth flagging: publishers are often wrong about their own format.
        "catalogue_format_was_wrong":
            source.get("download_format", "").upper() not in (real_format, ""),
    }


# --------------------------------------------------------------------------
# C2
# --------------------------------------------------------------------------

def _looks_like(values: list[str]) -> dict:
    """What fraction of the real values match each thing we can recognise."""
    total = len(values) or 1
    digits = [re.sub(r"\D", "", v) for v in values]

    date_hits = Counter()
    for value in values:
        for fmt, pattern in DATE_FORMATS:
            if re.match(pattern, value.strip()):
                date_hits[fmt] += 1
                break

    return {
        "abn_valid": round(sum(1 for d in digits if abn_is_valid(d)) / total, 3),
        "acn_valid": round(sum(1 for d in digits if acn_is_valid(d)) / total, 3),
        "postcode": round(sum(1 for v in values
                              if re.fullmatch(r"\d{4}", v.strip())) / total, 3),
        "state": round(sum(1 for v in values if STATE_WORDS.match(v.strip())) / total, 3),
        "numeric": round(sum(1 for v in values
                             if re.fullmatch(r"-?\d+(\.\d+)?", v.strip())) / total, 3),
        "url": round(sum(1 for v in values if v.strip().lower().startswith("http")) / total, 3),
        "email": round(sum(1 for v in values if "@" in v and "." in v) / total, 3),
        "date_formats": [{"format": fmt, "share": round(n / total, 3)}
                         for fmt, n in date_hits.most_common(3)],
    }


def c2_profile(source: dict, probe: dict) -> dict:
    """Describe every column from the sample rows."""
    records = [json.loads(line) for line in open(probe["records_file"])]
    sample = records[:SAMPLE_ROWS]

    columns = []
    for index, name in enumerate(probe["columns"]):
        raw = [str(r.get(name, "")) for r in sample]
        filled = [v for v in raw if v.strip() and v.strip().lower()
                  not in ("none", "null", "n/a", "na", "-")]
        distinct = Counter(filled)

        columns.append({
            "name": name,
            "index": index,
            "filled": len(filled),
            "of_rows": len(sample),
            "null_rate": round(1 - len(filled) / (len(sample) or 1), 3),
            "distinct": min(len(distinct), DISTINCT_CAP),
            "distinct_is_capped": len(distinct) >= DISTINCT_CAP,
            "max_length": max((len(v) for v in filled), default=0),
            # Real values, not invented ones. This is what makes the model's
            # job possible and what a human checks the mapping against.
            "examples": [v for v, _ in distinct.most_common(EXAMPLES_PER_COLUMN)],
            "looks_like": _looks_like(filled) if filled else {},
            # A column with few distinct values is an enum. Show them all, so
            # the model can write a complete map_values rather than guess.
            "all_values": (sorted(distinct) if 0 < len(distinct) <= 25 else None),
        })

    return {
        "columns_profiled": len(columns),
        "rows_sampled": len(sample),
        "columns": columns,
    }
