"""Load and normalize ES / SPX / VIX / CL (+ optional 0DTE & order-flow) CSVs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

COLUMN_ALIASES = {
    "timestamp": ["timestamp", "datetime", "date", "time", "ts"],
    "open": ["open", "o", "Open"],
    "high": ["high", "h", "High"],
    "low": ["low", "l", "Low"],
    "close": ["close", "c", "Close", "last", "settle", "settlement"],
    "volume": ["volume", "vol", "Volume"],
    "atm_iv": ["atm_iv", "iv", "implied_vol", "atm_implied_vol"],
    "call_volume": ["call_volume", "cvol", "call_vol"],
    "put_volume": ["put_volume", "pvol", "put_vol"],
    "call_oi": ["call_oi", "coi"],
    "put_oi": ["put_oi", "poi"],
    "gamma_flip": ["gamma_flip", "gamma_level", "zero_gamma"],
    "put_call_ratio": ["put_call_ratio", "pcr", "pc_ratio"],
    "bid_size": ["bid_size", "bidsize"],
    "ask_size": ["ask_size", "asksize"],
    "delta": ["delta", "of_delta", "volume_delta"],
    "cumulative_delta": ["cumulative_delta", "cum_delta", "cvd"],
    "large_print_count": ["large_print_count", "large_prints", "blocks"],
    "buy_aggression": ["buy_aggression", "buy_agg", "aggressive_buy"],
    "sell_aggression": ["sell_aggression", "sell_agg", "aggressive_sell"],
}


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    lower_map = {c.lower().strip(): c for c in df.columns}
    rename: dict[str, str] = {}
    used_src: set[str] = set()
    for canonical, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            key = alias.lower()
            if key in lower_map and lower_map[key] not in used_src:
                rename[lower_map[key]] = canonical
                used_src.add(lower_map[key])
                break
    out = df.rename(columns=rename)
    return out


def _read_table(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".parquet":
        df = pd.read_parquet(path)
    else:
        df = pd.read_csv(path)
    df = _normalize_columns(df)
    if "timestamp" not in df.columns:
        raise ValueError(f"{path}: missing timestamp/date column")
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    df = df.dropna(subset=["timestamp"]).sort_values("timestamp")
    df = df.drop_duplicates(subset=["timestamp"], keep="last")
    return df.reset_index(drop=True)


def _read_csv(path: Path) -> pd.DataFrame:
    return _read_table(path)


def load_ohlc(path: Path, name: str) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(
            f"Missing {name} data at {path}. Drop your CSV into data/raw/ "
            f"(see data/raw/README.md)."
        )
    df = _read_csv(path)
    for col in ("open", "high", "low", "close"):
        if col not in df.columns:
            raise ValueError(f"{path}: required column '{col}' not found")
        df[col] = pd.to_numeric(df[col], errors="coerce")
    if "volume" in df.columns:
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"])
    keep = ["timestamp", "open", "high", "low", "close"] + (
        ["volume"] if "volume" in df.columns else []
    )
    out = df[keep].copy()
    out = out.add_prefix(f"{name}_")
    out = out.rename(columns={f"{name}_timestamp": "timestamp"})
    return out


def load_optional(path: Path, name: str, *, keep: set[str] | None = None) -> pd.DataFrame | None:
    if not path.exists():
        return None
    df = _read_csv(path)
    known = set(COLUMN_ALIASES.keys())
    feature_cols = [c for c in df.columns if c != "timestamp" and c in known]
    if keep is not None:
        feature_cols = [c for c in feature_cols if c in keep]
    if not feature_cols:
        return None
    for c in feature_cols:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    out = df[["timestamp"] + feature_cols].copy()
    out = out.add_prefix(f"{name}_")
    out = out.rename(columns={f"{name}_timestamp": "timestamp"})
    return out


ORDERFLOW_COLS = {
    "bid_size",
    "ask_size",
    "delta",
    "cumulative_delta",
    "large_print_count",
    "buy_aggression",
    "sell_aggression",
}

SPX_0DTE_COLS = {
    "atm_iv",
    "call_volume",
    "put_volume",
    "call_oi",
    "put_oi",
    "gamma_flip",
    "put_call_ratio",
    "open",
    "high",
    "low",
    "close",
    "volume",
}


def load_orderflow_dir(orderflow_dir: Path, glob_pat: str = "*.csv") -> pd.DataFrame | None:
    paths = sorted(orderflow_dir.glob(glob_pat)) + sorted(
        orderflow_dir.glob(glob_pat.replace(".csv", ".parquet")) if ".csv" in glob_pat else []
    )
    # also pick parquet if glob is *.csv
    if glob_pat == "*.csv":
        paths = sorted(set(paths) | set(orderflow_dir.glob("*.parquet")))
    if not paths:
        return None
    frames = []
    for p in paths:
        try:
            df = load_optional(p, "of", keep=ORDERFLOW_COLS)
            if df is not None:
                frames.append(df)
        except Exception as exc:  # noqa: BLE001 — keep ingest resilient
            print(f"skip orderflow {p}: {exc}")
    if not frames:
        return None
    out = pd.concat(frames, ignore_index=True)
    out = out.sort_values("timestamp").drop_duplicates(subset=["timestamp"], keep="last")
    return out.reset_index(drop=True)


def load_universe(cfg: dict[str, Any]) -> pd.DataFrame:
    raw = Path(cfg["paths"]["raw_dir"])
    instruments = cfg["instruments"]

    es = load_ohlc(raw / instruments["es"], "es")
    spx = load_ohlc(raw / instruments["spx"], "spx")
    vix = load_ohlc(raw / instruments["vix"], "vix")
    cl = load_ohlc(raw / instruments["cl"], "cl")

    # Align on calendar day (daily bars). Intraday files still work — we resample later.
    frames = [es, spx, vix, cl]
    merged = frames[0]
    for fr in frames[1:]:
        merged = pd.merge(merged, fr, on="timestamp", how="outer")

    spx_0dte = load_optional(
        raw / instruments.get("spx_0dte", "spx_0dte.csv"),
        "spx0dte",
        keep=SPX_0DTE_COLS,
    )
    if spx_0dte is not None:
        merged = pd.merge(merged, spx_0dte, on="timestamp", how="left")

    of = load_orderflow_dir(
        Path(cfg["paths"]["orderflow_dir"]),
        instruments.get("orderflow_glob", "*.csv"),
    )
    if of is not None:
        merged = pd.merge(merged, of, on="timestamp", how="left")

    merged = merged.sort_values("timestamp").reset_index(drop=True)
    # Forward-fill cross-asset gaps within a reasonable window, then require SPX close
    value_cols = [c for c in merged.columns if c != "timestamp"]
    merged[value_cols] = merged[value_cols].ffill(limit=3)
    merged = merged.dropna(subset=["spx_close"]).reset_index(drop=True)
    return merged
