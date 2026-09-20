"""Stage A3: score every dataset with plain code. No model calls.

The point of this step is to throw away most of the catalogue cheaply, so the
model in stage A4 only reads a shortlist worth reading.

Two things decide the score:

1. A hard gate. A dataset must have at least one resource in a format we can
   actually parse. A PDF that describes businesses beautifully is no use to
   part 2, which has to read records out of the file.
2. Word weights. Words that name a business, words that hint at one, words that
   suggest the dataset is about weather or geology instead.

Every score keeps its working, so a human can see why a dataset ranked where
it did.

Run:
    python -m src.score
"""

from __future__ import annotations

import json
import re
import sys

import yaml

from . import config
from .formats import machine_readable, normalise

KEYWORD_FILE = config.ROOT / "config" / "keywords.yaml"


def _load_keywords() -> dict:
    with open(KEYWORD_FILE) as f:
        return yaml.safe_load(f)


def _searchable_text(row: dict) -> str:
    """Everything the filter is allowed to read, lowercased."""
    parts = [
        row.get("title") or "",
        row.get("description") or "",
        " ".join(row.get("tags") or []),
        " ".join(r.get("name") or "" for r in row.get("resources") or []),
    ]
    return re.sub(r"\s+", " ", " ".join(parts)).lower()


def _hits(text: str, words: list[str]) -> list[str]:
    """Which words appear. Whole words only, so 'abn' does not match 'abnormal'."""
    found = []
    for word in words:
        pattern = r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])"
        if re.search(pattern, text):
            found.append(word)
    return found


def score_one(row: dict, keywords: dict) -> dict:
    """Return the row with a score, the reasons for it, and the gate result."""
    text = _searchable_text(row)
    clean_formats = sorted({normalise(f) for f in row.get("formats") or []})
    readable = sorted(machine_readable(clean_formats))

    strong = _hits(text, keywords["strong"]["words"])
    medium = _hits(text, keywords["medium"]["words"])
    negative = _hits(text, keywords["negative"]["words"])

    points = 0.0
    points += len(strong) * keywords["strong"]["weight"]
    points += len(medium) * keywords["medium"]["weight"]
    points += len(negative) * keywords["negative"]["weight"]

    publisher_hit = row.get("organisation") in set(
        keywords["publisher_bonus"]["organisations"])
    if publisher_hit:
        points += keywords["publisher_bonus"]["weight"]

    format_points = max(
        [keywords["format_bonus"].get(f, 0.0) for f in readable] or [0.0])
    points += format_points

    # Squash to 0..1 so the number reads like a confidence. 20 raw points is
    # about as high as a real dataset gets, so that is the top of the scale.
    normalised = max(0.0, min(1.0, points / 20.0))

    return {
        **row,
        "clean_formats": clean_formats,
        "readable_formats": readable,
        "passes_format_gate": bool(readable),
        "rule_score": round(normalised, 4),
        "rule_points": round(points, 2),
        "rule_evidence": {
            "strong": strong,
            "medium": medium,
            "negative": negative,
            "publisher_bonus": publisher_hit,
            "format_points": format_points,
        },
    }


# --------------------------------------------------------------------------
# Collapsing yearly editions
# --------------------------------------------------------------------------

_EDITION_NOISE = [
    r"\b(19|20)\d{2}\s*[-/]\s*(19|20)?\d{2}\b",   # 2023-24, 2023-2024
    r"\b(19|20)\d{2}\b",                            # 2019
    r"\bfy\b",
    r"\bq[1-4]\b",
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b",
    r"\b(annual|quarterly|monthly)\b",
]


def _edition_stem(title: str) -> str:
    """A title with dates stripped out, so editions of one dataset collapse."""
    text = title.lower()
    for pattern in _EDITION_NOISE:
        text = re.sub(pattern, " ", text)
    text = re.sub(r"[^a-z ]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def collapse_editions(rows: list[dict]) -> list[dict]:
    """Mark one row per group of yearly editions, and list its siblings.

    Publishers ship the same dataset once a year. The ACNC Annual Information
    Statement appears twelve times in our pool, one file per year. Sending all
    twelve to the model wastes tokens and would fill the shortlist with one
    source repeated twelve ways.

    We keep the newest edition and record the rest, so nothing is lost and a
    human can see what was folded.
    """
    groups: dict[tuple, list[dict]] = {}
    for row in rows:
        key = (row.get("organisation"), _edition_stem(row["title"]))
        groups.setdefault(key, []).append(row)

    for group in groups.values():
        # Newest first. Fall back on score when dates are missing.
        group.sort(key=lambda r: (r.get("modified") or "", r["rule_score"]),
                   reverse=True)
        primary, siblings = group[0], group[1:]
        primary["is_primary_edition"] = True
        primary["edition_siblings"] = [
            {"dataset_id": s["dataset_id"], "title": s["title"]} for s in siblings]
        for sibling in siblings:
            sibling["is_primary_edition"] = False
            sibling["edition_siblings"] = []
            sibling["folded_into"] = primary["dataset_id"]
    return rows


def score_all(rows: list[dict]) -> list[dict]:
    """Score every row and return them ranked, best first."""
    keywords = _load_keywords()
    scored = [score_one(r, keywords) for r in rows]
    scored = collapse_editions(scored)
    scored.sort(key=lambda r: r["rule_score"], reverse=True)
    return scored


def main() -> None:
    in_file = config.path("interim") / "datasets.jsonl"
    out_file = config.path("interim") / "scored.jsonl"
    if not in_file.exists():
        sys.exit("No datasets.jsonl. Run: python -m src.catalogue flatten")

    settings = config.load()["catalogue"]
    rows = score_all([json.loads(line) for line in open(in_file)])

    with open(out_file, "w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")

    # No score threshold anywhere. The assignment does not define one, so we
    # rank and take the top k. Any cut-off we invented would be us choosing the
    # number that tells the nicest story.
    pool = settings["candidate_pool"]
    shortlist = settings["shortlist_size"]

    primaries = [r for r in rows if r["is_primary_edition"]]
    folded = len(rows) - len(primaries)
    candidates = primaries[:pool]

    by_channel: dict[str, int] = {}
    for row in candidates:
        by_channel[row["found_by"]] = by_channel.get(row["found_by"], 0) + 1

    print(f"Scored {len(rows)} datasets, all of them machine-readable")
    print(f"Yearly editions folded into their newest: {folded}")
    print(f"Distinct sources left: {len(primaries)}")
    print()
    print(f"Top {len(candidates)} go to the model in stage A4. Where they came from:")
    for channel, count in sorted(by_channel.items(), key=lambda kv: -kv[1]):
        print(f"  {channel:16} {count}")
    print()
    print(f"Score range: {candidates[-1]['rule_score']:.2f} "
          f"to {candidates[0]['rule_score']:.2f}")
    print(f"The model then ranks those {pool} down to a shortlist of {shortlist}.")
    print()
    print(f"Written to {out_file}")
    print(f"\nTop 15 by rule score:")
    for i, r in enumerate(candidates[:15], 1):
        print(f"  {i:2d}. {r['rule_score']:.2f}  "
              f"{','.join(r['readable_formats'])[:16]:16}  {r['title'][:50]}"
              + (f"  (+{len(r['edition_siblings'])} editions)"
                 if r["edition_siblings"] else ""))


if __name__ == "__main__":
    main()
