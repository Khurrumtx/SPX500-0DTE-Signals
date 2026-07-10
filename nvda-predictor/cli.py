#!/usr/bin/env python3
"""NVDA direction predictor — command line interface.

Commands:
  fetch      Download/refresh NVDA daily history into data/NVDA.csv
  evaluate   Walk-forward evaluation of LSTM + baselines (writes artifacts/)
  train      Train the final LSTM on all history (writes artifacts/)
  predict    Print the latest next-day direction prediction as JSON
  backtest   Re-run the backtest report from saved out-of-sample predictions
  serve      Start the FastAPI service (uvicorn)
  dashboard  Start the Streamlit dashboard
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from nvda_predictor.config import Config
from nvda_predictor.persist import META_FILE, load_meta, load_oos, save_artifacts

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


def cmd_fetch(cfg: Config, args) -> None:
    from nvda_predictor.data import load_prices

    df = load_prices(cfg, refresh=True)
    print(f"{cfg.ticker}: {len(df)} rows {df.index[0].date()} .. {df.index[-1].date()}")
    print(f"cached at {cfg.cache_path}")


def cmd_evaluate(cfg: Config, args) -> None:
    from nvda_predictor.train import run_walk_forward, train_final

    res = run_walk_forward(cfg, refresh=args.refresh, verbose=args.verbose)
    print(json.dumps(res["models"], indent=2))

    model, scaler, meta = train_final(cfg, refresh=False, verbose=args.verbose)
    meta["walk_forward"] = res["models"]
    meta["evaluated"] = {
        "data_start": res["data_start"],
        "data_end": res["data_end"],
        "n_samples": res["n_samples"],
    }
    save_artifacts(cfg, model, scaler, meta, oos=res["oos"])
    print(f"artifacts written to {cfg.artifacts_dir}")


def cmd_train(cfg: Config, args) -> None:
    from nvda_predictor.train import train_final

    model, scaler, meta = train_final(cfg, refresh=args.refresh, verbose=args.verbose)
    # keep any previous walk-forward metrics in meta
    try:
        old = load_meta(cfg)
        meta["walk_forward"] = old.get("walk_forward")
    except FileNotFoundError:
        pass
    save_artifacts(cfg, model, scaler, meta)
    print(f"trained through {meta['train_end']}; artifacts in {cfg.artifacts_dir}")


def cmd_predict(cfg: Config, args) -> None:
    from nvda_predictor.predict import predict_latest

    print(json.dumps(predict_latest(cfg, refresh=args.refresh), indent=2))


def cmd_backtest(cfg: Config, args) -> None:
    import pandas as pd

    from nvda_predictor.backtest import backtest, classification_metrics

    oos = load_oos(cfg)
    report = {}
    for col in [c for c in oos.columns if c.startswith("proba_")]:
        name = col.removeprefix("proba_")
        proba = oos[col].to_numpy()
        bt = backtest(
            pd.DatetimeIndex(oos["date"]), proba, oos["next_ret"].to_numpy(),
            threshold=args.threshold, cost_bps=cfg.cost_bps,
        )
        bt.pop("equity_curve")
        report[name] = {
            "classification": classification_metrics(oos["y"].to_numpy(), proba, args.threshold),
            "backtest": bt,
        }
    print(json.dumps(report, indent=2))


def cmd_serve(cfg: Config, args) -> None:
    import uvicorn

    uvicorn.run("api.main:app", host=args.host, port=args.port, reload=False)


def cmd_dashboard(cfg: Config, args) -> None:
    import subprocess

    app = Path(__file__).resolve().parent / "app" / "dashboard.py"
    subprocess.run([sys.executable, "-m", "streamlit", "run", str(app)], check=True)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ticker", default="NVDA")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("fetch")
    for name in ("evaluate", "train"):
        s = sub.add_parser(name)
        s.add_argument("--refresh", action="store_true", help="re-download data first")
        s.add_argument("--verbose", type=int, default=0)
    s = sub.add_parser("predict")
    s.add_argument("--refresh", action="store_true", help="re-download data first")
    s = sub.add_parser("backtest")
    s.add_argument("--threshold", type=float, default=0.5)
    s = sub.add_parser("serve")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8080)
    sub.add_parser("dashboard")

    args = p.parse_args()
    cfg = Config(ticker=args.ticker)
    {
        "fetch": cmd_fetch,
        "evaluate": cmd_evaluate,
        "train": cmd_train,
        "predict": cmd_predict,
        "backtest": cmd_backtest,
        "serve": cmd_serve,
        "dashboard": cmd_dashboard,
    }[args.cmd](cfg, args)


if __name__ == "__main__":
    main()
