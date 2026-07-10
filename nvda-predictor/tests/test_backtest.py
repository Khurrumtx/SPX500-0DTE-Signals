import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nvda_predictor.backtest import backtest, classification_metrics


def test_perfect_foresight_beats_buy_and_hold():
    rng = np.random.default_rng(2)
    n = 500
    rets = rng.normal(0, 0.02, n)
    proba = (rets > 0).astype(float)  # oracle
    dates = pd.bdate_range("2020-01-01", periods=n)
    res = backtest(dates, proba, rets, threshold=0.5, cost_bps=0.0)
    assert res["strategy"]["total_return"] > res["buy_hold"]["total_return"]
    assert res["strategy"]["max_drawdown"] >= -0.0001  # never long a down day


def test_always_long_equals_buy_and_hold_without_costs():
    rng = np.random.default_rng(3)
    n = 300
    rets = rng.normal(0.001, 0.02, n)
    dates = pd.bdate_range("2020-01-01", periods=n)
    res = backtest(dates, np.ones(n), rets, threshold=0.5, cost_bps=0.0)
    assert abs(res["strategy"]["total_return"] - res["buy_hold"]["total_return"]) < 1e-9


def test_costs_reduce_returns():
    rng = np.random.default_rng(4)
    n = 300
    rets = rng.normal(0.001, 0.02, n)
    proba = rng.uniform(0, 1, n)
    dates = pd.bdate_range("2020-01-01", periods=n)
    free = backtest(dates, proba, rets, cost_bps=0.0)
    costly = backtest(dates, proba, rets, cost_bps=20.0)
    assert costly["strategy"]["total_return"] < free["strategy"]["total_return"]


def test_classification_metrics_keys():
    y = np.array([0, 1, 1, 0, 1], dtype=float)
    p = np.array([0.2, 0.8, 0.6, 0.4, 0.9])
    m = classification_metrics(y, p, 0.5)
    assert m["accuracy"] == 1.0
    assert 0 <= m["roc_auc"] <= 1
