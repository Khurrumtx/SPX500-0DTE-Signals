#!/usr/bin/env python3
"""Generate synthetic 24-month ES / SPX / VIX / CL (+ optional 0DTE) CSVs for demo.

Replace these with your real historical files (same column names) before relying
on the model. See data/raw/README.md.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from agent import REPO_ROOT
from agent.config import load_config


def _gbm(n: int, start: float, mu: float, sigma: float, rng: np.random.Generator) -> np.ndarray:
    shocks = rng.normal(mu / 252, sigma / np.sqrt(252), size=n)
    return start * np.cumprod(1 + shocks)


def generate(months: int = 24, seed: int = 42, out_dir: Path | None = None) -> Path:
    cfg = load_config()
    out = Path(out_dir) if out_dir else Path(cfg["paths"]["raw_dir"])
    out.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)

    # ~21 trading days / month
    n = months * 21
    dates = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=n, freq="C")
    dates = dates.tz_localize("UTC")

    # Correlated shocks: SPX/ES driven, VIX inverse, CL loosely correlated
    z = rng.standard_normal((n, 3))
    spx_shock = z[:, 0]
    vix_shock = -0.6 * z[:, 0] + 0.8 * z[:, 1]
    cl_shock = 0.25 * z[:, 0] + 0.97 * z[:, 2]

    spx_ret = 0.00015 + (0.14 / np.sqrt(252)) * spx_shock
    spx_close = 5200 * np.cumprod(1 + spx_ret)
    es_close = spx_close * (1.002 + 0.0003 * rng.standard_normal(n))
    vix_ret = 0.0 + (0.80 / np.sqrt(252)) * vix_shock
    vix_close = np.clip(16 * np.cumprod(1 + vix_ret), 10, 80)
    cl_ret = 0.00005 + (0.30 / np.sqrt(252)) * cl_shock
    cl_close = 75 * np.cumprod(1 + cl_ret)

    def ohlc(close: np.ndarray, vol_scale: float) -> pd.DataFrame:
        noise = np.abs(rng.normal(0, vol_scale, size=n))
        high = close * (1 + noise)
        low = close * (1 - noise)
        open_ = np.r_[close[0], close[:-1]] * (1 + rng.normal(0, vol_scale / 2, n))
        volume = rng.integers(50_000, 500_000, size=n)
        return pd.DataFrame(
            {
                "timestamp": dates,
                "open": np.round(open_, 2),
                "high": np.round(np.maximum.reduce([open_, high, close]), 2),
                "low": np.round(np.minimum.reduce([open_, low, close]), 2),
                "close": np.round(close, 2),
                "volume": volume,
            }
        )

    es = ohlc(es_close, 0.004)
    spx = ohlc(spx_close, 0.004)
    vix = ohlc(vix_close, 0.02)
    cl = ohlc(cl_close, 0.01)

    # Optional synthetic 0DTE aggregates
    atm_iv = (vix_close / 100) * (0.95 + 0.1 * rng.random(n))
    call_vol = rng.integers(10_000, 200_000, size=n)
    put_vol = (call_vol * (0.8 + 0.6 * rng.random(n))).astype(int)
    spx_0dte = pd.DataFrame(
        {
            "timestamp": dates,
            "atm_iv": np.round(atm_iv, 4),
            "call_volume": call_vol,
            "put_volume": put_vol,
            "put_call_ratio": np.round(put_vol / call_vol, 3),
            "gamma_flip": np.round(spx_close * (1 + rng.normal(0, 0.002, n)), 2),
        }
    )

    instruments = cfg["instruments"]
    es.to_csv(out / instruments["es"], index=False)
    spx.to_csv(out / instruments["spx"], index=False)
    vix.to_csv(out / instruments["vix"], index=False)
    cl.to_csv(out / instruments["cl"], index=False)
    spx_0dte.to_csv(out / instruments["spx_0dte"], index=False)

    print(f"Wrote {months} months ({n} days) of sample data → {out}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--months", type=int, default=24)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    generate(months=args.months, seed=args.seed, out_dir=args.out)


if __name__ == "__main__":
    main()
