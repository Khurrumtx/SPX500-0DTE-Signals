#!/usr/bin/env python3
"""Daily SEC insider activity via the Alpha Vantage API (SEC-derived, clean JSON).

This is an alternative to scripts/collect_daily.py (which scrapes EDGAR directly).
Alpha Vantage's INSIDER_TRANSACTIONS endpoint returns parsed Form 4 data per
ticker, so there's no XML/index scraping. It writes the same dashboard/daily.json
schema the PWA already renders.

Key handling:
  - Set ALPHAVANTAGE_API_KEY as an environment variable / GitHub Actions secret.
    NEVER commit the key. Free tier = 25 requests/day & 1 req/sec, so it can only
    cover a handful of tickers per day; a premium key is needed for all 100.
  - INSTITUTIONAL_HOLDINGS (for the quarterly events.json view) is a premium
    endpoint; this script focuses on the free-tier-accessible insider feed.

Run:  ALPHAVANTAGE_API_KEY=xxx python3 scripts/collect_av.py
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WATCHLISTS = ROOT / "scripts" / "watchlists"
OUT = ROOT / "dashboard" / "daily.json"

API_KEY = os.environ.get("ALPHAVANTAGE_API_KEY", "").strip()
BASE = "https://www.alphavantage.co/query"
MAX_TICKERS = int(os.environ.get("AV_MAX_TICKERS", "100"))
REQUEST_PAUSE = float(os.environ.get("AV_PAUSE", "1.2"))  # respect 1 req/sec
MAX_ACTIVITY = int(os.environ.get("MAX_ACTIVITY", "400"))
LOOKBACK_DAYS = int(os.environ.get("LOOKBACK_DAYS", "30"))


def av_get(function: str, **params) -> dict:
    params.update({"function": function, "apikey": API_KEY})
    url = BASE + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "SPX0DTE-Signals"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def _num(s) -> float:
    try:
        return float(str(s).replace(",", ""))
    except (ValueError, TypeError):
        return 0.0


def insider_to_activity(data: dict, ticker: str, since: str) -> list[dict]:
    """Aggregate Alpha Vantage insider rows into one BUY/SELL item per
    (executive, date), counting only priced (open-market) common-stock trades."""
    rows = data.get("data", []) if isinstance(data, dict) else []
    groups: dict[tuple, dict] = {}
    for r in rows:
        date = r.get("transaction_date", "")
        if not date or date < since:
            continue
        price = _num(r.get("share_price"))
        shares = _num(r.get("shares"))
        if price <= 0 or shares <= 0:
            continue  # skip grants / RSU vesting / $0 rows (not market signals)
        ad = (r.get("acquisition_or_disposal") or "").upper()
        exec_name = r.get("executive", "").strip()
        key = (exec_name, date)
        g = groups.setdefault(key, {
            "owner": exec_name, "title": r.get("executive_title", "").strip(),
            "date": date, "buy_sh": 0.0, "buy_val": 0.0, "sell_sh": 0.0, "sell_val": 0.0,
        })
        if ad == "A":
            g["buy_sh"] += shares; g["buy_val"] += shares * price
        elif ad == "D":
            g["sell_sh"] += shares; g["sell_val"] += shares * price

    out = []
    for g in groups.values():
        if g["buy_val"] == 0 and g["sell_val"] == 0:
            continue
        if g["buy_val"] >= g["sell_val"]:
            action, shares, value = "BUY", g["buy_sh"], g["buy_val"]
        else:
            action, shares, value = "SELL", g["sell_sh"], g["sell_val"]
        out.append({
            "date": g["date"], "ticker": ticker, "company": "",
            "form": "4", "type": "insider",
            "owner": g["owner"], "title": g["title"],
            "action": action, "shares": int(shares), "value": int(value),
            "open_market": True, "ten_pct": False, "codes": [],
        })
    return out


def main() -> int:
    if not API_KEY:
        print("ERROR: set ALPHAVANTAGE_API_KEY", file=sys.stderr)
        return 1

    tickers = json.loads((WATCHLISTS / "sp100.json").read_text())["tickers"][:MAX_TICKERS]
    since = (datetime.now(timezone.utc).date().toordinal() - LOOKBACK_DAYS)
    since_str = datetime.fromordinal(since).strftime("%Y-%m-%d")

    activity: list[dict] = []
    done = skipped = 0
    for t in tickers:
        try:
            data = av_get("INSIDER_TRANSACTIONS", symbol=t, from_date=since_str)
        except Exception as e:
            print(f"  ! {t}: {e}", file=sys.stderr)
            skipped += 1
            time.sleep(REQUEST_PAUSE)
            continue
        if "Information" in data or "Note" in data:
            print(f"  rate-limited at {t} after {done} tickers — stopping", file=sys.stderr)
            break
        items = insider_to_activity(data, t, since_str)
        activity.extend(items)
        done += 1
        print(f"  + {t}: {len(items)} items")
        time.sleep(REQUEST_PAUSE)

    activity.sort(key=lambda a: (a["date"], a.get("value", 0)), reverse=True)
    activity = activity[:MAX_ACTIVITY]

    payload = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "lookback_days": LOOKBACK_DAYS,
        "source": "alphavantage",
        "activity": activity,
        "meta": {
            "total": len(activity),
            "insider": len(activity),
            "stake": 0,
            "tickers_covered": done,
            "tickers_skipped": skipped,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2))
    print(f"Wrote {len(activity)} activity items from {done} tickers to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
