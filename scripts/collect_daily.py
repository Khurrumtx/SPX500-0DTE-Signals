#!/usr/bin/env python3
"""Daily SEC activity for the top-100 S&P names.

13F holdings are quarterly, so they can't tell you what happened *today*. This
script collects the filings that DO update daily and reveal buying/selling:

  - Form 4         insider transactions (officers, directors, 10%+ owners),
                   filed within ~2 business days; includes open-market buys (P)
                   and sells (S) with share counts and prices.
  - SC 13D / 13G   (+ /A amendments) filed when an institution crosses or
                   changes a 5%+ stake in a company.

It walks SEC's daily filing index for the last few days, keeps filings whose
subject is a top-100 company, parses Form 4 transaction details, and writes
dashboard/daily.json.

Network: SEC is free but wants a descriptive User-Agent w/ contact email
(SEC_USER_AGENT). Meant to run daily in CI.

Run:  python3 scripts/collect_daily.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent
WATCHLISTS = ROOT / "scripts" / "watchlists"
OUT = ROOT / "dashboard" / "daily.json"

USER_AGENT = os.environ.get("SEC_USER_AGENT", "SPX0DTE-Signals research khurrumtx@gmail.com")
LOOKBACK_DAYS = int(os.environ.get("LOOKBACK_DAYS", "5"))
MAX_FORM4_DETAIL = int(os.environ.get("MAX_FORM4_DETAIL", "500"))
MAX_ACTIVITY = int(os.environ.get("MAX_ACTIVITY", "400"))
REQUEST_PAUSE = 0.18

TARGET_FORMS = {"4", "SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A"}

# Open-market transaction codes worth surfacing (P=purchase, S=sale).
OPEN_MARKET = {"P", "S"}


def http_get(url: str, accept: str = "*/*") -> bytes:
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate", "Accept": accept,
    })
    last = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    import gzip
                    raw = gzip.decompress(raw)
                return raw
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                raise
            last = e; time.sleep(2 ** attempt)
        except Exception as e:
            last = e; time.sleep(2 ** attempt)
    raise RuntimeError(f"GET failed {url}: {last}")


def get_json(url: str) -> dict:
    time.sleep(REQUEST_PAUSE)
    return json.loads(http_get(url, "application/json").decode("utf-8", "replace"))


def get_text(url: str) -> str:
    time.sleep(REQUEST_PAUSE)
    return http_get(url, "*/*").decode("utf-8", "replace")


def load_cik_map() -> dict[int, str]:
    """Return {CIK int -> ticker} for the S&P watchlist."""
    sp100 = {t.upper() for t in json.loads((WATCHLISTS / "sp100.json").read_text())["tickers"]}
    data = get_json("https://www.sec.gov/files/company_tickers.json")
    out = {}
    for row in data.values():
        ticker = str(row["ticker"]).upper()
        if ticker in sp100:
            out[int(row["cik_str"])] = ticker
    return out


# ---------------- daily index ----------------

def parse_master_idx(text: str, cik_to_ticker: dict[int, str]) -> list[dict]:
    """Parse a daily master index, keep target forms about watchlist companies."""
    rows = []
    for line in text.splitlines():
        parts = line.split("|")
        if len(parts) != 5:
            continue
        cik_s, company, form, date_filed, filename = parts
        if form not in TARGET_FORMS:
            continue
        try:
            cik = int(cik_s)
        except ValueError:
            continue
        ticker = cik_to_ticker.get(cik)
        if not ticker:
            continue
        rows.append({
            "cik": cik, "ticker": ticker, "company": company.strip(),
            "form": form, "date": date_filed.strip(), "filename": filename.strip(),
        })
    return rows


def daily_index_rows(cik_to_ticker: dict[int, str]) -> list[dict]:
    rows, days_seen = [], 0
    day = datetime.now(timezone.utc).date()
    for _ in range(LOOKBACK_DAYS * 3):  # skip weekends/holidays
        if days_seen >= LOOKBACK_DAYS:
            break
        q = (day.month - 1) // 3 + 1
        ymd = day.strftime("%Y%m%d")
        url = f"https://www.sec.gov/Archives/edgar/daily-index/{day.year}/QTR{q}/master.{ymd}.idx"
        try:
            text = get_text(url)
            found = parse_master_idx(text, cik_to_ticker)
            rows.extend(found)
            days_seen += 1
            print(f"  {ymd}: {len(found)} relevant filings")
        except urllib.error.HTTPError:
            pass  # weekend/holiday/not-published
        except Exception as e:
            print(f"  ! {ymd}: {e}", file=sys.stderr)
        day -= timedelta(days=1)
    return rows


# ---------------- Form 4 parsing ----------------

def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _text(el):
    return (el.text or "").strip() if el is not None else ""


def filing_folder(cik: int, filename: str) -> str:
    # filename like edgar/data/320193/0000320193-26-000060.txt
    accn = filename.rsplit("/", 1)[-1].replace(".txt", "")
    accn_nodash = accn.replace("-", "")
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{accn_nodash}"


def fetch_form4(cik: int, filename: str) -> dict | None:
    base = filing_folder(cik, filename)
    try:
        index = get_json(f"{base}/index.json")
    except Exception:
        return None
    xml_name = None
    for it in index.get("directory", {}).get("item", []):
        if it["name"].lower().endswith(".xml"):
            xml_name = it["name"]  # may need content check
            text = get_text(f"{base}/{it['name']}")
            if "ownershipDocument" in text:
                return parse_form4(text)
    return None


def parse_form4(xml_text: str) -> dict | None:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    owner = ""
    title = ""
    is_ten_pct = False
    bought = sold = 0.0
    buy_val = sell_val = 0.0
    codes = set()

    for el in root.iter():
        tag = _local(el.tag)
        if tag == "rptownername":
            owner = _text(el) or owner
        elif tag == "officertitle":
            title = _text(el) or title
        elif tag == "istenpercentowner":
            if _text(el) in ("1", "true"):
                is_ten_pct = True
        elif tag == "nonderivativetransaction":
            shares = price = 0.0
            ad = code = ""
            for c in el.iter():
                ct = _local(c.tag)
                if ct == "transactionshares":
                    shares = _num(_first_value(c))
                elif ct == "transactionpricepershare":
                    price = _num(_first_value(c))
                elif ct == "transactionacquireddisposedcode":
                    ad = _first_value(c)
                elif ct == "transactioncode":
                    code = _text(c)
            if code:
                codes.add(code)
            if ad == "A":
                bought += shares; buy_val += shares * price
            elif ad == "D":
                sold += shares; sell_val += shares * price

    if bought == 0 and sold == 0:
        return None
    net = bought - sold
    action = "BUY" if net >= 0 else "SELL"
    value = buy_val if action == "BUY" else sell_val
    return {
        "owner": owner, "title": title, "ten_pct": is_ten_pct,
        "action": action, "shares": int(abs(net)),
        "value": int(value), "codes": sorted(codes),
        "open_market": bool(codes & OPEN_MARKET),
    }


def _first_value(el):
    """Form 4 wraps many fields as <field><value>X</value></field>."""
    for c in el.iter():
        if _local(c.tag) == "value":
            return (c.text or "").strip()
    return (el.text or "").strip()


def _num(s: str) -> float:
    try:
        return float(str(s).replace(",", ""))
    except (ValueError, TypeError):
        return 0.0


# ---------------- main ----------------

def main() -> int:
    print("Loading S&P watchlist CIKs...")
    cik_to_ticker = load_cik_map()
    print(f"  {len(cik_to_ticker)} companies resolved")

    print(f"Scanning daily index (last {LOOKBACK_DAYS} filing days)...")
    rows = daily_index_rows(cik_to_ticker)
    # newest first, de-dup by (cik, filename)
    seen = set()
    rows = [r for r in sorted(rows, key=lambda r: r["date"], reverse=True)
            if not (r["cik"], r["filename"]) in seen and not seen.add((r["cik"], r["filename"]))]

    activity: list[dict] = []
    form4_done = 0
    for r in rows:
        if r["form"] == "4":
            if form4_done >= MAX_FORM4_DETAIL:
                continue
            form4_done += 1
            detail = fetch_form4(r["cik"], r["filename"])
            if not detail:
                continue
            activity.append({
                "date": r["date"], "ticker": r["ticker"], "company": r["company"],
                "form": "4", "type": "insider",
                "owner": detail["owner"], "title": detail["title"],
                "action": detail["action"], "shares": detail["shares"],
                "value": detail["value"], "open_market": detail["open_market"],
                "ten_pct": detail["ten_pct"], "codes": detail["codes"],
            })
        else:  # 13D / 13G stake change
            activity.append({
                "date": r["date"], "ticker": r["ticker"], "company": r["company"],
                "form": r["form"], "type": "stake",
                "action": "STAKE", "owner": "",
                "note": "5%+ ownership filing",
            })

    # Order: newest date, then biggest dollar move.
    activity.sort(key=lambda a: (a["date"], a.get("value", 0)), reverse=True)
    activity = activity[:MAX_ACTIVITY]

    payload = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "lookback_days": LOOKBACK_DAYS,
        "activity": activity,
        "meta": {
            "total": len(activity),
            "insider": sum(1 for a in activity if a["type"] == "insider"),
            "stake": sum(1 for a in activity if a["type"] == "stake"),
            "companies": len(cik_to_ticker),
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2))
    print(f"Wrote {len(activity)} activity items to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
