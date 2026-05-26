#!/usr/bin/env python3
"""Rule-based SPX 0DTE options-trade signal (defined-risk, directional).

EDUCATIONAL, NOT FINANCIAL ADVICE. 0DTE options are extremely high risk and can
lose 100% in hours. This generates a transparent, rule-based suggestion only.

Style (per user): directional, limited risk only -> single long options
(aggressive) and debit spreads (balanced). No credit spreads or condors.

How it works (one Alpha Vantage call/run):
  1. Pull recent daily candles for a liquid proxy (default SPY) via
     TIME_SERIES_DAILY.
  2. Derive SPX level (SPY x10), SMA(5/20), RSI(14), realized volatility.
  3. Score a directional bias + conviction; pick LONG option vs DEBIT spread
     based on conviction and the volatility regime.
  4. Price legs with Black-Scholes (sigma = realized vol, T = time to today's
     close) to show estimated debit, capped max risk/reward, and breakeven,
     sized to MAX_RISK.
  5. Write signals/signal.json (back-compat fields + a `trade` object).

Run:  ALPHAVANTAGE_API_KEY=xxx python3 scripts/signal.py
"""
from __future__ import annotations

import json
import math
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "signals" / "signal.json"
OUT_DASH = ROOT / "dashboard" / "signals" / "signal.json"

API_KEY = os.environ.get("ALPHAVANTAGE_API_KEY", "").strip()
SYMBOL = os.environ.get("SIGNAL_SYMBOL", "SPY")
SPX_MULT = float(os.environ.get("SPX_MULT", "10"))     # SPX ~= SPY x10
MAX_RISK = float(os.environ.get("MAX_RISK", "1000"))
CONTRACT_MULT = 100                                     # SPX/SPY option multiplier
BASE = "https://www.alphavantage.co/query"


# ---------------- data ----------------

def av_get(function: str, **params) -> dict:
    params.update({"function": function, "apikey": API_KEY})
    url = BASE + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "SPX0DTE-Signals"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def fetch_closes(symbol: str) -> list[float]:
    data = av_get("TIME_SERIES_DAILY", symbol=symbol, outputsize="compact")
    series = data.get("Time Series (Daily)")
    if not series:
        raise RuntimeError(data.get("Information") or data.get("Note") or "no daily series")
    # newest first
    closes = [float(v["4. close"]) for _, v in sorted(series.items(), reverse=True)]
    return closes


# ---------------- indicators ----------------

def sma(values: list[float], n: int) -> float:
    return sum(values[:n]) / n if len(values) >= n else float("nan")


def rsi(closes: list[float], n: int = 14) -> float:
    # closes newest-first
    if len(closes) < n + 1:
        return 50.0
    gains = losses = 0.0
    for i in range(n):
        diff = closes[i] - closes[i + 1]
        if diff >= 0:
            gains += diff
        else:
            losses -= diff
    if losses == 0:
        return 100.0
    rs = (gains / n) / (losses / n)
    return 100 - 100 / (1 + rs)


def realized_vol(closes: list[float], n: int = 20) -> float:
    if len(closes) < n + 1:
        return 0.16
    rets = [math.log(closes[i] / closes[i + 1]) for i in range(n)]
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(252)


# ---------------- Black-Scholes ----------------

def _ncdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs_price(S: float, K: float, T: float, sigma: float, right: str, r: float = 0.0) -> float:
    if T <= 0 or sigma <= 0:
        return max(0.0, S - K) if right == "CALL" else max(0.0, K - S)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    if right == "CALL":
        return S * _ncdf(d1) - K * math.exp(-r * T) * _ncdf(d2)
    return K * math.exp(-r * T) * _ncdf(-d2) - S * _ncdf(-d1)


def hours_to_close_T() -> float:
    """Year-fraction from now to today's 20:00 UTC (16:00 ET) close, floored small."""
    now = datetime.now(timezone.utc)
    close = now.replace(hour=20, minute=0, second=0, microsecond=0)
    secs = (close - now).total_seconds()
    if secs < 600:           # near/after close: treat as next-session open
        secs = 6.5 * 3600
    return secs / (365 * 24 * 3600)


# ---------------- signal + trade ----------------

def compute_signal(closes: list[float]):
    last = closes[0]
    s5, s20 = sma(closes, 5), sma(closes, 20)
    r = rsi(closes, 14)
    rv = realized_vol(closes, 20)

    score = 0
    if not math.isnan(s20):
        score += 1 if last > s20 else -1
    if not math.isnan(s5) and not math.isnan(s20):
        score += 1 if s5 > s20 else -1
    if r > 55:
        score += 1
    elif r < 45:
        score -= 1

    direction = "BULLISH" if score > 0 else "BEARISH" if score < 0 else "NEUTRAL"
    conviction = int(min(100, 22 * abs(score) + 0.6 * abs(r - 50)))
    regime = "low" if rv < 0.12 else "high" if rv > 0.22 else "normal"
    return {
        "direction": direction, "conviction": conviction, "regime": rv_regime(rv),
        "rv": round(rv, 4), "rsi": round(r, 1),
        "sma5": round(s5, 2), "sma20": round(s20, 2),
    }


def rv_regime(rv: float) -> str:
    return "low" if rv < 0.12 else "high" if rv > 0.22 else "normal"


def round5(x: float) -> int:
    return int(round(x / 5.0) * 5)


def build_trade(spx: float, sig: dict, T: float) -> dict:
    direction, conviction, rv = sig["direction"], sig["conviction"], sig["rv"]
    sigma = max(0.05, rv)
    expiry = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if direction == "NEUTRAL" or conviction < 35:
        return {"type": "none", "strategy": "No trade",
                "rationale": "No clear directional edge (low conviction); standing aside.",
                "expiry": f"0DTE ({expiry})"}

    right = "CALL" if direction == "BULLISH" else "PUT"
    atm = round5(spx)
    atm_price = bs_price(spx, atm, T, sigma, right)

    use_long = sig["regime"] == "low" and conviction >= 65 and atm_price * CONTRACT_MULT <= MAX_RISK

    if use_long:  # aggressive single long option
        contracts = max(1, int(MAX_RISK // (atm_price * CONTRACT_MULT)))
        debit = atm_price * CONTRACT_MULT * contracts
        be = atm + atm_price if right == "CALL" else atm - atm_price
        return {
            "type": "long_option",
            "strategy": f"Long {right.title()} (0DTE)",
            "expiry": f"0DTE ({expiry})",
            "legs": [{"action": "BUY", "right": right, "strike": atm, "est_price": round(atm_price, 2)}],
            "contracts": contracts,
            "est_debit": int(debit), "max_risk": int(debit), "max_reward": "open-ended",
            "breakeven": round(be, 2),
            "rationale": f"{direction.title()} bias (RSI {sig['rsi']}, conviction {conviction}); "
                         f"low realized vol ({rv:.0%}) makes buying premium cheaper.",
        }

    # balanced debit spread: long ATM, short one width OTM in the trade direction
    best = None
    for width in (25, 20, 15, 10):
        short_k = atm + width if right == "CALL" else atm - width
        short_price = bs_price(spx, short_k, T, sigma, right)
        debit_per = (atm_price - short_price) * CONTRACT_MULT
        if debit_per <= 0:
            continue
        if debit_per <= MAX_RISK:
            best = (width, short_k, short_price, debit_per)
            break
    if best is None:
        short_k = atm + 10 if right == "CALL" else atm - 10
        best = (10, short_k, 0.0, atm_price * CONTRACT_MULT)

    width, short_k, short_price, debit_per = best
    contracts = max(1, int(MAX_RISK // debit_per))
    debit_pts = debit_per / CONTRACT_MULT
    be = atm + debit_pts if right == "CALL" else atm - debit_pts
    max_reward = (width * CONTRACT_MULT - debit_per) * contracts
    name = "Bull Call" if right == "CALL" else "Bear Put"
    return {
        "type": "debit_spread",
        "strategy": f"{name} Debit Spread (0DTE)",
        "expiry": f"0DTE ({expiry})",
        "legs": [
            {"action": "BUY", "right": right, "strike": atm, "est_price": round(atm_price, 2)},
            {"action": "SELL", "right": right, "strike": short_k, "est_price": round(short_price, 2)},
        ],
        "contracts": contracts, "width": width,
        "est_debit": int(debit_per * contracts),
        "max_risk": int(debit_per * contracts),
        "max_reward": int(max_reward),
        "breakeven": round(be, 2),
        "rationale": f"{direction.title()} bias (RSI {sig['rsi']}, conviction {conviction}); "
                     f"{sig['regime']} vol favors a defined-risk debit spread over a naked long.",
    }


def main() -> int:
    if not API_KEY:
        print("ERROR: set ALPHAVANTAGE_API_KEY", file=sys.stderr)
        return 1
    try:
        closes = fetch_closes(SYMBOL)
    except Exception as e:
        print(f"data fetch failed: {e}", file=sys.stderr)
        return 1

    spx = closes[0] * SPX_MULT
    sig = compute_signal([c * SPX_MULT for c in closes])
    trade = build_trade(spx, sig, hours_to_close_T())

    direction = sig["direction"]
    payload = {
        "signal": "CALL" if direction == "BULLISH" else "PUT" if direction == "BEARISH" else "NONE",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "confidence": sig["conviction"],
        "direction": direction,
        "spx": round(spx, 2),
        "rsi": sig["rsi"], "realized_vol": sig["rv"], "vol_regime": sig["regime"],
        "trade": trade,
        "disclaimer": "Educational rule-based signal. 0DTE options are extremely high risk and can expire worthless. Not financial advice. Prices are Black-Scholes estimates; actual fills vary.",
    }
    for path in (OUT, OUT_DASH):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"{direction} conviction={sig['conviction']} -> {trade['strategy']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
