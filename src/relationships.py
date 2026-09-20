"""Part 3, the other half: how businesses relate to each other.

The assignment asks us to do something sensible with the fact that sources
hint at relationships between different businesses, parent and subsidiary
being the obvious one.

Which source asserts one is discovered, not hard-coded. We scan every approved
config for a column whose name says group, parent, holding, owner or
subsidiary, and use the first that actually disagrees with the entity's own
name on a fair share of rows.

On our six, that finds WGEA's `corporate_group_name`, which differs from
`employer_name` on 36% of rows. A regulator stating, for free, that one
company sits inside another's group. On a different six it finds whatever
those sources offer, or nothing, and says so.

Three rules govern this file.

1. **A relationship is never a sameness link.** A subsidiary is not its
   parent. Merging them is the failure the assignment warns about, and it is
   tempting because the names look alike. Relationships live in their own file
   with their own confidence.

2. **We only record what a source asserted.** SALTER BROTHERS (CLOVELLY) PTY
   LTD looks like a child of SALTER BROTHERS HOSPITALITY, and probably is. But
   NICHOLAS FAMILY TRUST as parent of STEEKIM NICHOLAS FAMILY TRUST is the
   same pattern and far less certain. Guessing structure from name similarity
   is how a company graph quietly acquires a wrong ownership tree.

3. **Both ends resolve to a canonical key where we can, and stay raw where we
   cannot.** A parent name that matches two different ABNs is ambiguous, and
   we leave it unresolved rather than pick one.

Run:
    python -m src.relationships
"""

from __future__ import annotations

import json
import time
import re
from collections import defaultdict

from . import config, fetchfile, names
from .extract import local_copy
from .match import canonical_key, load
from .transforms import abn_is_valid

RELATIONSHIP_VERSION = "rel0.1"
RAW_LIMIT = 25000

# A closed vocabulary, the same discipline as the transform list. A relation
# not on this list cannot be written.
RELATIONS = {
    "member_of_corporate_group":
        "The source states this business belongs to that group.",
    "same_group_sibling":
        "Derived: two businesses the same source places in one group. "
        "Recorded, deliberately not used in matching.",
}

# Column names that suggest a link to a DIFFERENT business. Deliberately
# narrow: "Parent Contract ID" relates two contracts and must not match, so
# `contract` and `id` are excluded below.
GROUP_COLUMN = re.compile(
    r"(corporate[_ ]?group|parent|holding|ultimate|controlling|subsidiar|"
    r"owner|group[_ ]?name)", re.I)
NOT_A_BUSINESS_COLUMN = re.compile(r"contract|invoice|order|ethnic|anzsic|"
                                   r"age|id$", re.I)

# A column has to actually disagree with the entity name this often before we
# believe it names a different business.
MIN_DISAGREEMENT = 0.05


def find_group_column(cfg: dict, records: list[dict]) -> tuple:
    """Which column of this source, if any, names a different business."""
    name_field = next(
        (m.get("source_field") for m in cfg["field_mappings"]
         if m["canonical_field"] == "entity.legal_name" and m.get("source_field")),
        None)
    if not name_field or not records:
        return None, None, 0.0

    best = (None, 0.0)
    for column in records[0]:
        if not GROUP_COLUMN.search(column) or NOT_A_BUSINESS_COLUMN.search(column):
            continue
        filled = [r for r in records if str(r.get(column, "")).strip()]
        if not filled:
            continue
        differs = sum(1 for r in filled
                      if str(r[column]).strip() != str(r.get(name_field, "")).strip())
        share = differs / len(records)
        if share > best[1]:
            best = (column, share)

    if best[0] and best[1] >= MIN_DISAGREEMENT:
        return best[0], name_field, round(best[1], 3)
    return None, name_field, round(best[1], 3)


def name_index(observations: list[dict]) -> dict:
    """Normalised legal name -> the canonical keys it could mean.

    Built across every source, so a WGEA parent can resolve against an ASIC
    company or an ACNC charity, not only against WGEA itself.
    """
    index: dict[str, set] = defaultdict(set)
    for row in observations:
        legal_name = (row.get("entity") or {}).get("legal_name")
        normalised = names.normalise(legal_name)
        if not normalised or names.too_generic(normalised):
            continue
        key, kind = canonical_key(row)
        if kind in ("abn", "acn"):
            index[normalised].add(key)
    return index


def resolve(raw_name: str, index: dict) -> tuple:
    """Turn a parent's name into a key, or admit we cannot."""
    normalised = names.normalise(raw_name)
    if not normalised or names.too_generic(normalised):
        return "", "unresolved_name_too_generic", 0
    candidates = index.get(normalised, set())
    if len(candidates) == 1:
        return next(iter(candidates)), "resolved_by_name", 1
    if len(candidates) > 1:
        # Two different registered businesses share this name. Picking one
        # would be a guess, and a wrong parent is worse than no parent.
        return "", "unresolved_ambiguous", len(candidates)
    return "", "unresolved_not_found", 0


def main() -> None:
    started = time.time()
    observations = load("observations_deep")
    index = name_index(observations)
    print(f"  {len(observations):,} observations, "
          f"{len(index):,} distinct names carrying an identifier\n")

    rows, by_group = [], defaultdict(set)
    found_any = False

    for config_file in sorted(config.path("configs").glob("*.json")):
        cfg = json.loads(config_file.read_text())
        source_id = cfg["source_id"]
        raw = fetchfile.read_records(local_copy(cfg), cfg["resource"], RAW_LIMIT)
        column, name_field, share = find_group_column(cfg, raw)

        if not column:
            print(f"  {source_id[:44]:44} no group column")
            continue

        found_any = True
        print(f"  {source_id[:44]:44} \"{column}\" differs from "
              f"\"{name_field}\" on {share:.0%} of rows")

        abn_field = next(
            (m.get("source_field") for m in cfg["field_mappings"]
             if m["canonical_field"] == "entity.abn" and m.get("source_field")),
            None)

        for record in raw:
            child = str(record.get(name_field, "")).strip()
            parent = str(record.get(column, "")).strip()
            if not child or not parent or parent == child:
                continue

            digits = "".join(ch for ch in str(record.get(abn_field, "") or "")
                             if ch.isdigit()) if abn_field else ""
            child_key = f"abn:{digits}" if digits and abn_is_valid(digits) else ""
            parent_key, how, candidates = resolve(parent, index)

            rows.append({
                "relation": "member_of_corporate_group",
                "direction": "child_to_parent",
                "from_key": child_key,
                "from_name_raw": child,
                "to_key": parent_key,
                "to_name_raw": parent,
                "asserted_by": source_id,
                "asserted_in_column": column,
                "source_record_id": f"abn:{digits}" if digits else child,
                # How sure we are the RELATIONSHIP holds. Not sameness, not
                # field parsing. A third kind of confidence, in its own file
                # so it cannot be mistaken for link_confidence.
                "confidence": 0.90,
                "resolved": {
                    "from": "abn_exact" if child_key else "unresolved_no_identifier",
                    "to": how,
                    "to_candidates": candidates,
                },
                "relationship_version": RELATIONSHIP_VERSION,
            })
            if child_key:
                by_group[names.normalise(parent)].add(child_key)

    # Derived siblings. Recorded because the structure is real and useful
    # later. Deliberately NOT fed into the matcher; the flag on every row is
    # what a future change would have to flip.
    siblings = []
    for group, children in by_group.items():
        if len(children) < 2:
            continue
        ordered = sorted(children)
        for i, a in enumerate(ordered):
            for b in ordered[i + 1:]:
                siblings.append({
                    "relation": "same_group_sibling",
                    "direction": "undirected",
                    "from_key": a, "to_key": b,
                    "via_group": group,
                    "confidence": 0.85,
                    "used_in_matching": False,
                    "relationship_version": RELATIONSHIP_VERSION,
                })

    out = config.path("outputs") / "relationships.jsonl"
    with open(out, "w") as f:
        for row in rows + siblings:
            f.write(json.dumps(row, default=str) + "\n")

    if not found_any:
        print("\n  None of these sources assert a relationship between two "
              "businesses.")
        print(f"  An empty {out.name} is written, which is the honest answer.")
        return

    resolved_both = sum(1 for r in rows if r["from_key"] and r["to_key"])
    how_counts: dict[str, int] = defaultdict(int)
    for r in rows:
        how_counts[r["resolved"]["to"]] += 1

    print(f"\n  parent assertions found: {len(rows):,}")
    print(f"  both ends resolved to a canonical key: {resolved_both:,}")
    print("\n  how the parent resolved:")
    for how, count in sorted(how_counts.items(), key=lambda kv: -kv[1]):
        print(f"    {how:28} {count:6,}")
    print(f"\n  derived sibling pairs recorded, unused in matching: "
          f"{len(siblings):,}")
    print(f"  groups with more than one known child: "
          f"{sum(1 for c in by_group.values() if len(c) > 1):,}")

    biggest = sorted(by_group.items(), key=lambda kv: -len(kv[1]))[:5]
    if biggest and len(biggest[0][1]) > 1:
        print("\n  largest groups seen:")
        for group, children in biggest:
            print(f"    {len(children):3d} children  {group[:52]}")

    print(f"\n  {round(time.time() - started, 2)}s, 0 model calls, $0.00")
    print(f"  written to {out}")


if __name__ == "__main__":
    main()
