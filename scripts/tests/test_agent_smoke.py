#!/usr/bin/env python3
"""Smoke test: sample → train → signal."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from generate_sample_data import generate
from agent.config import load_config
from agent.train import train_model
from agent.signal_writer import write_signal
from agent.retrain import retrain_if_needed
from agent.orderflow import ingest_orderflow_file, orderflow_status


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        raw = root / "raw"
        of_dir = root / "orderflow"
        processed = root / "processed"
        models = root / "models"
        sig = root / "signal.json"
        dash = root / "dash_signal.json"
        for d in (raw, of_dir, processed, models):
            d.mkdir()

        generate(months=24, seed=7, out_dir=raw)

        cfg = load_config()
        cfg["paths"]["raw_dir"] = str(raw)
        cfg["paths"]["orderflow_dir"] = str(of_dir)
        cfg["paths"]["processed_dir"] = str(processed)
        cfg["paths"]["models_dir"] = str(models)
        cfg["paths"]["signal_out"] = str(sig)
        cfg["paths"]["dashboard_signal_out"] = str(dash)
        cfg["train"]["min_train_rows"] = 80

        meta = train_model(cfg)
        assert meta["rows"] > 100, meta
        assert meta["walk_forward_accuracy"] is not None

        payload = write_signal(cfg)
        assert payload["signal"] in {"CALL", "PUT", "WAIT", "NONE"}
        assert 0 <= payload["confidence"] <= 100
        assert Path(sig).exists()

        # Tiny order-flow dump → retrain trigger
        of = raw / "spx.csv"
        import pandas as pd

        base = pd.read_csv(of).tail(30)
        base = base.rename(columns={"volume": "delta"})
        base["cumulative_delta"] = base["delta"].cumsum()
        base["buy_aggression"] = 1.0
        base["sell_aggression"] = 0.5
        dump = root / "of_dump.csv"
        base.to_csv(dump, index=False)
        ingest_orderflow_file(dump, cfg)
        assert orderflow_status(cfg)["file_count"] >= 1

        result = retrain_if_needed(cfg, force=False)
        assert result["retrained"] is True
        print("OK", json.dumps({"acc": meta["walk_forward_accuracy"], "signal": payload["signal"]}))


if __name__ == "__main__":
    main()
