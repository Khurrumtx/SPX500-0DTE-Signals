"""Price data loading: yfinance first, local CSV cache as fallback.

The cache file (``data/<TICKER>.csv``) doubles as a bundled snapshot so the
whole pipeline runs offline / in CI. Columns: date, open, high, low, close,
volume (close is split/dividend adjusted).
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from .config import Config

log = logging.getLogger(__name__)

COLUMNS = ["open", "high", "low", "close", "volume"]


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).lower() for c in df.columns]
    df.index = pd.to_datetime(df.index)
    df.index.name = "date"
    df = df[COLUMNS].astype(float).sort_index()
    df = df[~df.index.duplicated(keep="last")].dropna()
    return df


def fetch_yfinance(ticker: str, start: str) -> pd.DataFrame:
    import yfinance as yf

    raw = yf.download(ticker, start=start, auto_adjust=True, progress=False)
    if raw is None or raw.empty:
        raise RuntimeError(f"yfinance returned no data for {ticker}")
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    return _normalize(raw)


def load_cache(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["date"], index_col="date")
    return _normalize(df)


def save_cache(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, float_format="%.6f")


def load_prices(cfg: Config, refresh: bool = True) -> pd.DataFrame:
    """Load daily OHLCV. Tries yfinance when ``refresh``; falls back to the
    bundled CSV cache when the network is unavailable."""
    if refresh:
        try:
            df = fetch_yfinance(cfg.ticker, cfg.start)
            save_cache(df, cfg.cache_path)
            log.info("Fetched %d rows for %s via yfinance", len(df), cfg.ticker)
            return df
        except Exception as exc:  # network-restricted environments
            log.warning("yfinance fetch failed (%s); falling back to cache", exc)
    if cfg.cache_path.exists():
        df = load_cache(cfg.cache_path)
        log.info("Loaded %d cached rows for %s", len(df), cfg.ticker)
        return df
    raise FileNotFoundError(
        f"No network data and no cache at {cfg.cache_path}. "
        "Run `python cli.py fetch` from a machine with internet access."
    )
