#!/usr/bin/env python3
"""Pull a LIVE 0DTE option chain from Interactive Brokers and save it to Excel.

IMPORTANT: This runs on YOUR OWN COMPUTER, not the cloud app or GitHub Actions.
IBKR's API needs a logged-in Trader Workstation (TWS) or IB Gateway session, so
it can't run in CI.

Prerequisites
-------------
1. An Interactive Brokers account.
2. TWS or IB Gateway installed, running, and logged in.
3. Enable the API:  TWS/Gateway -> File/Configure -> Settings -> API -> Settings
   -> check "Enable ActiveX and Socket Clients" (and add 127.0.0.1 to trusted IPs).
4. Install the Python deps:
       pip install ib_insync pandas openpyxl
   Default socket ports:  TWS live 7496 / paper 7497;  Gateway live 4001 / paper 4002.

Examples
--------
    python3 scripts/ibkr_chain.py --symbol SPX --port 7497 --out spx_chain.xlsx
    python3 scripts/ibkr_chain.py --symbol SPY --strikes 40 --delayed
    python3 scripts/ibkr_chain.py --symbol SPX --expiry 20260526

Notes
-----
- Real-time option quotes need the matching IBKR market-data subscription
  (e.g. OPRA, CBOE indices). Without one, pass --delayed for delayed quotes.
- SPX uses the weekly/daily class 'SPXW' for 0DTE; this is handled automatically.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys

INDEX_SYMBOLS = {"SPX", "SPXW", "NDX", "RUT", "VIX", "DJX", "XSP"}


def parse_args():
    p = argparse.ArgumentParser(description="Save a live IBKR 0DTE option chain to Excel.")
    p.add_argument("--symbol", default="SPX", help="Underlying (SPX, SPY, QQQ, ...). Default SPX.")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=7497, help="TWS 7497(paper)/7496(live); Gateway 4002/4001.")
    p.add_argument("--client-id", type=int, default=17)
    p.add_argument("--expiry", default="", help="YYYYMMDD. Default = today (0DTE) or nearest.")
    p.add_argument("--strikes", type=int, default=30, help="Number of strikes each side of spot. Default 30.")
    p.add_argument("--delayed", action="store_true", help="Use delayed data (no real-time subscription needed).")
    p.add_argument("--out", default="", help="Output .xlsx path. Default <symbol>_chain_<date>.xlsx")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    try:
        from ib_insync import IB, Index, Stock, Option
    except ImportError:
        print("Missing dependency. Run:  pip install ib_insync pandas openpyxl", file=sys.stderr)
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
        # 1=real-time, 3=delayed, 4=delayed-frozen
        ib.reqMarketDataType(3 if args.delayed else 1)

        # ---- underlying ----
        if sym in INDEX_SYMBOLS:
            underlying = Index(sym, "CBOE", "USD")
        else:
            underlying = Stock(sym, "SMART", "USD")
        if not ib.qualifyContracts(underlying):
            print(f"Could not qualify underlying {sym}.", file=sys.stderr)
            return 1

        [u_tkr] = ib.reqTickers(underlying)
        spot = u_tkr.marketPrice()
        if not spot or spot != spot:  # NaN guard
            spot = u_tkr.close or u_tkr.last
        if not spot:
            print("Could not get underlying price (try --delayed or check subscriptions).", file=sys.stderr)
            return 1
        print(f"{sym} spot = {spot:.2f}")

        # ---- option parameters ----
        params = ib.reqSecDefOptParams(underlying.symbol, "", underlying.secType, underlying.conId)
        if not params:
            print("No option parameters returned for this symbol.", file=sys.stderr)
            return 1
        # Prefer SMART; for SPX prefer the weekly/daily 'SPXW' class (has 0DTE).
        def chain_rank(c):
            return (c.tradingClass == "SPXW", c.exchange == "SMART")
        chain = sorted(params, key=chain_rank, reverse=True)[0]

        expirations = sorted(chain.expirations)
        today = dt.date.today().strftime("%Y%m%d")
        want = args.expiry or today
        expiry = want if want in expirations else next((e for e in expirations if e >= want), None)
        if not expiry:
            print(f"No expiration on/after {want}. Available: {expirations[:6]}...", file=sys.stderr)
            return 1
        print(f"Expiry = {expiry}  ({'0DTE' if expiry == today else 'nearest'})")

        # strikes window around spot
        all_strikes = sorted(chain.strikes)
        nearest_i = min(range(len(all_strikes)), key=lambda i: abs(all_strikes[i] - spot))
        lo = max(0, nearest_i - args.strikes)
        hi = min(len(all_strikes), nearest_i + args.strikes + 1)
        strikes = all_strikes[lo:hi]

        tclass = chain.tradingClass or sym
        contracts = []
        for strike in strikes:
            for right in ("C", "P"):
                contracts.append(Option(sym, expiry, strike, right, "SMART", tradingClass=tclass))
        contracts = [c for c in ib.qualifyContracts(*contracts) if c.conId]
        print(f"Requesting {len(contracts)} contracts...")

        tickers = ib.reqTickers(*contracts)

        rows = []
        for t in tickers:
            c = t.contract
            g = t.modelGreeks
            rows.append({
                "expiry": c.lastTradeDateOrContractMonth,
                "strike": c.strike,
                "right": "CALL" if c.right == "C" else "PUT",
                "bid": _clean(t.bid),
                "ask": _clean(t.ask),
                "last": _clean(t.last),
                "mark": _mid(t),
                "volume": _clean(t.volume),
                "open_interest": _clean(getattr(t, "callOpenInterest", None) if c.right == "C"
                                        else getattr(t, "putOpenInterest", None)),
                "iv": round(g.impliedVol, 4) if g and g.impliedVol else None,
                "delta": round(g.delta, 4) if g and g.delta else None,
                "gamma": round(g.gamma, 5) if g and g.gamma else None,
                "theta": round(g.theta, 4) if g and g.theta else None,
            })

        df = pd.DataFrame(rows).sort_values(["right", "strike"]).reset_index(drop=True)
        out = args.out or f"{sym}_chain_{expiry}.xlsx"
        with pd.ExcelWriter(out, engine="openpyxl") as xl:
            df.to_excel(xl, index=False, sheet_name=f"{sym} {expiry}")
            meta = pd.DataFrame([
                {"field": "symbol", "value": sym},
                {"field": "spot", "value": round(spot, 2)},
                {"field": "expiry", "value": expiry},
                {"field": "pulled_at", "value": dt.datetime.now().isoformat(timespec="seconds")},
                {"field": "data_type", "value": "delayed" if args.delayed else "real-time"},
                {"field": "rows", "value": len(df)},
            ])
            meta.to_excel(xl, index=False, sheet_name="meta")
        print(f"Saved {len(df)} rows to {out}")
        return 0
    finally:
        ib.disconnect()


def _clean(v):
    try:
        if v is None or v != v:  # NaN
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _mid(t):
    b, a = _clean(t.bid), _clean(t.ask)
    if b and a:
        return round((b + a) / 2, 2)
    return _clean(t.last)


if __name__ == "__main__":
    sys.exit(main())
