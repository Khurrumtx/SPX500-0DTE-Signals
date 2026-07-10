import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nvda_predictor.features import FEATURE_COLUMNS, build_features


@pytest.fixture
def prices():
    rng = np.random.default_rng(0)
    n = 300
    close = 100 * np.exp(np.cumsum(rng.normal(0.0005, 0.02, n)))
    idx = pd.bdate_range("2022-01-03", periods=n)
    return pd.DataFrame(
        {
            "open": close * (1 + rng.normal(0, 0.003, n)),
            "high": close * (1 + np.abs(rng.normal(0, 0.01, n))),
            "low": close * (1 - np.abs(rng.normal(0, 0.01, n))),
            "close": close,
            "volume": rng.integers(1_000_000, 5_000_000, n).astype(float),
        },
        index=idx,
    )


def test_no_nan_features(prices):
    feats = build_features(prices)
    assert not feats[FEATURE_COLUMNS].isna().any().any()


def test_last_row_target_is_nan(prices):
    feats = build_features(prices)
    assert np.isnan(feats["target"].iloc[-1])
    assert not feats["target"].iloc[:-1].isna().any()


def test_target_matches_next_close(prices):
    feats = build_features(prices)
    closes = prices["close"]
    for date in feats.index[:-1][:50]:
        pos = closes.index.get_loc(date)
        expected = float(closes.iloc[pos + 1] > closes.iloc[pos])
        assert feats.loc[date, "target"] == expected


def test_rsi_bounds(prices):
    feats = build_features(prices)
    assert feats["rsi_14"].between(0, 1).all()


def test_no_lookahead(prices):
    """Features at date t must not change when future rows are appended."""
    feats_full = build_features(prices)
    feats_trunc = build_features(prices.iloc[:-20])
    common = feats_trunc.index[-5:]
    pd.testing.assert_frame_equal(
        feats_full.loc[common, FEATURE_COLUMNS],
        feats_trunc.loc[common, FEATURE_COLUMNS],
    )
