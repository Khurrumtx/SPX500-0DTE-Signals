"""Self-retrain loop: retrain when new bars / order-flow arrive, then emit signal."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from .load_data import load_universe
from .orderflow import orderflow_status
from .signal_writer import write_signal
from .train import META_NAME, train_model


STATE_NAME = "retrain_state.json"


def _state_path(cfg: dict[str, Any]) -> Path:
    return Path(cfg["paths"]["models_dir"]) / STATE_NAME


def _load_state(cfg: dict[str, Any]) -> dict[str, Any]:
    p = _state_path(cfg)
    if not p.exists():
        return {}
    return json.loads(p.read_text())


def _save_state(cfg: dict[str, Any], state: dict[str, Any]) -> None:
    p = _state_path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2))


def should_retrain(cfg: dict[str, Any]) -> tuple[bool, str]:
    """Decide whether enough new information arrived to retrain."""
    models_dir = Path(cfg["paths"]["models_dir"])
    meta_path = models_dir / META_NAME
    min_new = int(cfg["train"].get("retrain_min_new_rows", 5))

    raw = load_universe(cfg)
    latest_ts = raw["timestamp"].max().isoformat()
    n_rows = len(raw)
    of = orderflow_status(cfg)

    state = _load_state(cfg)
    if not meta_path.exists():
        return True, "no model yet — initial train"

    prev_rows = int(state.get("rows", 0))
    prev_ts = state.get("data_end")
    prev_of = int(state.get("orderflow_files", 0))

    new_rows = n_rows - prev_rows
    if new_rows >= min_new:
        return True, f"{new_rows} new bars since last train"
    if of["file_count"] > prev_of:
        return True, f"new order-flow files ({prev_of} → {of['file_count']})"
    if prev_ts and latest_ts > prev_ts and new_rows > 0:
        return True, f"data advanced to {latest_ts}"
    return False, "no material new data"


def retrain_if_needed(
    cfg: dict[str, Any],
    *,
    force: bool = False,
    write_sig: bool = True,
) -> dict[str, Any]:
    do_it, reason = (True, "forced") if force else should_retrain(cfg)
    result: dict[str, Any] = {"retrained": False, "reason": reason}

    if do_it:
        print(f"Retraining: {reason}")
        meta = train_model(cfg)
        of = orderflow_status(cfg)
        raw = load_universe(cfg)
        _save_state(
            cfg,
            {
                "rows": len(raw),
                "data_end": raw["timestamp"].max().isoformat(),
                "orderflow_files": of["file_count"],
                "last_train_accuracy": meta.get("walk_forward_accuracy"),
            },
        )
        result["retrained"] = True
        result["meta"] = {
            "walk_forward_accuracy": meta.get("walk_forward_accuracy"),
            "rows": meta.get("rows"),
            "data_end": meta.get("data_end"),
        }
    else:
        print(f"Skip retrain: {reason}")

    if write_sig:
        payload = write_signal(cfg)
        result["signal"] = {
            "signal": payload["signal"],
            "confidence": payload["confidence"],
            "timestamp": payload["timestamp"],
        }
    return result
