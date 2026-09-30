"""Feature engineering for ES / SPX / VIX / CL (+ optional 0DTE & order-flow)."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _realized_vol(returns: pd.Series, window: int) -> pd.Series:
    return returns.rolling(window).std() * np.sqrt(252)


def build_features(df: pd.DataFrame, cfg: dict[str, Any]) -> pd.DataFrame:
    out = df.copy()
    feat_cfg = cfg["features"]
    ret_windows = feat_cfg.get("return_windows", [1, 3, 5, 10, 20])
    vol_windows = feat_cfg.get("vol_windows", [5, 10, 20])
    rsi_period = int(feat_cfg.get("rsi_period", 14))

    series_map = {
        "es": "es_close",
        "spx": "spx_close",
        "vix": "vix_close",
        "cl": "cl_close",
    }

    for name, col in series_map.items():
        if col not in out.columns:
            continue
        rets = out[col].pct_change()
        out[f"{name}_ret_1"] = rets
        for w in ret_windows:
            if w == 1:
                continue
            out[f"{name}_ret_{w}"] = out[col].pct_change(w)
        for w in vol_windows:
            out[f"{name}_rvol_{w}"] = _realized_vol(rets, w)
        out[f"{name}_rsi_{rsi_period}"] = _rsi(out[col], rsi_period)
        out[f"{name}_range"] = (out[f"{name}_high"] - out[f"{name}_low"]) / out[col]
        if f"{name}_volume" in out.columns:
            out[f"{name}_vol_z"] = (
                out[f"{name}_volume"] - out[f"{name}_volume"].rolling(20).mean()
            ) / out[f"{name}_volume"].rolling(20).std()

    # Cross-asset relationships (ES ↔ VIX ↔ CL)
    if feat_cfg.get("include_cross_asset", True):
        if "es_ret_1" in out.columns and "vix_ret_1" in out.columns:
            out["es_vix_corr_10"] = out["es_ret_1"].rolling(10).corr(out["vix_ret_1"])
            out["es_vix_corr_20"] = out["es_ret_1"].rolling(20).corr(out["vix_ret_1"])
        if "es_ret_1" in out.columns and "cl_ret_1" in out.columns:
            out["es_cl_corr_10"] = out["es_ret_1"].rolling(10).corr(out["cl_ret_1"])
        if "spx_close" in out.columns and "es_close" in out.columns:
            out["spx_es_basis"] = (out["es_close"] - out["spx_close"]) / out["spx_close"]
        if "vix_close" in out.columns and "spx_rvol_10" in out.columns:
            out["vix_vs_rvol"] = out["vix_close"] / (out["spx_rvol_10"] * 100).replace(0, np.nan)

    # Optional SPX 0DTE aggregates
    if "spx0dte_put_call_ratio" in out.columns:
        out["pcr"] = out["spx0dte_put_call_ratio"]
    elif "spx0dte_put_volume" in out.columns and "spx0dte_call_volume" in out.columns:
        out["pcr"] = out["spx0dte_put_volume"] / out["spx0dte_call_volume"].replace(0, np.nan)
    if "spx0dte_atm_iv" in out.columns:
        out["atm_iv"] = out["spx0dte_atm_iv"]
        if "spx_rvol_10" in out.columns:
            out["iv_rv_spread"] = out["atm_iv"] - out["spx_rvol_10"]
    if "spx0dte_gamma_flip" in out.columns and "spx_close" in out.columns:
        out["dist_gamma_flip"] = (out["spx_close"] - out["spx0dte_gamma_flip"]) / out["spx_close"]

    # Optional order-flow (populated when subscription dumps land in data/orderflow/)
    if feat_cfg.get("include_orderflow", True):
        if "of_delta" in out.columns:
            out["of_delta_z"] = (
                out["of_delta"] - out["of_delta"].rolling(20).mean()
            ) / out["of_delta"].rolling(20).std()
        if "of_cumulative_delta" in out.columns:
            out["of_cvd_slope"] = out["of_cumulative_delta"].diff(5)
        if "of_buy_aggression" in out.columns and "of_sell_aggression" in out.columns:
            out["of_imbalance"] = (
                out["of_buy_aggression"] - out["of_sell_aggression"]
            ) / (out["of_buy_aggression"] + out["of_sell_aggression"] + 1e-9)
        if "of_bid_size" in out.columns and "of_ask_size" in out.columns:
            out["of_book_imbalance"] = (
                out["of_bid_size"] - out["of_ask_size"]
            ) / (out["of_bid_size"] + out["of_ask_size"] + 1e-9)

    # Session calendar features
    out["dow"] = out["timestamp"].dt.dayofweek
    out["month"] = out["timestamp"].dt.month

    return out


def feature_columns(df: pd.DataFrame) -> list[str]:
    """Columns safe to feed the model (exclude raw OHLC & target)."""
    exclude_exact = {"timestamp", "label", "fwd_ret", "signal"}
    exclude_raw_suffixes = ("_open", "_high", "_low", "_close", "_volume")
    cols = []
    for c in df.columns:
        if c in exclude_exact:
            continue
        if any(c.endswith(sfx) for sfx in exclude_raw_suffixes):
            prefix = c.rsplit("_", 1)[0]
            if prefix in {"es", "spx", "vix", "cl", "spx0dte", "of"}:
                continue
        if df[c].dtype.kind not in "iufb":
            continue
        # Drop all-NaN columns (e.g. order-flow before subscription)
        if df[c].isna().all():
            continue
        cols.append(c)
    return sorted(set(cols))
