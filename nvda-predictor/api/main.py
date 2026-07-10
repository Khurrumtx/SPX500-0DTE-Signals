"""FastAPI service exposing the trained NVDA direction model.

Run from the nvda-predictor directory:
    python cli.py serve            # or: uvicorn api.main:app --port 8080
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, HTTPException, Query

from nvda_predictor.config import Config
from nvda_predictor.persist import artifacts_exist, load_meta, load_oos

app = FastAPI(
    title="NVDA Direction Predictor",
    description=(
        "Next-day up/down probability for NVDA from a stacked-LSTM classifier "
        "(replicated from the top-starred GitHub stock-prediction repos). "
        "Educational only — not financial advice."
    ),
    version="0.1.0",
)

cfg = Config()


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "model_ready": artifacts_exist(cfg)}


@app.get("/model")
def model_info() -> dict:
    if not artifacts_exist(cfg):
        raise HTTPException(503, "Model not trained yet — run `python cli.py evaluate`")
    return load_meta(cfg)


@app.get("/prediction")
def prediction(refresh: bool = Query(True, description="pull latest prices first")) -> dict:
    if not artifacts_exist(cfg):
        raise HTTPException(503, "Model not trained yet — run `python cli.py evaluate`")
    from nvda_predictor.predict import predict_latest

    return predict_latest(cfg, refresh=refresh)


@app.get("/history")
def history(limit: int = Query(250, ge=1, le=5000)) -> list[dict]:
    """Most recent out-of-sample predictions from the walk-forward evaluation."""
    try:
        oos = load_oos(cfg)
    except FileNotFoundError:
        raise HTTPException(503, "No evaluation history — run `python cli.py evaluate`")
    tail = oos.tail(limit).copy()
    tail["date"] = tail["date"].dt.strftime("%Y-%m-%d")
    return tail.to_dict(orient="records")
