"""Step C6: what a person sees before a config is allowed to ship.

The card is the whole point of the assignment's premise: adding a source should
be a review rather than a build. So the card has to be readable in a minute and
has to make disagreeing easy.

It shows four things:

  1. What the agent decided, and how sure it says it is.
  2. What it refused to map, and why. This is where a reviewer disagrees.
  3. What broke when the config was run on rows the model never saw.
  4. Five real records, raw on the left and canonical on the right.

Nothing on the card is the model's description of its own work. Every number
came from running the config.
"""

from __future__ import annotations

import json


def _record_pair(raw: dict, observation: dict) -> str:
    lines = ["  RAW:"]
    for key, value in list(raw.items())[:14]:
        text = str(value)
        if text.strip():
            lines.append(f"    {key[:28]:28} {text[:54]}")
    lines.append("  BECOMES:")
    for section in ("entity", "address"):
        for key, value in (observation.get(section) or {}).items():
            confidence = observation["confidence"]["field_confidence"].get(
                f"{section}.{key}")
            lines.append(f"    {section}.{key:22} {str(value)[:44]:44} "
                         f"conf {confidence}")
    lines.append(f"    record id  {observation['source_record_id']}")
    return "\n".join(lines)


def card(source: dict, cfg: dict, validation: dict, rounds: list,
         raw_records: list[dict]) -> str:
    out = []
    add = out.append

    add(f"# Review: {source['title']}")
    add("")
    add(f"Publisher     {source.get('organisation')}")
    add(f"Licence       {cfg['observation']['licence']}")
    add(f"File          {cfg['resource']['real_format']}"
        + (f"  (catalogue said {cfg['resource']['catalogue_format']})"
           if cfg['resource'].get('catalogue_format', '').upper()
           != cfg['resource']['real_format'] else ""))
    add(f"Landing page  {source.get('landing_page')}")
    add("")
    add(f"The agent took {len(rounds)} correction round(s). "
        + ("It still has an unresolved failure."
           if validation["needs_revision"] else "It resolved everything."))
    add("")

    add("## What it decided")
    add("")
    add(f"Record id: {cfg['record_id']['strategy']} "
        f"over {cfg['record_id']['fields']}")
    add(f"  why: {cfg['record_id']['note']}")
    add(f"Source reliability: {cfg['observation']['source_reliability']}")
    add("")

    add("## Fields it mapped")
    add("")
    add(f"  {'canonical field':26} {'from column':26} {'column':>7} "
        f"{'field':>6} {'conf':>5}")
    fill = {c["canonical_field"]: c for c in validation["coverage"]}
    for mapping in cfg["field_mappings"]:
        c = fill.get(mapping["canonical_field"], {})
        column = c.get("source_column_filled")
        add(f"  {mapping['canonical_field']:26} "
            f"{(mapping.get('source_field') or '(built)')[:26]:26} "
            f"{('%.0f%%' % (column * 100)) if column is not None else '   ?':>7} "
            f"{c.get('fill_rate', 0):>6.0%} "
            f"{mapping['field_confidence']:>5}")
        chain = " -> ".join(t["op"] for t in mapping["transforms"]) or "(none)"
        add(f"      {chain}")
    add("")

    add("## What it refused to map")
    add("")
    add("This is where you are most likely to disagree.")
    add("")
    for item in cfg["unfilled_canonical_fields"]:
        add(f"  {item['canonical_field']:26} {item['reason']}")
    add("")
    add(f"  Source columns left alone: {len(cfg['unmapped_source_fields'])}")
    for item in cfg["unmapped_source_fields"][:8]:
        add(f"    {item['source_field'][:30]:30} {item['reason'][:60]}")
    if len(cfg["unmapped_source_fields"]) > 8:
        add(f"    ... and {len(cfg['unmapped_source_fields']) - 8} more, "
            f"all listed in the config")
    add("")

    add(f"## What broke on {validation['holdout_rows']} rows the model "
        f"never saw")
    add("")
    if validation["failures"]:
        for f in validation["failures"]:
            add(f"  {f['canonical_field']:26} {f['problem']}")
            add(f"      {f['count']} rows ({f['share_of_rows']:.1%}), "
                f"real values: {json.dumps(f['examples'][:5], default=str)}")
    else:
        add("  Nothing.")
    add("")

    if rounds:
        add("## Correction rounds")
        add("")
        for r in rounds:
            verdict = ("kept" if r.get("kept") else
                       f"REJECTED: {r.get('why_rejected', 'made it worse')}")
            add(f"  round {r['round']}: failures {r['failures_before']} -> "
                f"{r['failures_after']}, {verdict}")
        add("")

    add("## Five real records")
    add("")
    for raw, observation in zip(raw_records[:5],
                                validation["sample_observations"][:5]):
        add(_record_pair(raw, observation))
        add("")

    add("## To decide")
    add("")
    add(f"  python -m src.approve {cfg['source_id']} --yes --by <your name>")
    add(f"  python -m src.approve {cfg['source_id']} --no  --by <your name> "
        f"--note \"what is wrong\"")
    add("")
    return "\n".join(out)
