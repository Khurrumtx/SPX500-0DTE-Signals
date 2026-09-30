"""Config loader for the SPX 0DTE agent."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from . import DEFAULT_CONFIG, REPO_ROOT


def load_config(path: Path | str | None = None) -> dict[str, Any]:
    cfg_path = Path(path) if path else DEFAULT_CONFIG
    with cfg_path.open() as f:
        cfg = yaml.safe_load(f)
    # Resolve relative paths against repo root
    for key in ("raw_dir", "orderflow_dir", "processed_dir", "models_dir"):
        p = Path(cfg["paths"][key])
        if not p.is_absolute():
            cfg["paths"][key] = str(REPO_ROOT / p)
    for key in ("signal_out", "dashboard_signal_out"):
        p = Path(cfg["paths"][key])
        if not p.is_absolute():
            cfg["paths"][key] = str(REPO_ROOT / p)
    return cfg
