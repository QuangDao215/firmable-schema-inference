"""Part 3: find businesses that appear in more than one source.

The model is a pairwise link table. Each row records that two records refer to
one business, which business, how sure we are, and what the evidence was.
Clusters are derived from those links, never the other way round: you can
always rebuild a view, you cannot rebuild evidence.

No model calls. Every decision here is a rule a person can read and argue with.

Three outputs:
  outputs/links.jsonl       proposed links
  outputs/unlinked.jsonl    records we believed held a business and refused to
                            link, with the reason
  outputs/entities.jsonl    canonical key -> member records, derived from links

Run:
    python -m src.match
"""

from __future__ import annotations

import json
import sys
import time
from collections import defaultdict
from itertools import combinations

from . import config, names
from .transforms import abn_is_valid, acn_is_valid

MATCHER_VERSION = "matcher0.1"
THRESHOLD = 0.70
LOOSE_THRESHOLD = 0.45

# How sure we are that two records are the same business, per method.
# These are link_confidence and nothing else. Source reliability and field
# confidence are inputs recorded in the evidence, never folded into the score.
METHOD_CONFIDENCE = {
    "abn_exact": 0.99,
    "acn_exact": 0.99,
    "abn_acn_derived": 0.80,
    "name_geo": 0.75,
    "name_only": 0.45,
}


# --------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------

def load(folder: str = "observations_deep") -> list[dict]:
    rows = []
    path = config.path("outputs") / folder
    if not path.exists() or not list(path.glob("*.jsonl")):
        sys.exit(f"No observations in outputs/{folder}. "
                 f"Run: python -m src.extract --limit 25000 --out {folder}")
    for f in sorted(path.glob("*.jsonl")):
        rows.extend(json.loads(line) for line in open(f))
    return rows


def entity_of(row: dict) -> dict:
    return row.get("entity") or {}


def address_of(row: dict) -> dict:
    return row.get("address") or {}


def canonical_key(row: dict) -> tuple[str, str]:
    """The key this record gets, and how it was derived.

    The prefix is part of the key on purpose. A consumer can see at a glance
    whether an entity was identified by a checksummed government number or by
    a name that looked the same. Those are different claims.
    """
    entity = entity_of(row)
    if entity.get("abn") and abn_is_valid(entity["abn"]):
        return f"abn:{entity['abn']}", "abn"
    if entity.get("acn") and acn_is_valid(entity["acn"]):
        return f"acn:{entity['acn']}", "acn"

    normalised = names.normalise(entity.get("legal_name"))
    if normalised and not names.too_generic(normalised):
        geo = (address_of(row).get("postcode")
               or address_of(row).get("state") or "")
        import hashlib
        stamp = hashlib.sha1(f"{normalised}|{geo}".encode()).hexdigest()[:12]
        return f"nk:{stamp}", "name"
    return "", "none"


# --------------------------------------------------------------------------
# one record per key per source
# --------------------------------------------------------------------------

def completeness(row: dict) -> int:
    return len(entity_of(row)) + len(address_of(row))


def representatives(rows: list[dict]) -> dict:
    """Collapse within-source duplicates before comparing across sources.

    One ABN appears on many rows of the ASIC extract and on many rows of the
    contract register, because a company changes its name and wins contracts
    more than once. Emitting every pair would produce tens of thousands of
    links that all say the same thing and make a precision sample meaningless.

    So we take one representative per (source, key): the most complete record,
    then the most recently observed. The rest are recorded as members.
    """
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        key, _ = canonical_key(row)
        if key:
            groups[(row["source_id"], key)].append(row)

    chosen = {}
    for (source_id, key), members in groups.items():
        members.sort(key=lambda r: (completeness(r), r.get("observed_at") or ""),
                     reverse=True)
        chosen[(source_id, key)] = {
            "record": members[0],
            "member_count": len(members),
            "member_record_ids": [m["source_record_id"] for m in members[:5]],
        }
    return chosen


# --------------------------------------------------------------------------
# scoring one candidate pair
# --------------------------------------------------------------------------

def compare(a: dict, b: dict) -> dict:
    """Decide whether two records are the same business, and say why."""
    ea, eb = entity_of(a), entity_of(b)
    aa, ab = address_of(a), address_of(b)

    abn_a, abn_b = ea.get("abn"), eb.get("abn")
    acn_a, acn_b = ea.get("acn"), eb.get("acn")
    name_a = names.normalise(ea.get("legal_name"))
    name_b = names.normalise(eb.get("legal_name"))
    trade_a = names.normalise(ea.get("trading_name"))
    trade_b = names.normalise(eb.get("trading_name"))

    conflicts = []
    if aa.get("state") and ab.get("state") and aa["state"] != ab["state"]:
        conflicts.append(f"state: {aa['state']} vs {ab['state']}")
    if abn_a and abn_b and abn_a != abn_b:
        conflicts.append(f"abn: {abn_a} vs {abn_b}")
    if acn_a and acn_b and acn_a != acn_b:
        conflicts.append(f"acn: {acn_a} vs {acn_b}")

    method, matched_on = None, {}

    if abn_a and abn_b and abn_a == abn_b and abn_is_valid(abn_a):
        method, matched_on = "abn_exact", {"abn": abn_a}
    elif acn_a and acn_b and acn_a == acn_b and acn_is_valid(acn_a):
        method, matched_on = "acn_exact", {"acn": acn_a}
    else:
        # The ontology warns: the last 9 digits of an ABN are often the ACN,
        # but not always. So this is a bridge, at lower confidence, marked as
        # derived. It is the only link between ASIC and sources with no ACN.
        for abn, acn in ((abn_a, acn_b), (abn_b, acn_a)):
            if abn and acn and abn_is_valid(abn) and acn_is_valid(acn) \
                    and abn[2:] == acn:
                method = "abn_acn_derived"
                matched_on = {"abn": abn, "acn": acn,
                              "rule": "last 9 digits of the ABN equal the ACN"}
                break

    if method is None:
        pairs = [(name_a, name_b), (name_a, trade_b),
                 (trade_a, name_b), (trade_a, trade_b)]
        hit = next(((x, y) for x, y in pairs
                    if x and y and x == y and not names.too_generic(x)), None)
        if hit:
            postcode_agrees = (aa.get("postcode") and ab.get("postcode")
                               and aa["postcode"] == ab["postcode"])
            state_agrees = (aa.get("state") and ab.get("state")
                            and aa["state"] == ab["state"])
            method = "name_geo" if (postcode_agrees or state_agrees) else "name_only"
            matched_on = {"name": hit[0],
                          "postcode_agrees": bool(postcode_agrees),
                          "state_agrees": bool(state_agrees)}

    if method is None:
        return {}

    confidence = METHOD_CONFIDENCE[method]
    refusals = []

    # Where we draw the line. Each of these turns a candidate into a refusal
    # rather than a low-confidence link, because the reason matters.
    if conflicts and method.startswith("name"):
        refusals.append("names agree but " + "; ".join(conflicts))
    if method.startswith("name"):
        if names.looks_like_a_person(ea.get("legal_name")) or \
                names.looks_like_a_person(eb.get("legal_name")):
            refusals.append("one side reads as a person's name, not an organisation")
        form_a, form_b = (names.legal_form(ea.get("legal_name")),
                          names.legal_form(eb.get("legal_name")))
        if form_a and form_b and form_a != form_b:
            refusals.append(f"different legal forms: {form_a} vs {form_b}")
        # A body attached to an organisation is not that organisation. This
        # is the single commonest error the precision check found: a
        # foundation matched to its college, a committee to its association.
        # Checked on every name field, because the marker can sit on a
        # trading name.
        marker_a = next((names.related_body_marker(v) for v in
                         (ea.get("legal_name"), ea.get("trading_name")) if
                         names.related_body_marker(v)), "")
        marker_b = next((names.related_body_marker(v) for v in
                         (eb.get("legal_name"), eb.get("trading_name")) if
                         names.related_body_marker(v)), "")
        if bool(marker_a) != bool(marker_b):
            refusals.append(
                f"one side is a related body ({marker_a or marker_b}), "
                f"the other is not")
    if conflicts and method == "abn_acn_derived":
        refusals.append("derived identifier match with " + "; ".join(conflicts))

    return {
        "method": method,
        "confidence": confidence,
        "matched_on": matched_on,
        "conflicts": conflicts,
        "refusals": refusals,
        "agreement": {
            "legal_name_a": ea.get("legal_name"),
            "legal_name_b": eb.get("legal_name"),
            "normalised_match": name_a == name_b and bool(name_a),
        },
    }


# --------------------------------------------------------------------------
# blocking: who is worth comparing to whom
# --------------------------------------------------------------------------

def blocks(reps: dict) -> dict:
    """Group records so we only compare ones that could plausibly match.

    Comparing everything to everything is O(n squared) and is the component
    that breaks first between 6 sources and 500. Blocking is what keeps it
    near linear: a pair is only considered if it shares a block.
    """
    buckets: dict[str, list] = defaultdict(list)
    for (source_id, key), entry in reps.items():
        row = entry["record"]
        entity, address = entity_of(row), address_of(row)
        handle = (source_id, key)

        if entity.get("abn") and abn_is_valid(entity["abn"]):
            buckets[f"abn:{entity['abn']}"].append(handle)
            # The ACN bridge: an ABN's last 9 digits often are the ACN, so put
            # it in the ACN bucket too and let compare() judge.
            buckets[f"acn:{entity['abn'][2:]}"].append(handle)
        if entity.get("acn") and acn_is_valid(entity["acn"]):
            buckets[f"acn:{entity['acn']}"].append(handle)

        for field in ("legal_name", "trading_name"):
            normalised = names.normalise(entity.get(field))
            if normalised and not names.too_generic(normalised):
                buckets[f"name:{normalised}"].append(handle)
    return buckets


# --------------------------------------------------------------------------
# the run
# --------------------------------------------------------------------------

def run(folder: str = "observations_deep") -> dict:
    started = time.time()
    rows = load(folder)
    reps = representatives(rows)
    buckets = blocks(reps)

    print(f"  {len(rows):,} observations")
    print(f"  {len(reps):,} (source, key) representatives")
    print(f"  {len(buckets):,} blocks")

    seen: set = set()
    links, unlinked = [], []

    for bucket in buckets.values():
        if len(bucket) < 2:
            continue
        for handle_a, handle_b in combinations(sorted(set(bucket)), 2):
            # We are asked for businesses appearing in more than one source.
            if handle_a[0] == handle_b[0]:
                continue
            pair = (handle_a, handle_b)
            if pair in seen:
                continue
            seen.add(pair)

            entry_a, entry_b = reps[handle_a], reps[handle_b]
            a, b = entry_a["record"], entry_b["record"]
            verdict = compare(a, b)
            if not verdict:
                continue

            key, key_kind = canonical_key(a if verdict["method"] != "acn_exact" else a)
            # Prefer whichever side carries a government identifier.
            for candidate in (a, b):
                candidate_key, kind = canonical_key(candidate)
                if kind in ("abn", "acn"):
                    key, key_kind = candidate_key, kind
                    break

            common = {
                "source_a_record": {"source_id": a["source_id"],
                                    "source_record_id": a["source_record_id"]},
                "source_b_record": {"source_id": b["source_id"],
                                    "source_record_id": b["source_record_id"]},
                "canonical_entity_key": key,
                "canonical_key_kind": key_kind,
                "confidence": verdict["confidence"],
                "evidence": {
                    "method": verdict["method"],
                    "matched_on": verdict["matched_on"],
                    "agreement": verdict["agreement"],
                    "conflicts": verdict["conflicts"],
                    "source_reliability_a": (a.get("confidence") or {}).get("source_reliability"),
                    "source_reliability_b": (b.get("confidence") or {}).get("source_reliability"),
                    "a_members_in_source": entry_a["member_count"],
                    "b_members_in_source": entry_b["member_count"],
                },
                "matcher_version": MATCHER_VERSION,
                "threshold_used": THRESHOLD,
                "decided_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "human_verdict": None,
            }

            if verdict["refusals"]:
                unlinked.append({**common, "refused_because": verdict["refusals"],
                                 "would_have_been": verdict["confidence"]})
            elif verdict["confidence"] >= THRESHOLD:
                links.append(common)
            else:
                unlinked.append({
                    **common,
                    "refused_because": [
                        f"confidence {verdict['confidence']} is below the "
                        f"threshold of {THRESHOLD}"],
                    "would_have_been": verdict["confidence"]})

    entities = derive_entities(links)
    return {"links": links, "unlinked": unlinked, "entities": entities,
            "seconds": round(time.time() - started, 2),
            "representatives": len(reps), "observations": len(rows)}


def derive_entities(links: list[dict]) -> list[dict]:
    """Group linked records under one key. A view, rebuildable from the links.

    We do not take a blind transitive closure. Two records only join a group
    through a link at or above the threshold, and a group holding two
    different government identifiers is a contradiction rather than a cluster.
    """
    members: dict[str, set] = defaultdict(set)
    for link in links:
        key = link["canonical_entity_key"]
        for side in ("source_a_record", "source_b_record"):
            members[key].add((link[side]["source_id"],
                              link[side]["source_record_id"]))

    entities = []
    for key, records in members.items():
        sources = sorted({source for source, _ in records})
        entities.append({
            "canonical_entity_key": key,
            "key_kind": key.split(":", 1)[0],
            "source_count": len(sources),
            "sources": sources,
            "records": [{"source_id": s, "source_record_id": r}
                        for s, r in sorted(records)],
        })
    entities.sort(key=lambda e: -e["source_count"])
    return entities


def main() -> None:
    print("Matching across sources\n")
    result = run()

    out = config.path("outputs")
    for name, data in (("links", result["links"]),
                       ("unlinked", result["unlinked"]),
                       ("entities", result["entities"])):
        with open(out / f"{name}.jsonl", "w") as f:
            for row in data:
                f.write(json.dumps(row, default=str) + "\n")

    by_method: dict[str, int] = defaultdict(int)
    for link in result["links"]:
        by_method[link["evidence"]["method"]] += 1
    by_reason: dict[str, int] = defaultdict(int)
    for row in result["unlinked"]:
        by_reason[row["refused_because"][0].split(":")[0][:52]] += 1

    print(f"\n  links proposed at threshold {THRESHOLD}: {len(result['links']):,}")
    for method, count in sorted(by_method.items(), key=lambda kv: -kv[1]):
        print(f"    {method:20} {count:6,}  confidence {METHOD_CONFIDENCE[method]}")

    print(f"\n  refused: {len(result['unlinked']):,}")
    for reason, count in sorted(by_reason.items(), key=lambda kv: -kv[1])[:8]:
        print(f"    {count:6,}  {reason}")

    print(f"\n  entities with records in more than one source: "
          f"{sum(1 for e in result['entities'] if e['source_count'] > 1):,}")
    spread: dict[int, int] = defaultdict(int)
    for e in result["entities"]:
        spread[e["source_count"]] += 1
    for n in sorted(spread, reverse=True):
        print(f"    in {n} sources: {spread[n]:,}")

    print(f"\n  {result['seconds']}s, 0 model calls, $0.00")
    print(f"  written to outputs/links.jsonl, unlinked.jsonl, entities.jsonl")


if __name__ == "__main__":
    main()
