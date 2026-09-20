"""One place where all Gemini calls happen.

Everything goes through `ask()`. That way every call is priced, timed and
logged in the same way, and the cost numbers Part 5 asks for are computed from
a record of real calls rather than estimated at the end.

Needs GEMINI_API_KEY in a .env file. Copy .env.example to .env.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types

from . import config

CALL_LOG = config.ROOT / "runs" / "llm_calls.jsonl"

_client = None


def client() -> genai.Client:
    """One shared client. Fails with a useful message if the key is missing."""
    global _client
    if _client is None:
        load_dotenv(config.ROOT / ".env")
        key = os.getenv("GEMINI_API_KEY")
        if not key:
            raise SystemExit(
                "GEMINI_API_KEY is not set.\n"
                "Copy .env.example to .env and paste your key in.\n"
                "Get one at https://aistudio.google.com/apikey"
            )
        _client = genai.Client(api_key=key)
    return _client


def price_of(model: str, input_tokens: int, output_tokens: int) -> float:
    """Dollars for one call, using the price table in config/settings.yaml."""
    prices = config.load().get("prices", {}).get(model)
    if not prices:
        return 0.0
    return (input_tokens * prices["input"] + output_tokens * prices["output"]) / 1_000_000


def ask(model: str, prompt: str, schema: dict | None = None,
        step: str = "", thinking_budget: int | None = 0) -> dict:
    """Send one prompt. Return the parsed reply plus what it cost.

    `schema` asks Gemini for JSON shaped a particular way. Without it, Flash
    models sometimes wrap JSON in markdown fences and the parse fails.

    `thinking_budget` defaults to 0 because thinking tokens are billed at the
    output rate, which is four to five times the input rate.
    """
    settings = types.GenerateContentConfig()
    if schema is not None:
        settings.response_mime_type = "application/json"
        settings.response_schema = schema
    if thinking_budget is not None:
        settings.thinking_config = types.ThinkingConfig(thinking_budget=thinking_budget)

    started = time.time()
    reply = client().models.generate_content(
        model=model, contents=prompt, config=settings)
    seconds = time.time() - started

    usage = reply.usage_metadata
    input_tokens = usage.prompt_token_count or 0
    output_tokens = (usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0)
    cost = price_of(model, input_tokens, output_tokens)

    record = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "step": step,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "seconds": round(seconds, 2),
        "usd": round(cost, 6),
    }
    CALL_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(CALL_LOG, "a") as f:
        f.write(json.dumps(record) + "\n")

    text = reply.text or ""
    parsed = json.loads(text) if schema is not None else text

    return {"data": parsed, "usage": record}


def spend_so_far() -> dict:
    """Total tokens, seconds and dollars across every call ever made."""
    if not CALL_LOG.exists():
        return {"calls": 0, "input_tokens": 0, "output_tokens": 0,
                "seconds": 0.0, "usd": 0.0}
    rows = [json.loads(line) for line in open(CALL_LOG)]
    return {
        "calls": len(rows),
        "input_tokens": sum(r["input_tokens"] for r in rows),
        "output_tokens": sum(r["output_tokens"] for r in rows),
        "seconds": round(sum(r["seconds"] for r in rows), 1),
        "usd": round(sum(r["usd"] for r in rows), 4),
    }


if __name__ == "__main__":
    print(json.dumps(spend_so_far(), indent=1))
