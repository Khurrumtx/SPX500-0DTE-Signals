"""Walk-forward evaluation and final-model training."""

from __future__ import annotations

import logging
import time

import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler

from .backtest import backtest, classification_metrics
from .config import Config
from .data import load_prices
from .dataset import Supervised, make_supervised, walk_forward_splits
from .features import FEATURE_COLUMNS, build_features
from .models.baselines import make_baselines
from .models.lstm import predict_proba, train_lstm

log = logging.getLogger(__name__)


def scale_fold(scaler: StandardScaler, sup: Supervised, idx: np.ndarray):
    """Scale both representations with a scaler fit elsewhere (train only)."""
    flat = scaler.transform(sup.X_flat[idx])
    seq = sup.X_seq[idx]
    seq = scaler.transform(seq.reshape(-1, seq.shape[-1])).reshape(seq.shape)
    return flat.astype(np.float32), seq.astype(np.float32)


def _next_day_returns(prices: pd.DataFrame, dates: pd.DatetimeIndex) -> np.ndarray:
    nxt = (prices["close"].shift(-1) / prices["close"] - 1).reindex(dates)
    return nxt.to_numpy(dtype=np.float64)


def run_walk_forward(cfg: Config, refresh: bool = False, verbose: int = 0) -> dict:
    """Evaluate LSTM + baselines on expanding-window folds.

    Returns {"oos": DataFrame, "models": {name: {"classification": ...,
    "backtest": ...}}, ...}. All probabilities in ``oos`` are strictly
    out-of-sample.
    """
    prices = load_prices(cfg, refresh=refresh)
    features = build_features(prices)
    sup = make_supervised(features, cfg.window)
    next_ret = _next_day_returns(prices, sup.dates)

    model_names = ["lstm", *make_baselines(cfg.seed).keys()]
    oos_rows: list[pd.DataFrame] = []

    for fold, (tr, te) in enumerate(
        walk_forward_splits(len(sup), cfg.n_folds, cfg.test_days_per_fold)
    ):
        t0 = time.time()
        scaler = StandardScaler().fit(sup.X_flat[tr])
        Xf_tr, Xs_tr = scale_fold(scaler, sup, tr)
        Xf_te, Xs_te = scale_fold(scaler, sup, te)

        fold_df = pd.DataFrame(
            {
                "fold": fold,
                "date": sup.dates[te],
                "y": sup.y[te],
                "next_ret": next_ret[te],
            }
        )

        lstm = train_lstm(Xs_tr, sup.y[tr], cfg, verbose=verbose)
        fold_df["proba_lstm"] = predict_proba(lstm, Xs_te)

        for name, mdl in make_baselines(cfg.seed).items():
            mdl.fit(Xf_tr, sup.y[tr])
            fold_df[f"proba_{name}"] = mdl.predict_proba(Xf_te)[:, 1]

        oos_rows.append(fold_df)
        log.info(
            "fold %d: train=%d test=%d (%s..%s) in %.0fs",
            fold, len(tr), len(te),
            fold_df["date"].iloc[0].date(), fold_df["date"].iloc[-1].date(),
            time.time() - t0,
        )

    oos = pd.concat(oos_rows, ignore_index=True)

    results: dict[str, dict] = {}
    for name in model_names:
        proba = oos[f"proba_{name}"].to_numpy()
        bt = backtest(
            pd.DatetimeIndex(oos["date"]),
            proba,
            oos["next_ret"].to_numpy(),
            threshold=cfg.prob_threshold,
            cost_bps=cfg.cost_bps,
        )
        bt.pop("equity_curve")
        results[name] = {
            "classification": classification_metrics(
                oos["y"].to_numpy(), proba, cfg.prob_threshold
            ),
            "backtest": bt,
        }

    return {
        "oos": oos,
        "models": results,
        "data_start": str(sup.dates[0].date()),
        "data_end": str(sup.dates[-1].date()),
        "n_samples": len(sup),
        "features": FEATURE_COLUMNS,
    }


def train_final(cfg: Config, refresh: bool = False, verbose: int = 0):
    """Train the deployable LSTM on all available history."""
    prices = load_prices(cfg, refresh=refresh)
    features = build_features(prices)
    sup = make_supervised(features, cfg.window)

    scaler = StandardScaler().fit(sup.X_flat)
    seq = scaler.transform(
        sup.X_seq.reshape(-1, sup.X_seq.shape[-1])
    ).reshape(sup.X_seq.shape).astype(np.float32)
    model = train_lstm(seq, sup.y, cfg, verbose=verbose)
    meta = {
        "ticker": cfg.ticker,
        "window": cfg.window,
        "features": FEATURE_COLUMNS,
        "train_start": str(sup.dates[0].date()),
        "train_end": str(sup.dates[-1].date()),
        "n_samples": len(sup),
    }
    return model, scaler, meta
