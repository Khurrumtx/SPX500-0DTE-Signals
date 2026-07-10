"""Supervised dataset construction and walk-forward splitting."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .features import FEATURE_COLUMNS


@dataclass
class Supervised:
    """Aligned views of the same problem for both model families.

    For each index i: ``X_flat[i]`` is the feature row at date ``dates[i]``,
    ``X_seq[i]`` is the trailing ``window`` rows of features ending at that
    same date, and ``y[i]`` labels the *next* day's direction.
    """

    X_flat: np.ndarray  # (n, n_features)
    X_seq: np.ndarray   # (n, window, n_features)
    y: np.ndarray       # (n,)
    dates: pd.DatetimeIndex

    def __len__(self) -> int:
        return len(self.y)


def make_supervised(features: pd.DataFrame, window: int) -> Supervised:
    labeled = features.dropna(subset=["target"])
    values = features[FEATURE_COLUMNS].to_numpy(dtype=np.float32)

    # Row i of `labeled` sits at position pos[i] in `features`; a sequence
    # sample needs `window` rows of history ending at that position.
    positions = features.index.get_indexer(labeled.index)
    keep = positions >= window - 1
    positions = positions[keep]

    X_seq = np.stack([values[p - window + 1 : p + 1] for p in positions])
    X_flat = values[positions]
    y = labeled["target"].to_numpy(dtype=np.float32)[keep]
    dates = pd.DatetimeIndex(features.index[positions])
    return Supervised(X_flat=X_flat, X_seq=X_seq, y=y, dates=dates)


def latest_window(features: pd.DataFrame, window: int) -> tuple[np.ndarray, np.ndarray, pd.Timestamp]:
    """Feature row + trailing window for the most recent date (for live
    prediction of tomorrow's direction)."""
    values = features[FEATURE_COLUMNS].to_numpy(dtype=np.float32)
    if len(values) < window:
        raise ValueError(f"Need at least {window} rows, have {len(values)}")
    return values[-1], values[-window:], features.index[-1]


def walk_forward_splits(n: int, n_folds: int, test_size: int):
    """Yield (train_idx, test_idx) with an expanding training window.

    The last ``n_folds * test_size`` samples are split into consecutive test
    blocks; each fold trains on everything before its block.
    """
    first_test_start = n - n_folds * test_size
    if first_test_start < 500:
        raise ValueError("Not enough history for the requested folds")
    for k in range(n_folds):
        start = first_test_start + k * test_size
        end = min(start + test_size, n)
        yield np.arange(0, start), np.arange(start, end)
