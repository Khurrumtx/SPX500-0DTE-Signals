"""Inference: load model, score latest bar, map to CALL/PUT/WAIT."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd

from .features import build_features, feature_columns
from .labels import INV_LABEL_MAP
from .train import MODEL_NAME, prepare_dataset


def load_bundle(cfg: dict[str, Any]) -> dict[str, Any]:
    path = Path(cfg["paths"]["models_dir"]) / MODEL_NAME
    if not path.exists():
        raise FileNotFoundError(f"No trained model at {path}. Run train first.")
    return joblib.load(path)


def predict_latest(cfg: dict[str, Any]) -> dict[str, Any]:
    bundle = load_bundle(cfg)
    model = bundle["model"]
    feat_cols: list[str] = bundle["features"]

    df, _ = prepare_dataset(cfg)
    # Score the last row that has features (label may be -1 for last bar — that's fine)
    row = df.iloc[[-1]].copy()
    for c in feat_cols:
        if c not in row.columns:
            row[c] = np.nan
    X = row[feat_cols]
    proba = model.predict_proba(X)[0]
    classes = list(model.named_steps["clf"].classes_)
    # Align probabilities to WAIT/CALL/PUT
    p_map = {int(c): float(p) for c, p in zip(classes, proba)}
    p_wait = p_map.get(0, 0.0)
    p_call = p_map.get(1, 0.0)
    p_put = p_map.get(2, 0.0)

    best = max([(p_wait, "WAIT"), (p_call, "CALL"), (p_put, "PUT")], key=lambda t: t[0])
    signal = best[1]
    confidence = int(round(best[0] * 100))
    floor = int(cfg["train"].get("confidence_floor", 45))
    if confidence < floor or signal == "WAIT":
        signal = "WAIT" if signal == "WAIT" or confidence < floor else signal
        if confidence < floor:
            signal = "WAIT"

    spx = float(row["spx_close"].iloc[0])
    rsi = float(row.get("spx_rsi_14", pd.Series([np.nan])).iloc[0]) if "spx_rsi_14" in row else float("nan")
    rvol = float(row.get("spx_rvol_10", pd.Series([np.nan])).iloc[0]) if "spx_rvol_10" in row else float("nan")

    direction = {
        "CALL": "BULLISH",
        "PUT": "BEARISH",
        "WAIT": "NEUTRAL",
    }[signal]

    return {
        "signal": signal if signal != "WAIT" else "WAIT",
        "direction": direction,
        "confidence": confidence,
        "probabilities": {"WAIT": p_wait, "CALL": p_call, "PUT": p_put},
        "spx": spx,
        "rsi": None if np.isnan(rsi) else round(rsi, 2),
        "realized_vol": None if np.isnan(rvol) else round(rvol, 4),
        "timestamp": row["timestamp"].iloc[0].isoformat(),
        "features_used": len(feat_cols),
    }
