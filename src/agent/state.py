"""The notebook every agent step writes into.

One file per source: runs/<source_id>/state.json.

It does three jobs at once:

  1. Carries state between steps. Step 3 needs what steps 1 and 2 found.
  2. Makes a crash cheap. A step already marked done is skipped on a rerun, so
     a failure in step 4 costs step 4, not the download.
  3. Is the evidence for the cost and timing numbers Part 5 asks for. Every
     step records how long it took and what it spent.
"""

from __future__ import annotations

import json
import time
import traceback
from contextlib import contextmanager
from pathlib import Path

from .. import config


class Run:
    """One source being onboarded."""

    def __init__(self, source_id: str, force: bool = False):
        self.source_id = source_id
        self.folder = config.path("runs") / source_id
        self.folder.mkdir(parents=True, exist_ok=True)
        self.file = self.folder / "state.json"
        self.force = force
        self.data = (json.loads(self.file.read_text()) if self.file.exists()
                     else {"source_id": source_id, "steps": {}})

    def save(self) -> None:
        self.file.write_text(json.dumps(self.data, indent=1, default=str))

    def done(self, step: str) -> bool:
        return (not self.force
                and self.data["steps"].get(step, {}).get("status") == "done")

    @contextmanager
    def step(self, name: str):
        """Run one step, or skip it if it already succeeded.

        Yields a dict the step writes its result into. On an exception the
        step is marked failed with the traceback, the state is saved, and the
        error is re-raised. Nothing earlier is lost.
        """
        if self.done(name):
            print(f"  {name}: already done, skipping")
            yield None
            return

        record = {"status": "running", "started": time.strftime("%H:%M:%S")}
        self.data["steps"][name] = record
        self.save()

        started = time.time()
        result: dict = {}
        try:
            yield result
        except Exception as err:
            record.update(status="failed", seconds=round(time.time() - started, 2),
                          error=f"{type(err).__name__}: {err}",
                          traceback=traceback.format_exc()[-1500:])
            self.save()
            raise
        record.update(status="done", seconds=round(time.time() - started, 2),
                      result=result)
        self.save()

    def result(self, step: str) -> dict:
        """What an earlier step produced."""
        return self.data["steps"].get(step, {}).get("result", {})

    def summary(self) -> dict:
        steps = self.data["steps"]
        return {
            "source_id": self.source_id,
            "steps_done": sum(1 for s in steps.values() if s["status"] == "done"),
            "steps_failed": [n for n, s in steps.items() if s["status"] == "failed"],
            "seconds": round(sum(s.get("seconds", 0) for s in steps.values()), 1),
            "model_calls": sum(s.get("result", {}).get("model_calls", 0)
                               for s in steps.values()),
            "usd": round(sum(s.get("result", {}).get("usd", 0)
                             for s in steps.values()), 5),
        }
