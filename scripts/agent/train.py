"""Walk-forward train + persist the SPX 0DTE direction model."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import classification_report, accuracy_score
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer

from .features import build_features, feature_columns
from .labels import INV_LABEL_MAP, LABEL_MAP, add_labels
from .load_data import load_universe


MODEL_NAME = "spx_0dte_hgbt.joblib"
META_NAME = "spx_0dte_meta.json"


def _time_folds(n: int, n_folds: int) -> list[tuple[np.ndarray, np.ndarray]]:
    """Expanding-window walk-forward folds."""
    folds = []
    # Keep at least ~40% for first train
    min_train = max(int(n * 0.4), 80)
    test_size = max((n - min_train) // n_folds, 20)
    start_test = min_train
    for _ in range(n_folds):
        end_test = min(start_test + test_size, n)
        if end_test <= start_test:
            break
        train_idx = np.arange(0, start_test)
        test_idx = np.arange(start_test, end_test)
        folds.append((train_idx, test_idx))
        start_test = end_test
        if start_test >= n:
            break
    return folds


def prepare_dataset(cfg: dict[str, Any]) -> tuple[pd.DataFrame, list[str]]:
    raw = load_universe(cfg)
    # Restrict to lookback window
    months = int(cfg["train"].get("lookback_months", 24))
    cutoff = raw["timestamp"].max() - pd.DateOffset(months=months)
    raw = raw[raw["timestamp"] >= cutoff].reset_index(drop=True)

    feat = build_features(raw, cfg)
    labeled = add_labels(feat, cfg)
    cols = feature_columns(labeled)
    return labeled, cols


def train_model(cfg: dict[str, Any], df: pd.DataFrame | None = None) -> dict[str, Any]:
    if df is None:
        df, feat_cols = prepare_dataset(cfg)
    else:
        feat_cols = feature_columns(df)

    usable = df[df["label"] >= 0].copy()
    # Drop sparse columns (>90% NaN) so partial order-flow history doesn't poison folds
    sparse = [c for c in feat_cols if usable[c].isna().mean() > 0.9]
    feat_cols = [c for c in feat_cols if c not in sparse]
    if sparse:
        print(f"Dropping sparse features: {sparse}")

    min_rows = int(cfg["train"].get("min_train_rows", 120))
    if len(usable) < min_rows:
        raise RuntimeError(
            f"Need at least {min_rows} labeled rows; have {len(usable)}. "
            "Add more history under data/raw/."
        )

    X = usable[feat_cols]
    y = usable["label"].to_numpy()
    n_folds = int(cfg["train"].get("walk_forward_folds", 6))
    folds = _time_folds(len(usable), n_folds)

    fold_reports = []
    oof = np.full(len(usable), -1, dtype=int)
    for i, (tr, te) in enumerate(folds):
        pipe = Pipeline(
            steps=[
                ("imputer", SimpleImputer(strategy="median")),
                (
                    "clf",
                    HistGradientBoostingClassifier(
                        max_depth=4,
                        learning_rate=0.08,
                        max_iter=200,
                        random_state=int(cfg["train"].get("random_state", 42)),
                        class_weight="balanced",
                    ),
                ),
            ]
        )
        pipe.fit(X.iloc[tr], y[tr])
        pred = pipe.predict(X.iloc[te])
        oof[te] = pred
        acc = float(accuracy_score(y[te], pred))
        fold_reports.append(
            {
                "fold": i,
                "train_rows": int(len(tr)),
                "test_rows": int(len(te)),
                "accuracy": acc,
                "report": classification_report(
                    y[te],
                    pred,
                    labels=[0, 1, 2],
                    target_names=["WAIT", "CALL", "PUT"],
                    zero_division=0,
                    output_dict=True,
                ),
            }
        )

    # Final model on all usable history
    final = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="median")),
            (
                "clf",
                HistGradientBoostingClassifier(
                    max_depth=4,
                    learning_rate=0.08,
                    max_iter=250,
                    random_state=int(cfg["train"].get("random_state", 42)),
                    class_weight="balanced",
                ),
            ),
        ]
    )
    final.fit(X, y)

    models_dir = Path(cfg["paths"]["models_dir"])
    models_dir.mkdir(parents=True, exist_ok=True)
    model_path = models_dir / MODEL_NAME
    joblib.dump({"model": final, "features": feat_cols}, model_path)

    oof_mask = oof >= 0
    oof_acc = float(accuracy_score(y[oof_mask], oof[oof_mask])) if oof_mask.any() else None

    meta = {
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "rows": int(len(usable)),
        "features": feat_cols,
        "label_map": LABEL_MAP,
        "walk_forward_accuracy": oof_acc,
        "folds": [
            {"fold": f["fold"], "accuracy": f["accuracy"], "test_rows": f["test_rows"]}
            for f in fold_reports
        ],
        "lookback_months": cfg["train"].get("lookback_months", 24),
        "data_end": usable["timestamp"].max().isoformat(),
        "data_start": usable["timestamp"].min().isoformat(),
        "model_path": str(model_path),
        "disclaimer": "Educational model. 0DTE options are extremely high risk. Not financial advice.",
    }
    meta_path = models_dir / META_NAME
    meta_path.write_text(json.dumps(meta, indent=2))

    # Persist processed feature matrix for inspection / incremental retrain
    processed = Path(cfg["paths"]["processed_dir"])
    processed.mkdir(parents=True, exist_ok=True)
    usable.to_csv(processed / "training_frame.csv", index=False)

    print(f"Saved model → {model_path}")
    print(f"Walk-forward accuracy: {oof_acc}")
    return meta
