#!/usr/bin/env python3
"""CLI for the SPX 0DTE self-training agent.

Examples:
  python3 scripts/run_agent.py sample          # write demo CSVs
  python3 scripts/run_agent.py train           # walk-forward train
  python3 scripts/run_agent.py signal          # score latest → signal.json
  python3 scripts/run_agent.py retrain         # retrain if new data, then signal
  python3 scripts/run_agent.py retrain --force
  python3 scripts/run_agent.py orderflow-status
  python3 scripts/run_agent.py ingest-orderflow path/to/dump.csv
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow `python3 scripts/run_agent.py` without installing the package
sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent.config import load_config
from agent.orderflow import ingest_orderflow_file, orderflow_status
from agent.retrain import retrain_if_needed
from agent.signal_writer import write_signal
from agent.train import train_model


def cmd_sample(args: argparse.Namespace) -> None:
    from generate_sample_data import generate

    generate(months=args.months, seed=args.seed)


def cmd_train(_: argparse.Namespace) -> None:
    cfg = load_config()
    meta = train_model(cfg)
    print(json.dumps({k: meta[k] for k in ("rows", "walk_forward_accuracy", "data_end")}, indent=2))


def cmd_signal(_: argparse.Namespace) -> None:
    cfg = load_config()
    payload = write_signal(cfg)
    print(json.dumps({"signal": payload["signal"], "confidence": payload["confidence"]}, indent=2))


def cmd_retrain(args: argparse.Namespace) -> None:
    cfg = load_config()
    result = retrain_if_needed(cfg, force=args.force, write_sig=not args.skip_signal)
    print(json.dumps(result, indent=2))


def cmd_orderflow_status(_: argparse.Namespace) -> None:
    cfg = load_config()
    print(json.dumps(orderflow_status(cfg), indent=2))


def cmd_ingest(args: argparse.Namespace) -> None:
    cfg = load_config()
    dest = ingest_orderflow_file(args.path, cfg)
    print(json.dumps({"ingested": str(dest)}, indent=2))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_sample = sub.add_parser("sample", help="Generate synthetic 24m demo data")
    p_sample.add_argument("--months", type=int, default=24)
    p_sample.add_argument("--seed", type=int, default=42)
    p_sample.set_defaults(func=cmd_sample)

    p_train = sub.add_parser("train", help="Walk-forward train on data/raw")
    p_train.set_defaults(func=cmd_train)

    p_sig = sub.add_parser("signal", help="Infer and write signal.json")
    p_sig.set_defaults(func=cmd_signal)

    p_rt = sub.add_parser("retrain", help="Retrain if new data, then write signal")
    p_rt.add_argument("--force", action="store_true")
    p_rt.add_argument("--skip-signal", action="store_true")
    p_rt.set_defaults(func=cmd_retrain)

    p_ofs = sub.add_parser("orderflow-status", help="Show order-flow drop folder status")
    p_ofs.set_defaults(func=cmd_orderflow_status)

    p_ing = sub.add_parser("ingest-orderflow", help="Validate + copy an order-flow dump")
    p_ing.add_argument("path", type=Path)
    p_ing.set_defaults(func=cmd_ingest)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
