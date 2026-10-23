"""Load config.yaml into a plain nested dict, with dotted-key overrides for tests and scripts."""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = PROJECT_ROOT / "config.yaml"


def load_config(path: str | Path | None = None, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """Read the YAML config and apply overrides such as {"policy.idle_window_s": 5}."""
    with open(path or DEFAULT_CONFIG, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    for dotted, value in (overrides or {}).items():
        set_dotted(cfg, dotted, value)
    return cfg


def set_dotted(cfg: dict[str, Any], dotted: str, value: Any) -> None:
    """Set cfg["a"]["b"] = value for dotted == "a.b", creating sections as needed."""
    *parents, leaf = dotted.split(".")
    node = cfg
    for key in parents:
        node = node.setdefault(key, {})
    node[leaf] = value


def with_overrides(cfg: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    """Return a deep copy of cfg with overrides applied (the original is untouched)."""
    out = copy.deepcopy(cfg)
    for dotted, value in overrides.items():
        set_dotted(out, dotted, value)
    return out


def resolve_path(p: str | Path) -> Path:
    """Paths in the config are relative to the project root."""
    p = Path(p)
    return p if p.is_absolute() else PROJECT_ROOT / p
