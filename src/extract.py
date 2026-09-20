"""Stage D: run the one engine over every approved config.

Six configs, one engine. Nothing here knows anything about any particular
source. It reads a config, opens the file the config describes, and writes
canonical observations.

No model calls. That is the whole point: the money was spent once when the
agent wrote the config, and the per-record cost from here is zero.

The file is opened from the config's own `resource` section, not from anything
the agent left lying around, so this proves the config is sufficient on its own.

Run:
    python -m src.extract
    python -m src.extract --limit 1000
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

from . import config, engine, fetchfile

DEFAULT_LIMIT = 1000
DOWNLOADS = config.ROOT / "data" / "raw" / "sources"


def local_copy(cfg: dict) -> Path:
    """The file this config points at, downloading it if we do not have it."""
    url = cfg["resource"]["url"]
    stamp = hashlib.sha1(url.encode()).hexdigest()[:10]
    # Same naming the probe used, so an already-downloaded file is reused.
    matches = list(DOWNLOADS.glob(f"*_{stamp}.bin"))
    if matches:
        return matches[0]

    target = DOWNLOADS / f"{cfg['source_id'][:20]}_{stamp}.bin"
    got = fetchfile.download_for_preview(url, target)
    if not got.get("ok"):
        raise RuntimeError(f"could not download {url}: {got.get('error')}")
    return target


def extract_one(cfg: dict, limit: int, out_name: str = "observations") -> dict:
    started = time.time()
    path = local_copy(cfg)
    records = fetchfile.read_records(path, cfg["resource"], limit)
    result = engine.run(cfg, records)

    out_dir = config.path("outputs") / out_name
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{cfg['source_id']}.jsonl"
    with open(out_file, "w") as f:
        for observation in result["observations"]:
            f.write(json.dumps(observation, default=str) + "\n")

    filled = {c["canonical_field"]: c["fill_rate"] for c in result["coverage"]}
    with_identifier = sum(
        1 for o in result["observations"]
        if (o.get("entity") or {}).get("abn") or (o.get("entity") or {}).get("acn"))

    return {
        "source_id": cfg["source_id"],
        "licence": cfg["observation"]["licence"],
        "file": str(path),
        "rows_in": result["rows_in"],
        "observations": result["rows_out"],
        "with_abn_or_acn": with_identifier,
        "fields": filled,
        "failures": result["failures"],
        "seconds": round(time.time() - started, 2),
        "model_calls": 0,
        "usd": 0.0,
        "output_file": str(out_file),
    }


def main() -> None:
    limit = DEFAULT_LIMIT
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])
    # Part 2 ships 1,000 records per source, as the assignment specifies.
    # Part 3 needs a deeper pull to find the same business twice, so it writes
    # to its own directory rather than overwriting the deliverable.
    out_name = "observations"
    if "--out" in sys.argv:
        out_name = sys.argv[sys.argv.index("--out") + 1]

    configs = sorted(config.path("configs").glob("*.json"))
    if not configs:
        sys.exit("No approved configs. Approve some: python -m src.approve")

    print(f"Running the engine over {len(configs)} configs, "
          f"up to {limit} records each\n")

    reports = []
    for path in configs:
        cfg = json.loads(path.read_text())
        try:
            report = extract_one(cfg, limit, out_name)
        except Exception as err:
            print(f"  {cfg['source_id'][:44]:44} FAILED "
                  f"{type(err).__name__}: {str(err)[:50]}")
            reports.append({"source_id": cfg["source_id"], "error": str(err)})
            continue

        reports.append(report)
        print(f"  {report['source_id'][:44]:44} "
              f"{report['observations']:>5} observations, "
              f"{report['with_abn_or_acn']:>5} with ABN or ACN, "
              f"{report['seconds']:>5.1f}s")

    total = sum(r.get("observations", 0) for r in reports)
    identified = sum(r.get("with_abn_or_acn", 0) for r in reports)
    seconds = sum(r.get("seconds", 0) for r in reports)

    summary = {
        "engine_version": engine.ENGINE_VERSION,
        "sources": len(reports),
        "observations": total,
        "with_abn_or_acn": identified,
        "seconds": round(seconds, 2),
        "model_calls": 0,
        "usd": 0.0,
        "note": ("The engine makes no model calls. Per-record cost is zero. "
                 "All model spend happened once, when the agent wrote the "
                 "configs."),
        "per_source": reports,
    }
    out = config.path("outputs") / (
        "extraction_report.json" if out_name == "observations"
        else f"extraction_report_{out_name}.json")
    out.write_text(json.dumps(summary, indent=1))

    print()
    print(f"  {'TOTAL':44} {total:>5} observations, "
          f"{identified:>5} with ABN or ACN, {seconds:>5.1f}s")
    print(f"\n  model calls: 0.  cost: $0.00.  per-record cost: $0.00")
    print(f"\nWritten to outputs/{out_name}/ and {out}")


if __name__ == "__main__":
    main()
