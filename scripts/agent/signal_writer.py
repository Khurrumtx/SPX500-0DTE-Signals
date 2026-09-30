"""Write dashboard-compatible signal.json from model inference."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .infer import predict_latest


DISCLAIMER = (
    "Educational ML signal trained on ES/SPX/VIX/CL. "
    "0DTE options are extremely high risk and can expire worthless. "
    "Not financial advice. Prices are structure estimates; actual fills vary."
)


def _suggest_trade(pred: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    signal = pred["signal"]
    spx = float(pred["spx"])
    conf = int(pred["confidence"])
    max_risk = float(cfg["train"].get("max_risk_usd", 1000))
    # Round strikes to nearest 5
    atm = int(round(spx / 5) * 5)
    width = 25

    if signal == "CALL":
        if conf >= 70:
            # Long call estimate
            est = max(3.0, spx * 0.0015)
            contracts = max(1, int(max_risk / (est * 100)))
            return {
                "type": "long_option",
                "strategy": "Long Call (0DTE)",
                "expiry": "0DTE",
                "legs": [{"action": "BUY", "right": "CALL", "strike": atm, "est_price": round(est, 2)}],
                "contracts": contracts,
                "est_debit": round(est * 100 * contracts),
                "max_risk": round(est * 100 * contracts),
                "max_reward": "open",
                "breakeven": round(atm + est, 2),
                "rationale": (
                    f"ML bullish bias (confidence {conf}); "
                    f"p_call={pred['probabilities']['CALL']:.2f}."
                ),
                "pricing": "estimate",
                "instrument": "SPX",
            }
        long_est = max(4.0, spx * 0.0018)
        short_est = max(0.5, long_est * 0.2)
        debit = (long_est - short_est) * 100
        contracts = max(1, int(max_risk / debit))
        return {
            "type": "debit_spread",
            "strategy": "Bull Call Debit Spread (0DTE)",
            "expiry": "0DTE",
            "legs": [
                {"action": "BUY", "right": "CALL", "strike": atm, "est_price": round(long_est, 2)},
                {
                    "action": "SELL",
                    "right": "CALL",
                    "strike": atm + width,
                    "est_price": round(short_est, 2),
                },
            ],
            "contracts": contracts,
            "width": width,
            "est_debit": round(debit * contracts),
            "max_risk": round(debit * contracts),
            "max_reward": round((width * 100 - debit) * contracts),
            "breakeven": round(atm + (long_est - short_est), 2),
            "rationale": (
                f"ML bullish bias (confidence {conf}); defined-risk debit spread."
            ),
            "pricing": "estimate",
            "instrument": "SPX",
        }

    if signal == "PUT":
        if conf >= 70:
            est = max(3.0, spx * 0.0015)
            contracts = max(1, int(max_risk / (est * 100)))
            return {
                "type": "long_option",
                "strategy": "Long Put (0DTE)",
                "expiry": "0DTE",
                "legs": [{"action": "BUY", "right": "PUT", "strike": atm, "est_price": round(est, 2)}],
                "contracts": contracts,
                "est_debit": round(est * 100 * contracts),
                "max_risk": round(est * 100 * contracts),
                "max_reward": "open",
                "breakeven": round(atm - est, 2),
                "rationale": (
                    f"ML bearish bias (confidence {conf}); "
                    f"p_put={pred['probabilities']['PUT']:.2f}."
                ),
                "pricing": "estimate",
                "instrument": "SPX",
            }
        long_est = max(4.0, spx * 0.0018)
        short_est = max(0.5, long_est * 0.2)
        debit = (long_est - short_est) * 100
        contracts = max(1, int(max_risk / debit))
        return {
            "type": "debit_spread",
            "strategy": "Bear Put Debit Spread (0DTE)",
            "expiry": "0DTE",
            "legs": [
                {"action": "BUY", "right": "PUT", "strike": atm, "est_price": round(long_est, 2)},
                {
                    "action": "SELL",
                    "right": "PUT",
                    "strike": atm - width,
                    "est_price": round(short_est, 2),
                },
            ],
            "contracts": contracts,
            "width": width,
            "est_debit": round(debit * contracts),
            "max_risk": round(debit * contracts),
            "max_reward": round((width * 100 - debit) * contracts),
            "breakeven": round(atm - (long_est - short_est), 2),
            "rationale": (
                f"ML bearish bias (confidence {conf}); defined-risk debit spread."
            ),
            "pricing": "estimate",
            "instrument": "SPX",
            }

    return {
        "type": "none",
        "rationale": (
            f"Standing aside — model confidence {conf}% "
            f"(floor {cfg['train'].get('confidence_floor', 45)}%) or WAIT class."
        ),
    }


def build_signal_payload(pred: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    trade = _suggest_trade(pred, cfg)
    rvol = pred.get("realized_vol")
    vol_regime = "normal"
    if rvol is not None:
        if rvol >= 0.25:
            vol_regime = "high"
        elif rvol <= 0.10:
            vol_regime = "low"

    out_signal = pred["signal"]
    if out_signal == "WAIT":
        dash_signal = "WAIT"
    else:
        dash_signal = out_signal

    return {
        "signal": dash_signal,
        "timestamp": pred.get("timestamp") or datetime.now(timezone.utc).isoformat(),
        "confidence": pred["confidence"],
        "direction": pred["direction"],
        "spx": pred["spx"],
        "pricing": "estimate",
        "instrument": "SPX",
        "rsi": pred.get("rsi"),
        "realized_vol": rvol,
        "vol_regime": vol_regime,
        "model": {
            "name": "spx_0dte_hgbt",
            "probabilities": pred.get("probabilities"),
            "features_used": pred.get("features_used"),
        },
        "trade": trade,
        "disclaimer": DISCLAIMER,
    }


def write_signal(cfg: dict[str, Any], pred: dict[str, Any] | None = None) -> dict[str, Any]:
    if pred is None:
        pred = predict_latest(cfg)
    payload = build_signal_payload(pred, cfg)
    for key in ("signal_out", "dashboard_signal_out"):
        path = Path(cfg["paths"][key])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2) + "\n")
        print(f"Wrote {path}")
    return payload
