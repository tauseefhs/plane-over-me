"""Load settings.toml, then overlay settings.local.toml if present."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .compat import tomllib

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
STANDING_DATA_DIR = DATA_DIR / "standing-data"
LOOKUP_DB = DATA_DIR / "lookup.db"


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load_settings() -> dict[str, Any]:
    settings: dict[str, Any] = {}
    for name in ("settings.toml", "settings.local.toml"):
        path = PROJECT_ROOT / name
        if path.exists():
            with path.open("rb") as f:
                settings = _merge(settings, tomllib.load(f))
    return settings


def resolve_path(path: str) -> Path:
    """Relative paths in settings are relative to the project root."""
    p = Path(path)
    return p if p.is_absolute() else PROJECT_ROOT / p
