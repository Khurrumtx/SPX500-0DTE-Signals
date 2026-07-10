"""Stacked-LSTM direction classifier.

Architecture replicated from jaungiers/LSTM-Neural-Network-for-Time-Series-
Prediction (config.json): LSTM(100, return_sequences) -> Dropout(0.2) ->
LSTM(100, return_sequences) -> LSTM(100) -> Dropout(0.2) -> Dense(1).
Differences from the source (deliberate, documented in the README):

- Dense head uses sigmoid + binary cross-entropy (direction classification)
  instead of linear + MSE price regression.
- Adam learning rate 1e-3 (huseinzol05 uses Adam as well) with early
  stopping on a validation tail, instead of a fixed epoch count.
- Inputs are 15 technical-indicator features standardized on the training
  fold, instead of min-max-scaled raw close/volume.
"""

from __future__ import annotations

import os
import random

import numpy as np

from ..config import Config


def _set_seeds(seed: int) -> None:
    os.environ["PYTHONHASHSEED"] = str(seed)
    random.seed(seed)
    np.random.seed(seed)
    import tensorflow as tf

    tf.random.set_seed(seed)


def build_model(window: int, n_features: int, cfg: Config):
    from tensorflow import keras
    from tensorflow.keras import layers

    model = keras.Sequential(
        [
            keras.Input(shape=(window, n_features)),
            layers.LSTM(cfg.lstm_units, return_sequences=True),
            layers.Dropout(cfg.dropout),
            layers.LSTM(cfg.lstm_units, return_sequences=True),
            layers.LSTM(cfg.lstm_units),
            layers.Dropout(cfg.dropout),
            layers.Dense(1, activation="sigmoid"),
        ]
    )
    model.compile(
        optimizer=keras.optimizers.Adam(learning_rate=cfg.learning_rate),
        loss="binary_crossentropy",
        metrics=["accuracy"],
    )
    return model


def train_lstm(X_train: np.ndarray, y_train: np.ndarray, cfg: Config, verbose: int = 0):
    """Train with a chronological validation tail and early stopping."""
    from tensorflow import keras

    _set_seeds(cfg.seed)
    n_val = max(int(len(X_train) * cfg.validation_fraction), 50)
    X_tr, y_tr = X_train[:-n_val], y_train[:-n_val]
    X_val, y_val = X_train[-n_val:], y_train[-n_val:]

    model = build_model(X_train.shape[1], X_train.shape[2], cfg)
    early = keras.callbacks.EarlyStopping(
        monitor="val_loss",
        patience=cfg.early_stopping_patience,
        restore_best_weights=True,
    )
    model.fit(
        X_tr,
        y_tr,
        validation_data=(X_val, y_val),
        epochs=cfg.max_epochs,
        batch_size=cfg.batch_size,
        callbacks=[early],
        shuffle=True,
        verbose=verbose,
    )
    return model


def predict_proba(model, X: np.ndarray) -> np.ndarray:
    return model.predict(X, verbose=0).reshape(-1)
