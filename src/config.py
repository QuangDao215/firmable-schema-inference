"""Loads config/settings.yaml once and hands it to everything else."""

from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parent.parent
SETTINGS_FILE = ROOT / "config" / "settings.yaml"


def load() -> dict:
    with open(SETTINGS_FILE) as f:
        return yaml.safe_load(f)


def path(key: str) -> Path:
    """Turn a name from the paths section into a real directory, creating it."""
    p = ROOT / load()["paths"][key]
    p.mkdir(parents=True, exist_ok=True)
    return p
