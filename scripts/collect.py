#!/usr/bin/env python3
"""Collect institutional 13F flows from SEC EDGAR (+ optional FRED macro) and
write a feed the PWA renders.

For each institution in watchlists/institutions.json we:
  1. validate the CIK (entity name match + has 13F-HR filings),
  2. fetch the two most recent 13F-HR information tables,
  3. diff holdings per CUSIP to classify NEW / ADD / TRIM / EXIT,
  4. tag holdings that map to a top-100 S&P ticker.

Significant moves are written to dashboard/events.json.

Network: SEC EDGAR is free but requires a descriptive User-Agent with a
contact email (set SEC_USER_AGENT or it falls back to a default). FRED needs
FRED_API_KEY (optional; skipped if unset).

Run:  python3 scripts/collect.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent
WATCHLISTS = ROOT / "scripts" / "watchlists"
OUT = ROOT / "dashboard" / "events.json"

USER_AGENT = os.environ.get("SEC_USER_AGENT", "SPX0DTE-Signals research khurrumtx@gmail.com")
FRED_API_KEY = os.environ.get("FRED_API_KEY", "").strip()

# Thresholds for what counts as a "major event".
MIN_VALUE = float(os.environ.get("MIN_EVENT_VALUE", "50000000"))   # ~$50M reported value
MIN_PCT = float(os.environ.get("MIN_EVENT_PCT", "0.20"))           # 20% share change
MAX_EVENTS = int(os.environ.get("MAX_EVENTS", "120"))
REQUEST_PAUSE = 0.20  # be polite: <10 req/s

FRED_SERIES = [
    ("FEDFUNDS", "Fed Funds Rate"),
    ("DGS10", "10Y Treasury"),
    ("T10Y2Y", "10Y-2Y Spread"),
    ("CPIAUCSL", "CPI"),
    ("UNRATE", "Unemployment"),
    ("VIXCLS", "VIX"),
]


def http_get(url: str, accept: str = "application/json") -> bytes:
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept-Encoding": "gzip, deflate",
        "Accept": accept,
    })
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    import gzip
                    raw = gzip.decompress(raw)
                return raw
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                raise
            time.sleep(2 ** attempt)
        except Exception:
            time.sleep(2 ** attempt)
    raise RuntimeError(f"failed to GET {url}")


def get_json(url: str) -> dict:
    time.sleep(REQUEST_PAUSE)
    return json.loads(http_get(url, "application/json").decode("utf-8", "replace"))


def get_text(url: str) -> str:
    time.sleep(REQUEST_PAUSE)
    return http_get(url, "*/*").decode("utf-8", "replace")


# ---------- name normalization & ticker mapping ----------

_SUFFIXES = re.compile(
    r"\b(INC|INCORPORATED|CORP|CORPORATION|CO|COMPANY|LTD|LIMITED|PLC|LP|LLC|"
    r"HOLDINGS|HOLDING|GROUP|TRUST|THE|SA|NV|AG|CLASS|COM|COMMON|STOCK|"
    r"NEW|DEL|DE|ADR|SP|ORD|SHS|SER|REIT)\b",
    re.I,
)


def normalize(name: str) -> str:
    name = name.upper()
    name = re.sub(r"[^A-Z0-9 ]", " ", name)
    name = _SUFFIXES.sub(" ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def load_ticker_map() -> tuple[dict, dict]:
    """Return (norm_title -> ticker, ticker -> CIKint) from SEC's official map."""
    data = get_json("https://www.sec.gov/files/company_tickers.json")
    by_name, by_ticker = {}, {}
    for row in data.values():
        ticker = str(row["ticker"]).upper()
        cik = int(row["cik_str"])
        title = normalize(str(row["title"]))
        by_ticker[ticker] = cik
        if title and title not in by_name:
            by_name[title] = ticker
    return by_name, by_ticker


# ---------- 13F retrieval ----------

def institution_recent_13f(cik10: str) -> tuple[str, list[dict]]:
    """Return (entity_name, [filing dicts]) for recent 13F-HR filings, newest first."""
    url = f"https://data.sec.gov/submissions/CIK{cik10}.json"
    data = get_json(url)
    name = data.get("name", "")
    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accns = recent.get("accessionNumber", [])
    dates = recent.get("filingDate", [])
    primary = recent.get("primaryDocument", [])
    report_dates = recent.get("reportDate", [])
    out = []
    for i, form in enumerate(forms):
        if form == "13F-HR":
            out.append({
                "accession": accns[i],
                "filed": dates[i] if i < len(dates) else "",
                "report_date": report_dates[i] if i < len(report_dates) else "",
                "primary": primary[i] if i < len(primary) else "",
            })
    return name, out


def fetch_info_table(cik_int: int, accession: str) -> list[dict]:
    """Download a 13F filing's information table and return aggregated holdings."""
    accn_nodash = accession.replace("-", "")
    base = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{accn_nodash}"
    index = get_json(f"{base}/index.json")
    xml_files = [
        it["name"] for it in index.get("directory", {}).get("item", [])
        if it["name"].lower().endswith(".xml")
    ]
    table_xml = None
    for fname in xml_files:
        text = get_text(f"{base}/{fname}")
        if "infoTable" in text or "informationtable" in text.lower():
            table_xml = text
            break
    if not table_xml:
        return []
    return parse_info_table(table_xml)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def parse_info_table(xml_text: str) -> dict:
    """Parse holdings -> {cusip: {issuer, shares, value}} aggregated per CUSIP."""
    holdings: dict[str, dict] = {}
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return holdings
    for info in root.iter():
        if _local(info.tag) != "infotable":
            continue
        issuer = cusip = ""
        value = shares = 0.0
        for child in info.iter():
            tag = _local(child.tag)
            txt = (child.text or "").strip()
            if tag == "nameofissuer":
                issuer = txt
            elif tag == "cusip":
                cusip = txt.upper()
            elif tag == "value" and txt:
                try:
                    value = float(txt.replace(",", ""))
                except ValueError:
                    pass
            elif tag == "sshprnamt" and txt:
                try:
                    shares = float(txt.replace(",", ""))
                except ValueError:
                    pass
        if not cusip:
            continue
        h = holdings.setdefault(cusip, {"issuer": issuer, "shares": 0.0, "value": 0.0})
        h["shares"] += shares
        h["value"] += value
        if issuer and not h["issuer"]:
            h["issuer"] = issuer
    return holdings


def classify(prev: float, curr: float) -> tuple[str, float]:
    if prev <= 0 and curr > 0:
        return "NEW", 1.0
    if prev > 0 and curr <= 0:
        return "EXIT", -1.0
    if prev <= 0 and curr <= 0:
        return "", 0.0
    pct = (curr - prev) / prev
    if pct >= MIN_PCT:
        return "ADD", pct
    if pct <= -MIN_PCT:
        return "TRIM", pct
    return "", pct


def diff_holdings(inst_name, cik, prev, curr, report_date, filed,
                  name_to_ticker, sp100) -> list[dict]:
    events = []
    cusips = set(prev) | set(curr)
    for cusip in cusips:
        p = prev.get(cusip, {})
        c = curr.get(cusip, {})
        p_sh, c_sh = p.get("shares", 0.0), c.get("shares", 0.0)
        action, pct = classify(p_sh, c_sh)
        if not action:
            continue
        issuer = c.get("issuer") or p.get("issuer") or ""
        value = c.get("value", 0.0) or p.get("value", 0.0)
        ticker = name_to_ticker.get(normalize(issuer))
        in_watch = bool(ticker and ticker in sp100)
        # value reported in $1000s historically; treat large raw values as already-dollars
        if value < MIN_VALUE and not (action in ("NEW", "EXIT") and value > 0):
            if value * 1000 < MIN_VALUE:
                continue
        events.append({
            "institution": inst_name,
            "cik": cik,
            "action": action,
            "ticker": ticker,
            "issuer": issuer,
            "cusip": cusip,
            "shares_prev": int(p_sh),
            "shares_curr": int(c_sh),
            "pct_change": round(pct, 4),
            "value": int(value),
            "quarter": report_date,
            "filed": filed,
            "in_watchlist": in_watch,
        })
    return events


def collect_fred() -> list[dict]:
    if not FRED_API_KEY:
        return []
    out = []
    for series_id, label in FRED_SERIES:
        try:
            url = (
                "https://api.stlouisfed.org/fred/series/observations"
                f"?series_id={series_id}&api_key={FRED_API_KEY}&file_type=json"
                "&sort_order=desc&limit=2"
            )
            data = get_json(url)
            obs = data.get("observations", [])
            if not obs:
                continue
            latest = obs[0]
            prev = obs[1] if len(obs) > 1 else None
            out.append({
                "series": series_id,
                "label": label,
                "value": latest.get("value"),
                "date": latest.get("date"),
                "prev": prev.get("value") if prev else None,
            })
        except Exception as e:
            print(f"  ! FRED {series_id} failed: {e}", file=sys.stderr)
    return out


def main() -> int:
    sp100 = set(json.loads((WATCHLISTS / "sp100.json").read_text())["tickers"])
    institutions = json.loads((WATCHLISTS / "institutions.json").read_text())["institutions"]

    print("Loading SEC ticker map...")
    name_to_ticker, _ = load_ticker_map()

    all_events: list[dict] = []
    processed, skipped = [], []

    for inst in institutions:
        cik10 = str(inst["cik"]).zfill(10)
        cik_int = int(cik10)
        want = normalize(inst["name"]).split(" ")[0]
        try:
            entity_name, filings = institution_recent_13f(cik10)
        except Exception as e:
            skipped.append(f'{inst["name"]} ({cik10}): {e}')
            continue

        if want and want not in normalize(entity_name):
            print(f'  ? name mismatch for {inst["name"]}: EDGAR has "{entity_name}"', file=sys.stderr)
        if len(filings) < 2:
            skipped.append(f'{inst["name"]}: <2 13F-HR filings')
            continue

        try:
            curr = fetch_info_table(cik_int, filings[0]["accession"])
            prev = fetch_info_table(cik_int, filings[1]["accession"])
        except Exception as e:
            skipped.append(f'{inst["name"]}: holdings fetch failed: {e}')
            continue

        evs = diff_holdings(
            inst["name"], cik10, prev, curr,
            filings[0].get("report_date", ""), filings[0].get("filed", ""),
            name_to_ticker, sp100,
        )
        all_events.extend(evs)
        processed.append(f'{inst["name"]}: {len(evs)} events ({entity_name})')
        print(f'  + {inst["name"]}: {len(evs)} events')

    # Rank: watchlist hits first, then by reported value.
    all_events.sort(key=lambda e: (e["in_watchlist"], e["value"]), reverse=True)
    all_events = all_events[:MAX_EVENTS]

    payload = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "events": all_events,
        "fred": collect_fred(),
        "meta": {
            "institutions_processed": processed,
            "institutions_skipped": skipped,
            "min_value": MIN_VALUE,
            "min_pct": MIN_PCT,
            "watchlist_size": len(sp100),
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2))
    print(f"\nWrote {len(all_events)} events to {OUT}")
    if skipped:
        print("Skipped:", *skipped, sep="\n  ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
