"""Part 4: one profile per business, merging what all six sources say.

No model calls. This is arithmetic over data we already have.

Four things the assignment insists on, and how each is handled here:

  a confidence on each field, not one score for the profile
      every field carries value_confidence, with the inputs that produced it
      kept visible beside it rather than blended away

  provenance per field
      which source record the winning value came from, and when it said it

  where sources disagree, show your working
      the winning value, the policy that chose it, the rung of the ladder that
      decided it, and every losing value with its source. Silently picking one
      is the failure mode the assignment names.

  fields you could not fill should be absent
      not blank, not null, not guessed

Run:
    python -m src.profile
    python -m src.profile --count 50
"""

from __future__ import annotations

import json
import sys
import time
from collections import defaultdict

from . import config, names
from .match import load

BUILDER_VERSION = "profile0.1"
DEFAULT_COUNT = 50

CANONICAL_FIELDS = [
    "entity.legal_name", "entity.trading_name", "entity.abn", "entity.acn",
    "entity.nzbn", "entity.entity_type", "entity.status", "entity.website",
    "entity.industry_code", "entity.date_registered",
    "address.full", "address.locality", "address.state", "address.postcode",
    "address.country",
]

# Two policies, because the right answer differs by what kind of fact it is.
#
# recency_wins   facts that change. A company that moved has a new address, so
#                the newest assertion is the right one.
# authority_wins registry facts that do not change. Disagreement means one
#                source is wrong, so the more reliable publisher should win.
POLICY = {
    "entity.legal_name": "recency_wins",
    "entity.trading_name": "recency_wins",
    "entity.status": "recency_wins",
    "entity.website": "recency_wins",
    "entity.entity_type": "recency_wins",
    "address.full": "recency_wins",
    "address.locality": "recency_wins",
    "address.state": "recency_wins",
    "address.postcode": "recency_wins",
    "address.country": "recency_wins",
    "entity.abn": "authority_wins",
    "entity.acn": "authority_wins",
    "entity.nzbn": "authority_wins",
    "entity.date_registered": "authority_wins",
    "entity.industry_code": "authority_wins",
}


def comparable(field: str, value) -> str:
    """The form we compare on. The raw value is what we keep.

    'Wilson Security Pty Ltd' and 'WILSON SECURITY PTY. LTD.' are agreement,
    not conflict. Counting them as a conflict would manufacture disagreement
    that is not there.
    """
    text = str(value).strip()
    if field in ("entity.legal_name", "entity.trading_name"):
        return names.normalise(text)
    if field == "entity.website":
        return text.lower().rstrip("/").replace("https://", "").replace(
            "http://", "").removeprefix("www.")
    return text.upper()


def candidates(observations: list[dict]) -> dict:
    """Every value any source offered for every field, with its provenance."""
    found: dict[str, list] = defaultdict(list)
    for row in observations:
        confidence = row.get("confidence") or {}
        for section in ("entity", "address"):
            for name, value in (row.get(section) or {}).items():
                field = f"{section}.{name}"
                if value is None or str(value).strip() == "":
                    continue
                found[field].append({
                    "value": value,
                    "source_id": row["source_id"],
                    "source_record_id": row["source_record_id"],
                    "observed_at": row.get("observed_at") or "",
                    "valid_to": row.get("valid_to"),
                    "field_confidence": (confidence.get("field_confidence")
                                         or {}).get(field),
                    "source_reliability": confidence.get("source_reliability"),
                })
    return found


def _still_current(candidate: dict, today: str) -> bool:
    """A claim whose validity window has not closed."""
    return not candidate.get("valid_to") or str(candidate["valid_to"]) >= today


def resolve(field: str, offered: list[dict], today: str) -> dict:
    """Pick a winner, and record exactly why it won and what lost."""
    groups: dict[str, list] = defaultdict(list)
    for candidate in offered:
        groups[comparable(field, candidate["value"])].append(candidate)

    policy = POLICY.get(field, "recency_wins")
    sources_total = len({c["source_id"] for c in offered})

    def rank(key_and_members):
        _, members = key_and_members
        sources = len({m["source_id"] for m in members})
        current = any(_still_current(m, today) for m in members)
        newest = max((m["observed_at"] or "") for m in members)
        reliability = max((m["source_reliability"] or 0) for m in members)
        if policy == "authority_wins":
            return (reliability, sources, newest, current)
        return (current, newest, reliability, sources)

    ordered = sorted(groups.items(), key=rank, reverse=True)
    winning_key, winning_members = ordered[0]

    # The most complete spelling of the winning value, not an arbitrary one.
    winning_members.sort(key=lambda m: (len(str(m["value"])),
                                        m["observed_at"] or ""), reverse=True)
    winner = winning_members[0]

    agreeing = len({m["source_id"] for m in winning_members})
    disagreeing = sources_total - agreeing

    decided_by = "only value offered"
    if len(ordered) > 1:
        runner_up = ordered[1][1]
        if policy == "authority_wins":
            decided_by = (
                "higher source reliability"
                if max((m["source_reliability"] or 0) for m in winning_members)
                > max((m["source_reliability"] or 0) for m in runner_up)
                else "more sources agreed")
        else:
            if any(_still_current(m, today) for m in winning_members) and \
                    not any(_still_current(m, today) for m in runner_up):
                decided_by = "the losing claim had expired"
            elif max((m["observed_at"] or "") for m in winning_members) > \
                    max((m["observed_at"] or "") for m in runner_up):
                decided_by = "stated more recently"
            else:
                decided_by = "higher source reliability"
    elif agreeing > 1:
        decided_by = f"all {agreeing} sources agreed"

    # A value confidence, not a field confidence. The ontology's
    # field_confidence means "we parsed this right". This means "this is the
    # right value for this business", which is a different question. The
    # inputs stay visible beside it rather than being blended away.
    best_parse = max((m["field_confidence"] or 0) for m in winning_members)
    reliability = max((m["source_reliability"] or 0) for m in winning_members)
    value_confidence = round(
        min(0.99, best_parse * reliability
            * (1.0 if disagreeing == 0 else 0.75)
            * (1.0 if agreeing > 1 or sources_total == 1 else 0.9)), 3)

    losing = []
    for key, members in ordered[1:]:
        members.sort(key=lambda m: m["observed_at"] or "", reverse=True)
        example = members[0]
        losing.append({
            "value": example["value"],
            "source_id": example["source_id"],
            "source_record_id": example["source_record_id"],
            "observed_at": example["observed_at"],
            "sources_offering_it": len({m["source_id"] for m in members}),
        })

    return {
        "value": winner["value"],
        "value_confidence": value_confidence,
        "provenance": {
            "source_id": winner["source_id"],
            "source_record_id": winner["source_record_id"],
            "observed_at": winner["observed_at"],
            "field_confidence": winner["field_confidence"],
            "source_reliability": winner["source_reliability"],
        },
        "agreement": {
            "sources_agreeing": agreeing,
            "sources_disagreeing": disagreeing,
            "sources_offering_this_field": sources_total,
            "distinct_values": len(groups),
        },
        "conflict": None if len(ordered) == 1 else {
            "policy": policy,
            "decided_by": decided_by,
            "losing_values": losing,
        },
    }


def build(entity: dict, by_key: dict, today: str) -> dict:
    observations = by_key[entity["canonical_entity_key"]]
    offered = candidates(observations)

    fields = {}
    for field in CANONICAL_FIELDS:
        if offered.get(field):
            fields[field] = resolve(field, offered[field], today)
    # Fields no source supplied are absent. Not blank, not null, not guessed.

    return {
        "canonical_entity_key": entity["canonical_entity_key"],
        "key_kind": entity["key_kind"],
        "sources": entity["sources"],
        "source_count": entity["source_count"],
        "observation_count": len(observations),
        "fields": fields,
        "fields_filled": len(fields),
        "fields_with_conflict": sum(1 for f in fields.values() if f["conflict"]),
        "built_at": today,
        "builder_version": BUILDER_VERSION,
    }


# --------------------------------------------------------------------------
# what happens when a source is wrong
# --------------------------------------------------------------------------

def without_source(entities: list[dict], by_key: dict, dropped: str,
                   today: str) -> dict:
    """Rebuild every profile with one source removed, and see what moved."""
    gone, lost_fields, changed_values = 0, 0, 0
    field_changes = defaultdict(int)

    for entity in entities:
        key = entity["canonical_entity_key"]
        kept = [o for o in by_key[key] if o["source_id"] != dropped]
        before = build(entity, by_key, today)

        if not kept:
            gone += 1
            continue
        remaining_sources = sorted({o["source_id"] for o in kept})
        if len(remaining_sources) < 2:
            # No longer appears in more than one source, so it would not be an
            # entity at all under our own definition.
            gone += 1
            continue

        after = build({**entity, "canonical_entity_key": key,
                       "sources": remaining_sources,
                       "source_count": len(remaining_sources)},
                      {key: kept}, today)

        if after["fields_filled"] < before["fields_filled"]:
            lost_fields += 1
        for field, resolved in before["fields"].items():
            new = after["fields"].get(field)
            if new and comparable(field, new["value"]) != \
                    comparable(field, resolved["value"]):
                changed_values += 1
                field_changes[field] += 1
                break

    return {
        "source_removed": dropped,
        "profiles_that_disappear": gone,
        "profiles_that_lose_a_field": lost_fields,
        "profiles_where_a_value_changes": changed_values,
        "fields_that_changed": dict(field_changes),
    }


def main() -> None:
    count = DEFAULT_COUNT
    if "--count" in sys.argv:
        count = int(sys.argv[sys.argv.index("--count") + 1])

    entities_file = config.path("outputs") / "entities.jsonl"
    if not entities_file.exists():
        sys.exit("No entities.jsonl. Run: python -m src.match")

    entities = [json.loads(line) for line in open(entities_file)]
    observations = load("observations_deep")
    today = time.strftime("%Y-%m-%d")

    # Index every observation under the key its record resolves to. This
    # includes within-source duplicates that the matcher collapsed: a company
    # with 40 contract rows has 40 chances to state its address.
    from .match import canonical_key
    by_key: dict[str, list] = defaultdict(list)
    for row in observations:
        key, _ = canonical_key(row)
        if key:
            by_key[key].append(row)

    # Which 50. Most sources first, because a profile assembled from four
    # disagreeing sources is worth more than one from two identical rows.
    entities.sort(key=lambda e: (e["source_count"], len(by_key[e["canonical_entity_key"]])),
                  reverse=True)
    chosen = entities[:count]

    print(f"Building {len(chosen)} profiles from {len(observations):,} "
          f"observations\n")

    profiles = [build(entity, by_key, today) for entity in chosen]

    out = config.path("outputs") / "company_profiles.jsonl"
    with open(out, "w") as f:
        for profile in profiles:
            f.write(json.dumps(profile, default=str) + "\n")

    spread = defaultdict(int)
    for p in profiles:
        spread[p["source_count"]] += 1
    filled = sum(p["fields_filled"] for p in profiles) / len(profiles)
    conflicted = sum(p["fields_with_conflict"] for p in profiles)

    print(f"  profiles written      : {len(profiles)}")
    print(f"  sources per profile   : "
          + ", ".join(f"{n} sources x{spread[n]}" for n in sorted(spread, reverse=True)))
    print(f"  mean fields filled    : {filled:.1f} of {len(CANONICAL_FIELDS)}")
    print(f"  fields with a conflict: {conflicted}")

    by_field = defaultdict(lambda: [0, 0])
    for p in profiles:
        for field, resolved in p["fields"].items():
            by_field[field][0] += 1
            if resolved["conflict"]:
                by_field[field][1] += 1
    print(f"\n  {'field':26} {'filled':>7} {'conflicts':>10}")
    for field in CANONICAL_FIELDS:
        got, clash = by_field[field]
        if got:
            print(f"  {field:26} {got:>7} {clash:>10}")

    print("\n  What happens if we delete one source")
    print(f"  {'source':44} {'gone':>5} {'lose a field':>13} {'value moves':>12}")
    analysis = []
    for source in sorted({o["source_id"] for o in observations}):
        result = without_source(chosen, by_key, source, today)
        analysis.append(result)
        print(f"  {source[:44]:44} {result['profiles_that_disappear']:>5} "
              f"{result['profiles_that_lose_a_field']:>13} "
              f"{result['profiles_where_a_value_changes']:>12}")

    report = config.path("outputs") / "profile_source_impact.json"
    report.write_text(json.dumps({
        "profiles": len(profiles),
        "question": "If we deleted one of the six sources, how many profiles "
                    "change and how badly?",
        "definitions": {
            "profiles_that_disappear": "the entity no longer appears in more "
                                       "than one source, so it is not an "
                                       "entity under our own definition",
            "profiles_that_lose_a_field": "at least one canonical field can no "
                                          "longer be filled",
            "profiles_where_a_value_changes": "a winning value is different "
                                              "without this source",
        },
        "per_source": analysis,
    }, indent=1))

    print(f"\n  written to {out}")
    print(f"           and {report}")
    print(f"  0 model calls, $0.00")


if __name__ == "__main__":
    main()
