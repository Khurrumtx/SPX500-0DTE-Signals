"""Order-flow ingest hook for a future options order-flow subscription.

Drop vendor CSV/Parquet dumps into data/orderflow/. Recognized columns are
merged on timestamp during feature build. When the subscription is live,
call `ingest_orderflow_file()` (or just land files and run `retrain`).
"""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from .load_data import COLUMN_ALIASES, _normalize_columns


REQUIRED_HINTS = ("timestamp", "date", "datetime", "time")


def validate_orderflow_frame(df: pd.DataFrame) -> list[str]:
    """Return list of human-readable issues (empty = ok)."""
    issues: list[str] = []
    norm = _normalize_columns(df)
    if "timestamp" not in norm.columns:
        issues.append("missing timestamp/date column")
    known = set(COLUMN_ALIASES.keys()) - {"timestamp", "open", "high", "low", "close", "volume"}
    of_cols = [c for c in norm.columns if c in known]
    if not of_cols:
        issues.append(
            "no recognized order-flow columns "
            f"(expected one of: {sorted(known)})"
        )
    return issues


def ingest_orderflow_file(
    src: Path | str,
    cfg: dict[str, Any],
    *,
    copy: bool = True,
) -> Path:
    """Validate and place an order-flow dump into data/orderflow/."""
    src_path = Path(src)
    if not src_path.exists():
        raise FileNotFoundError(src_path)

    if src_path.suffix.lower() == ".parquet":
        df = pd.read_parquet(src_path)
    else:
        df = pd.read_csv(src_path)

    issues = validate_orderflow_frame(df)
    if issues:
        raise ValueError("Order-flow file rejected: " + "; ".join(issues))

    dest_dir = Path(cfg["paths"]["orderflow_dir"])
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = dest_dir / f"orderflow_{stamp}{src_path.suffix.lower() or '.csv'}"
    if copy:
        shutil.copy2(src_path, dest)
    else:
        src_path.rename(dest)
    print(f"Ingested order-flow → {dest}")
    return dest


def orderflow_status(cfg: dict[str, Any]) -> dict[str, Any]:
    dest_dir = Path(cfg["paths"]["orderflow_dir"])
    files = sorted(dest_dir.glob("*.csv")) + sorted(dest_dir.glob("*.parquet"))
    return {
        "dir": str(dest_dir),
        "file_count": len(files),
        "files": [p.name for p in files[-10:]],
        "ready_for_features": len(files) > 0,
        "note": (
            "Place subscription dumps here. Retrain picks them up automatically "
            "via include_orderflow features."
        ),
    }
