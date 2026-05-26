#!/usr/bin/env python3
"""Summarize SEC 13F institutional ownership for the top-100 S&P names.

Instead of tracking a handful of named funds, this pulls SEC's *bulk* quarterly
13F data sets (every filer's holdings as TSV) for the two most recent quarters,
diffs each manager's holdings per stock, and produces a per-stock summary of who
is buying and who is selling across ALL institutions and banks.

Output (dashboard/events.json):
  - stocks[]:  one row per top-100 stock -> #buyers/#sellers/#holders, net share
               & value change, plus top buyers and top sellers (by share delta).
  - events[]:  the largest individual buy/sell moves across all filers.
  - fred[]:    optional macro context (needs FRED_API_KEY).

Network: SEC is free but wants a descriptive User-Agent w/ contact email
(SEC_USER_AGENT). The bulk data sets are large; this is meant to run in CI.

Run:  python3 scripts/collect.py
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WATCHLISTS = ROOT / "scripts" / "watchlists"
OUT = ROOT / "dashboard" / "events.json"

USER_AGENT = os.environ.get("SEC_USER_AGENT", "SPX0DTE-Signals research khurrumtx@gmail.com")
FRED_API_KEY = os.environ.get("FRED_API_KEY", "").strip()

MIN_PCT = float(os.environ.get("MIN_EVENT_PCT", "0.20"))          # 20% share change = ADD/TRIM
MIN_EVENT_VALUE = float(os.environ.get("MIN_EVENT_VALUE", "25000000"))  # min $ move for events feed
MAX_EVENTS = int(os.environ.get("MAX_EVENTS", "150"))
TOP_MOVERS = int(os.environ.get("TOP_MOVERS", "5"))              # top buyers/sellers per stock
MAX_QUARTERS_BACK = 8

# Candidate URL templates for the bulk 13F data sets (SEC has moved these around).
DATASET_URL_TEMPLATES = [
    "https://www.sec.gov/files/structureddata/data/form-13f-data-sets/{y}q{q}_form13f.zip",
    "https://www.sec.gov/files/dera/data/form-13f-data-sets/{y}q{q}_form13f.zip",
    "https://www.sec.gov/Archives/edgar/full-index/form13f/{y}q{q}_form13f.zip",
]

FRED_SERIES = [
    ("FEDFUNDS", "Fed Funds Rate"),
    ("DGS10", "10Y Treasury"),
    ("T10Y2Y", "10Y-2Y Spread"),
    ("VIXCLS", "VIX"),
]


# ---------------- HTTP ----------------

def http_get(url: str, accept: str = "*/*") -> bytes:
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept-Encoding": "gzip, deflate",
        "Accept": accept,
    })
    last = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    import gzip
                    raw = gzip.decompress(raw)
                return raw
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                raise
            last = e
            time.sleep(2 ** attempt)
        except Exception as e:
            last = e
            time.sleep(2 ** attempt)
    raise RuntimeError(f"GET failed {url}: {last}")


def get_json(url: str) -> dict:
    return json.loads(http_get(url, "application/json").decode("utf-8", "replace"))


# ---------------- name normalization ----------------

_SUFFIXES = re.compile(
    r"\b(INC|INCORPORATED|CORP|CORPORATION|CO|COMPANY|LTD|LIMITED|PLC|LP|LLC|"
    r"HOLDINGS|HOLDING|GROUP|TRUST|THE|SA|NV|AG|CLASS|COM|COMMON|STOCK|"
    r"NEW|DEL|DE|ADR|SP|ORD|SHS|SER|REIT)\b",
    re.I,
)
_norm_cache: dict[str, str] = {}


def normalize(name: str) -> str:
    if name in _norm_cache:
        return _norm_cache[name]
    s = name.upper()
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    s = _SUFFIXES.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    _norm_cache[name] = s
    return s


def load_universe():
    """Return (top100_names set, name->ticker dict) for the S&P watchlist."""
    sp100 = json.loads((WATCHLISTS / "sp100.json").read_text())["tickers"]
    sp100_set = {t.upper() for t in sp100}
    data = get_json("https://www.sec.gov/files/company_tickers.json")
    name_to_ticker = {}
    top_names = set()
    for row in data.values():
        ticker = str(row["ticker"]).upper()
        nname = normalize(str(row["title"]))
        if nname and nname not in name_to_ticker:
            name_to_ticker[nname] = ticker
        if ticker in sp100_set and nname:
            top_names.add(nname)
    return top_names, name_to_ticker


def load_highlights() -> list[str]:
    insts = json.loads((WATCHLISTS / "institutions.json").read_text())["institutions"]
    return [normalize(i["name"]).split(" ")[0] for i in insts if normalize(i["name"])]


# ---------------- bulk 13F data sets ----------------

def quarter_labels(n: int) -> list[tuple[int, int]]:
    now = datetime.now(timezone.utc)
    y, q = now.year, (now.month - 1) // 3 + 1
    out = []
    for _ in range(n):
        out.append((y, q))
        q -= 1
        if q == 0:
            q = 4
            y -= 1
    return out


def download_dataset(y: int, q: int) -> bytes | None:
    for tmpl in DATASET_URL_TEMPLATES:
        url = tmpl.format(y=y, q=q)
        try:
            data = http_get(url, "application/zip")
            if data[:2] == b"PK":
                print(f"  downloaded {y}Q{q} from {url} ({len(data)//1_000_000}MB)")
                return data
        except urllib.error.HTTPError:
            continue
        except Exception as e:
            print(f"  ! {url}: {e}", file=sys.stderr)
    return None


def _find_member(zf: zipfile.ZipFile, name: str) -> str | None:
    target = name.lower()
    for n in zf.namelist():
        if n.lower().split("/")[-1] == target:
            return n
    return None


def _reader(zf: zipfile.ZipFile, member: str):
    f = io.TextIOWrapper(zf.open(member), encoding="utf-8", errors="replace")
    return csv.DictReader(f, delimiter="\t")


def load_quarter_holdings(zip_bytes: bytes, top_names: set[str],
                          name_to_ticker: dict, extra_cusips: set[str] | None = None):
    """Return ({(manager, cusip): {shares, value, issuer, ticker, cusip}}, learned_cusips)."""
    extra_cusips = extra_cusips or set()
    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))

    cover = _find_member(zf, "COVERPAGE.tsv")
    info = _find_member(zf, "INFOTABLE.tsv")
    if not cover or not info:
        print(f"  ! missing tables (cover={cover}, info={info})", file=sys.stderr)
        return {}, set()

    # accession -> filing manager name
    acc_mgr: dict[str, str] = {}
    for row in _reader(zf, cover):
        acc = (row.get("ACCESSION_NUMBER") or "").strip()
        mgr = (row.get("FILINGMANAGER_NAME") or "").strip()
        if acc and mgr:
            acc_mgr[acc] = mgr

    # stream holdings, keep only watchlist stocks
    agg: dict[tuple[str, str], dict] = {}
    acc_rows: dict[str, int] = defaultdict(int)
    learned: set[str] = set()

    for row in _reader(zf, info):
        if (row.get("SSHPRNAMTTYPE") or "").strip().upper() != "SH":
            continue
        if (row.get("PUTCALL") or "").strip():
            continue
        issuer = (row.get("NAMEOFISSUER") or "").strip()
        cusip = (row.get("CUSIP") or "").strip().upper()
        if not cusip:
            continue
        nname = normalize(issuer)
        ticker = name_to_ticker.get(nname)
        is_watch = nname in top_names or cusip in extra_cusips
        if not is_watch:
            continue
        learned.add(cusip)
        acc = (row.get("ACCESSION_NUMBER") or "").strip()
        try:
            shares = float((row.get("SSHPRNAMT") or "0").replace(",", ""))
            value = float((row.get("VALUE") or "0").replace(",", ""))
        except ValueError:
            continue
        key = (acc, cusip)
        a = agg.setdefault(key, {"shares": 0.0, "value": 0.0, "issuer": issuer, "ticker": ticker, "cusip": cusip})
        a["shares"] += shares
        a["value"] += value
        acc_rows[acc] += 1

    # pick each manager's most complete filing (handles amendments / multiple filings)
    best_acc: dict[str, str] = {}
    for acc, mgr in acc_mgr.items():
        if acc not in acc_rows:
            continue
        if mgr not in best_acc or acc_rows[acc] > acc_rows[best_acc[mgr]]:
            best_acc[mgr] = acc

    holdings: dict[tuple[str, str], dict] = {}
    for (acc, cusip), a in agg.items():
        mgr = acc_mgr.get(acc)
        if not mgr or best_acc.get(mgr) != acc:
            continue
        k = (mgr, cusip)
        h = holdings.setdefault(k, {"shares": 0.0, "value": 0.0, "issuer": a["issuer"], "ticker": a["ticker"], "cusip": cusip})
        h["shares"] += a["shares"]
        h["value"] += a["value"]
    return holdings, learned


# ---------------- diff & aggregate ----------------

def classify(prev: float, curr: float) -> str:
    if prev <= 0 and curr > 0:
        return "NEW"
    if prev > 0 and curr <= 0:
        return "EXIT"
    if prev <= 0:
        return ""
    pct = (curr - prev) / prev
    if pct >= MIN_PCT:
        return "ADD"
    if pct <= -MIN_PCT:
        return "TRIM"
    return "HOLD"


def summarize(prev_h: dict, curr_h: dict, highlights: list[str]):
    keys = set(prev_h) | set(curr_h)
    per_stock: dict[str, dict] = {}
    events: list[dict] = []

    for (mgr, cusip) in keys:
        p = prev_h.get((mgr, cusip), {})
        c = curr_h.get((mgr, cusip), {})
        p_sh, c_sh = p.get("shares", 0.0), c.get("shares", 0.0)
        p_val, c_val = p.get("value", 0.0), c.get("value", 0.0)
        action = classify(p_sh, c_sh)
        if not action:
            continue
        ref = c if c else p
        ticker = ref.get("ticker")
        issuer = ref.get("issuer", "")
        d_sh = c_sh - p_sh
        d_val = c_val - p_val
        nmgr = normalize(mgr)
        is_highlight = any(nmgr.startswith(h) or (" " + h) in (" " + nmgr) for h in highlights)

        st = per_stock.setdefault(cusip, {
            "ticker": ticker, "issuer": issuer, "cusip": cusip,
            "holders": 0, "buyers": 0, "sellers": 0, "new": 0, "exits": 0,
            "net_shares": 0.0, "net_value": 0.0, "_buyers": [], "_sellers": [],
        })
        if c_sh > 0:
            st["holders"] += 1
        st["net_shares"] += d_sh
        st["net_value"] += d_val
        if action in ("NEW", "ADD"):
            st["buyers"] += 1
            if action == "NEW":
                st["new"] += 1
            st["_buyers"].append((d_sh, mgr, action, d_val))
        elif action in ("TRIM", "EXIT"):
            st["sellers"] += 1
            if action == "EXIT":
                st["exits"] += 1
            st["_sellers"].append((d_sh, mgr, action, d_val))

        if action != "HOLD" and abs(d_val) >= MIN_EVENT_VALUE:
            events.append({
                "institution": mgr, "action": action,
                "ticker": ticker, "issuer": issuer, "cusip": cusip,
                "shares_prev": int(p_sh), "shares_curr": int(c_sh),
                "delta_shares": int(d_sh), "value": int(c_val or p_val),
                "delta_value": int(d_val), "highlight": is_highlight,
            })

    stocks = []
    for cusip, st in per_stock.items():
        buyers = sorted(st.pop("_buyers"), key=lambda x: x[0], reverse=True)[:TOP_MOVERS]
        sellers = sorted(st.pop("_sellers"), key=lambda x: x[0])[:TOP_MOVERS]
        st["top_buyers"] = [{"name": m, "action": a, "delta_shares": int(d), "delta_value": int(dv)} for d, m, a, dv in buyers]
        st["top_sellers"] = [{"name": m, "action": a, "delta_shares": int(d), "delta_value": int(dv)} for d, m, a, dv in sellers]
        st["net_shares"] = int(st["net_shares"])
        st["net_value"] = int(st["net_value"])
        stocks.append(st)

    stocks.sort(key=lambda s: abs(s["net_value"]), reverse=True)
    events.sort(key=lambda e: abs(e["delta_value"]), reverse=True)
    return stocks, events[:MAX_EVENTS]


# ---------------- FRED ----------------

def collect_fred() -> list[dict]:
    if not FRED_API_KEY:
        return []
    out = []
    for sid, label in FRED_SERIES:
        try:
            url = ("https://api.stlouisfed.org/fred/series/observations"
                   f"?series_id={sid}&api_key={FRED_API_KEY}&file_type=json&sort_order=desc&limit=2")
            obs = get_json(url).get("observations", [])
            if obs:
                out.append({"series": sid, "label": label,
                            "value": obs[0].get("value"), "date": obs[0].get("date"),
                            "prev": obs[1].get("value") if len(obs) > 1 else None})
        except Exception as e:
            print(f"  ! FRED {sid}: {e}", file=sys.stderr)
    return out


# ---------------- main ----------------

def main() -> int:
    print("Loading universe (SEC ticker map + S&P watchlist)...")
    top_names, name_to_ticker = load_universe()
    highlights = load_highlights()
    print(f"  {len(top_names)} watchlist issuer names, {len(highlights)} highlighted institutions")

    print("Locating two most recent 13F data sets...")
    datasets = []
    for (y, q) in quarter_labels(MAX_QUARTERS_BACK):
        data = download_dataset(y, q)
        if data:
            datasets.append(((y, q), data))
        if len(datasets) == 2:
            break
    if len(datasets) < 2:
        print("ERROR: could not find two consecutive 13F data sets.", file=sys.stderr)
        return 1

    (cy, cq), cur_bytes = datasets[0]
    (py, pq), prev_bytes = datasets[1]
    print(f"Current: {cy}Q{cq}  Previous: {py}Q{pq}")

    curr_h, learned = load_quarter_holdings(cur_bytes, top_names, name_to_ticker)
    prev_h, _ = load_quarter_holdings(prev_bytes, top_names, name_to_ticker, extra_cusips=learned)
    print(f"  holdings rows: current={len(curr_h)} previous={len(prev_h)}")

    stocks, events = summarize(prev_h, curr_h, highlights)

    payload = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "quarters": {"current": f"{cy}Q{cq}", "previous": f"{py}Q{pq}"},
        "stocks": stocks,
        "events": events,
        "fred": collect_fred(),
        "meta": {
            "stocks_with_activity": len(stocks),
            "total_events": len(events),
            "min_pct": MIN_PCT,
            "min_event_value": MIN_EVENT_VALUE,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2))
    print(f"Wrote {len(stocks)} stock summaries and {len(events)} events to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
