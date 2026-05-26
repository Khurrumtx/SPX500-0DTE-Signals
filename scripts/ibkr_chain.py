#!/usr/bin/env python3
"""Pull a LIVE 0DTE option chain from Interactive Brokers -> Excel (+ optional signal).

IMPORTANT: Runs on YOUR OWN COMPUTER, not the cloud app/GitHub. IBKR's API needs
a logged-in TWS or IB Gateway session, so it can't run in CI.

Prerequisites
-------------
1. An Interactive Brokers account.
2. TWS or IB Gateway running and logged in.
3. Enable the API:  TWS/Gateway -> Settings -> API -> Settings -> check
   "Enable ActiveX and Socket Clients" (add 127.0.0.1 to Trusted IPs).
4. pip install -r scripts/requirements-ibkr.txt
   Ports: TWS live 7496 / paper 7497;  Gateway live 4001 / paper 4002.

Examples
--------
    python3 scripts/ibkr_chain.py --symbol SPX --port 7496 --delayed
    python3 scripts/ibkr_chain.py --symbol SPX --signal            # also write signal.json
    python3 scripts/ibkr_chain.py --symbol SPY --strikes 20 --out my.xlsx

Output
------
- Appends a timestamped sheet (e.g. "20260526_1532") to one workbook per symbol
  (default <symbol>_chains.xlsx) and keeps a "runs" log sheet, so the workbook
  builds a history across runs.
- With --signal: runs the rule-based 0DTE signal on the LIVE IBKR chain and
  writes signals/signal.json (+ dashboard copy) so the app can show broker prices.
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX_SYMBOLS = {"SPX", "SPXW", "NDX", "RUT", "VIX", "DJX", "XSP"}


def parse_args():
    p = argparse.ArgumentParser(description="Save a live IBKR 0DTE option chain to Excel.")
    p.add_argument("--symbol", default="SPX", help="Underlying (SPX, SPY, QQQ, ...). Default SPX.")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=7497, help="TWS 7497(paper)/7496(live); Gateway 4002/4001.")
    p.add_argument("--client-id", type=int, default=17)
    p.add_argument("--expiry", default="", help="YYYYMMDD. Default = today (0DTE) or nearest.")
    p.add_argument("--strikes", type=int, default=30, help="Strikes each side of spot. Default 30.")
    p.add_argument("--delayed", action="store_true", help="Use delayed data (no real-time sub needed).")
    p.add_argument("--out", default="", help="Workbook path. Default <symbol>_chains.xlsx")
    p.add_argument("--signal", action="store_true", help="Also run the 0DTE signal on the live chain.")
    p.add_argument("--max-risk", type=float, default=1000.0, help="Max $ risk for the signal. Default 1000.")
    return p.parse_args()


def _clean(v):
    try:
        if v is None or v != v:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _mid(t):
    b, a = _clean(t.bid), _clean(t.ask)
    if b and a:
        return round((b + a) / 2, 2)
    return _clean(t.last)


def append_workbook(out: str, chain_df, meta_row: dict, sheet_name: str):
    """Append a timestamped chain sheet + a row to the 'runs' log; create if new."""
    import pandas as pd
    exists = os.path.exists(out)
    runs_df = None
    if exists:
        try:
            runs_df = pd.read_excel(out, sheet_name="runs")
        except Exception:
            runs_df = None
    new_runs = pd.DataFrame([meta_row]) if runs_df is None else \
        pd.concat([runs_df, pd.DataFrame([meta_row])], ignore_index=True)
    kwargs = {"engine": "openpyxl", "mode": "a" if exists else "w"}
    if exists:
        kwargs["if_sheet_exists"] = "replace"
    with pd.ExcelWriter(out, **kwargs) as xl:
        chain_df.to_excel(xl, index=False, sheet_name=sheet_name[:31])
        new_runs.to_excel(xl, index=False, sheet_name="runs")


def load_signal_engine():
    """Load scripts/signal.py under a non-clashing name (avoid stdlib `signal`)."""
    spec = importlib.util.spec_from_file_location("sig_engine", Path(__file__).resolve().parent / "signal.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    args = parse_args()
    try:
        from ib_insync import IB, Index, Stock, Option
    except ImportError:
        print("Missing dependency. Run:  pip install -r scripts/requirements-ibkr.txt", file=sys.stderr)
        return 1
    try:
        import pandas as pd
    except ImportError:
        print("Missing dependency. Run:  pip install pandas openpyxl", file=sys.stderr)
        return 1

    sym = args.symbol.upper()
    ib = IB()
    try:
        ib.connect(args.host, args.port, clientId=args.client_id, timeout=15)
    except Exception as e:
        print(f"Could not connect to TWS/Gateway at {args.host}:{args.port} — is it running with the "
              f"API enabled?\n  {e}", file=sys.stderr)
        return 1

    try:
        ib.reqMarketDataType(3 if args.delayed else 1)

        underlying = Index(sym, "CBOE", "USD") if sym in INDEX_SYMBOLS else Stock(sym, "SMART", "USD")
        if not ib.qualifyContracts(underlying):
            print(f"Could not qualify underlying {sym}.", file=sys.stderr)
            return 1

        [u_tkr] = ib.reqTickers(underlying)
        spot = u_tkr.marketPrice()
        if not spot or spot != spot:
            spot = u_tkr.close or u_tkr.last
        if not spot:
            print("Could not get underlying price (try --delayed or check subscriptions).", file=sys.stderr)
            return 1
        print(f"{sym} spot = {spot:.2f}")

        params = ib.reqSecDefOptParams(underlying.symbol, "", underlying.secType, underlying.conId)
        if not params:
            print("No option parameters returned.", file=sys.stderr)
            return 1
        chain = sorted(params, key=lambda c: (c.tradingClass == "SPXW", c.exchange == "SMART"), reverse=True)[0]

        expirations = sorted(chain.expirations)
        today = dt.date.today().strftime("%Y%m%d")
        want = args.expiry or today
        expiry = want if want in expirations else next((e for e in expirations if e >= want), None)
        if not expiry:
            print(f"No expiration on/after {want}. Available: {expirations[:6]}...", file=sys.stderr)
            return 1
        print(f"Expiry = {expiry}  ({'0DTE' if expiry == today else 'nearest'})")

        all_strikes = sorted(chain.strikes)
        i = min(range(len(all_strikes)), key=lambda j: abs(all_strikes[j] - spot))
        strikes = all_strikes[max(0, i - args.strikes): i + args.strikes + 1]
        tclass = chain.tradingClass or sym

        contracts = [Option(sym, expiry, k, r, "SMART", tradingClass=tclass)
                     for k in strikes for r in ("C", "P")]
        contracts = [c for c in ib.qualifyContracts(*contracts) if c.conId]
        print(f"Requesting {len(contracts)} contracts...")
        tickers = ib.reqTickers(*contracts)

        rows = []
        for t in tickers:
            c = t.contract
            g = t.modelGreeks
            rows.append({
                "expiry": c.lastTradeDateOrContractMonth, "strike": c.strike,
                "right": "CALL" if c.right == "C" else "PUT",
                "bid": _clean(t.bid), "ask": _clean(t.ask), "last": _clean(t.last), "mark": _mid(t),
                "volume": _clean(t.volume),
                "iv": round(g.impliedVol, 4) if g and g.impliedVol else None,
                "delta": round(g.delta, 4) if g and g.delta else None,
                "gamma": round(g.gamma, 5) if g and g.gamma else None,
                "theta": round(g.theta, 4) if g and g.theta else None,
            })

        chain_df = pd.DataFrame(rows).sort_values(["right", "strike"]).reset_index(drop=True)
        out = args.out or f"{sym}_chains.xlsx"
        stamp = dt.datetime.now().strftime("%H%M")
        meta_row = {
            "pulled_at": dt.datetime.now().isoformat(timespec="seconds"),
            "symbol": sym, "spot": round(spot, 2), "expiry": expiry,
            "rows": len(chain_df), "data_type": "delayed" if args.delayed else "real-time",
        }
        append_workbook(out, chain_df, meta_row, f"{expiry}_{stamp}")
        print(f"Saved {len(chain_df)} rows to {out} (sheet {expiry}_{stamp})")

        if args.signal:
            run_signal(ib, underlying, sym, spot, expiry, rows, args.max_risk)
        return 0
    finally:
        ib.disconnect()


def run_signal(ib, underlying, sym, spot, expiry, rows, max_risk):
    """Compute the rule-based signal from IBKR history + the live chain."""
    import json
    eng = load_signal_engine()
    eng.MAX_RISK = float(max_risk)

    bars = ib.reqHistoricalData(underlying, endDateTime="", durationStr="40 D",
                                barSizeSetting="1 day", whatToShow="TRADES", useRTH=True)
    if not bars or len(bars) < 21:
        print("Not enough history for a signal; skipping signal.json.", file=sys.stderr)
        return
    closes = [b.close for b in bars][::-1]   # newest-first
    sig = eng.compute_signal(closes)

    chain = {}
    for r in rows:
        if r["mark"] is None and not (r["bid"] and r["ask"]):
            continue
        typ = "call" if r["right"] == "CALL" else "put"
        chain[(typ, round(r["strike"], 2))] = {"mark": r["mark"], "bid": r["bid"], "ask": r["ask"]}
    exp_iso = f"{expiry[:4]}-{expiry[4:6]}-{expiry[6:]}"
    trade = eng.build_trade_live(spot, sig, {"chain": chain, "expiration": exp_iso})
    if trade is None:
        trade = {"type": "none", "strategy": "No trade",
                 "rationale": "Could not build a defined-risk trade from the live chain.",
                 "expiry": f"0DTE ({exp_iso})"}
    trade["pricing"], trade["instrument"] = "live", sym

    direction = sig["direction"]
    payload = {
        "signal": "CALL" if direction == "BULLISH" else "PUT" if direction == "BEARISH" else "NONE",
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
        "confidence": sig["conviction"], "direction": direction,
        "spx": round(spot, 2), "pricing": "live", "instrument": sym,
        "rsi": sig["rsi"], "realized_vol": sig["rv"], "vol_regime": sig["regime"],
        "trade": trade,
        "disclaimer": "Educational rule-based signal from live IBKR data. 0DTE options are extremely "
                      "high risk and can expire worthless. Not financial advice.",
    }
    for path in (eng.OUT, eng.OUT_DASH):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"Signal: {direction} conviction={sig['conviction']} -> {trade['strategy']} "
          f"(wrote {eng.OUT})")


if __name__ == "__main__":
    sys.exit(main())
