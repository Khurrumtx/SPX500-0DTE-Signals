"""Technical-indicator feature engineering (pandas only, no TA-Lib).

Every feature at row *t* uses data up to and including *t* — no lookahead.
The label ``target`` is 1 when the next day's close is higher than today's.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

FEATURE_COLUMNS = [
    "ret_1", "ret_2", "ret_3", "ret_5", "ret_10",
    "sma_ratio", "ema_ratio", "macd_hist",
    "rsi_14", "bb_pctb", "atr_norm", "roc_10",
    "vol_z", "hl_range", "close_z20",
]


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50.0)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def build_features(prices: pd.DataFrame) -> pd.DataFrame:
    """Return a DataFrame of FEATURE_COLUMNS plus ``target``, NaN rows dropped.

    The final row has ``target`` = NaN (tomorrow unknown); it is kept so the
    live predictor can score "today", but training code must drop it.
    """
    c, v = prices["close"], prices["volume"]
    out = pd.DataFrame(index=prices.index)

    logc = np.log(c)
    for k in (1, 2, 3, 5, 10):
        out[f"ret_{k}"] = logc.diff(k)

    sma5, sma20 = c.rolling(5).mean(), c.rolling(20).mean()
    out["sma_ratio"] = sma5 / sma20 - 1

    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    out["ema_ratio"] = ema12 / ema26 - 1
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    out["macd_hist"] = (macd - signal) / c

    out["rsi_14"] = rsi(c) / 100.0

    std20 = c.rolling(20).std()
    upper = sma20 + 2 * std20
    lower = sma20 - 2 * std20
    out["bb_pctb"] = ((c - lower) / (upper - lower)).clip(-0.5, 1.5)

    out["atr_norm"] = atr(prices) / c
    out["roc_10"] = c.pct_change(10)

    vol_mean = v.rolling(20).mean()
    vol_std = v.rolling(20).std()
    out["vol_z"] = ((v - vol_mean) / vol_std).clip(-5, 5)

    out["hl_range"] = (prices["high"] - prices["low"]) / c
    out["close_z20"] = ((c - sma20) / std20).clip(-5, 5)

    out["target"] = (c.shift(-1) > c).astype(float)
    out.loc[out.index[-1], "target"] = np.nan  # tomorrow is unknown

    # Drop warm-up rows where any *feature* is NaN (keep the last row).
    out = out[out[FEATURE_COLUMNS].notna().all(axis=1)]
    return out
