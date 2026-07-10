"""Backtest a direction classifier as a long/cash strategy.

Position for day t+1 is decided at the close of day t from the predicted
probability. Transaction costs are charged on position changes.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import (
    accuracy_score,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)

TRADING_DAYS = 252


def classification_metrics(y_true: np.ndarray, proba: np.ndarray, threshold: float) -> dict:
    pred = (proba >= threshold).astype(int)
    out = {
        "accuracy": accuracy_score(y_true, pred),
        "precision": precision_score(y_true, pred, zero_division=0),
        "recall": recall_score(y_true, pred, zero_division=0),
        "f1": f1_score(y_true, pred, zero_division=0),
        "base_rate_up": float(np.mean(y_true)),
        "n": int(len(y_true)),
    }
    try:
        out["roc_auc"] = roc_auc_score(y_true, proba)
    except ValueError:
        out["roc_auc"] = float("nan")
    return {k: round(float(v), 4) if isinstance(v, float) else v for k, v in out.items()}


def _perf(daily_returns: pd.Series) -> dict:
    equity = (1 + daily_returns).cumprod()
    total = equity.iloc[-1] - 1
    years = len(daily_returns) / TRADING_DAYS
    cagr = (1 + total) ** (1 / years) - 1 if years > 0 else np.nan
    vol = daily_returns.std() * np.sqrt(TRADING_DAYS)
    sharpe = (daily_returns.mean() * TRADING_DAYS) / vol if vol > 0 else np.nan
    drawdown = equity / equity.cummax() - 1
    return {
        "total_return": round(float(total), 4),
        "cagr": round(float(cagr), 4),
        "annual_vol": round(float(vol), 4),
        "sharpe": round(float(sharpe), 3),
        "max_drawdown": round(float(drawdown.min()), 4),
    }


def backtest(
    dates: pd.DatetimeIndex,
    proba: np.ndarray,
    next_day_returns: np.ndarray,
    threshold: float = 0.5,
    cost_bps: float = 5.0,
) -> dict:
    """``next_day_returns[i]`` is the simple close-to-close return realized
    the day after ``dates[i]`` — exactly what a signal at ``dates[i]``
    captures."""
    position = (proba >= threshold).astype(float)
    trades = np.abs(np.diff(position, prepend=0.0))
    cost = trades * cost_bps / 1e4
    strat_returns = pd.Series(position * next_day_returns - cost, index=dates)
    bh_returns = pd.Series(next_day_returns, index=dates)

    result = {
        "strategy": _perf(strat_returns),
        "buy_hold": _perf(bh_returns),
        "exposure": round(float(position.mean()), 4),
        "n_trades": int(trades.sum()),
        "threshold": threshold,
        "cost_bps": cost_bps,
    }
    result["equity_curve"] = pd.DataFrame(
        {
            "strategy": (1 + strat_returns).cumprod(),
            "buy_hold": (1 + bh_returns).cumprod(),
        }
    )
    return result
