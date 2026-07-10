"""Live prediction: probability that the next session closes higher."""

from __future__ import annotations

import numpy as np

from .config import Config
from .data import load_prices
from .dataset import latest_window
from .features import build_features
from .persist import load_meta, load_model_and_scaler


def predict_latest(cfg: Config, refresh: bool = True) -> dict:
    model, scaler = load_model_and_scaler(cfg)
    meta = load_meta(cfg)

    prices = load_prices(cfg, refresh=refresh)
    features = build_features(prices)
    _, seq, as_of = latest_window(features, meta["window"])

    seq = scaler.transform(seq).astype(np.float32)[None, ...]
    p_up = float(model.predict(seq, verbose=0).reshape(-1)[0])
    return {
        "ticker": cfg.ticker,
        "as_of": str(as_of.date()),
        "close": round(float(prices["close"].iloc[-1]), 2),
        "p_up": round(p_up, 4),
        "direction": "UP" if p_up >= cfg.prob_threshold else "DOWN",
        "confidence": round(abs(p_up - 0.5) * 200, 1),  # 0..100
        "model_trained_through": meta.get("train_end"),
        "disclaimer": "Educational only — not financial advice.",
    }
