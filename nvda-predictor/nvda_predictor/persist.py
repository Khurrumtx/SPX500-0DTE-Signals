"""Saving/loading trained artifacts (model, scaler, metadata, OOS preds)."""

from __future__ import annotations

import json
from pathlib import Path

import joblib
import pandas as pd

from .config import Config

MODEL_FILE = "lstm.keras"
SCALER_FILE = "scaler.joblib"
META_FILE = "meta.json"
OOS_FILE = "oos_predictions.csv"


def save_artifacts(cfg: Config, model, scaler, meta: dict, oos: pd.DataFrame | None = None) -> None:
    d = cfg.artifacts_dir
    d.mkdir(parents=True, exist_ok=True)
    model.save(d / MODEL_FILE)
    joblib.dump(scaler, d / SCALER_FILE)
    (d / META_FILE).write_text(json.dumps(meta, indent=2, default=str))
    if oos is not None:
        oos.to_csv(d / OOS_FILE, index=False)


def load_meta(cfg: Config) -> dict:
    return json.loads((cfg.artifacts_dir / META_FILE).read_text())


def load_model_and_scaler(cfg: Config):
    from tensorflow import keras

    model = keras.models.load_model(cfg.artifacts_dir / MODEL_FILE)
    scaler = joblib.load(cfg.artifacts_dir / SCALER_FILE)
    return model, scaler


def load_oos(cfg: Config) -> pd.DataFrame:
    return pd.read_csv(cfg.artifacts_dir / OOS_FILE, parse_dates=["date"])


def artifacts_exist(cfg: Config) -> bool:
    d = cfg.artifacts_dir
    return all((d / f).exists() for f in (MODEL_FILE, SCALER_FILE, META_FILE))
