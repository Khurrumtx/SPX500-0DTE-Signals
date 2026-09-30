"""Label construction: next-bar SPX direction → CALL / PUT / WAIT."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

LABEL_MAP = {"WAIT": 0, "CALL": 1, "PUT": 2}
INV_LABEL_MAP = {v: k for k, v in LABEL_MAP.items()}


def add_labels(df: pd.DataFrame, cfg: dict[str, Any]) -> pd.DataFrame:
    out = df.copy()
    horizon = int(cfg["labels"]["horizon_bars"])
    call_th = float(cfg["labels"]["call_threshold"])
    put_th = float(cfg["labels"]["put_threshold"])

    out["fwd_ret"] = out["spx_close"].shift(-horizon) / out["spx_close"] - 1.0
    labels = np.full(len(out), LABEL_MAP["WAIT"], dtype=int)
    labels[out["fwd_ret"].to_numpy() >= call_th] = LABEL_MAP["CALL"]
    labels[out["fwd_ret"].to_numpy() <= put_th] = LABEL_MAP["PUT"]
    # Last `horizon` rows have no label
    labels[-horizon:] = -1
    out["label"] = labels
    return out
