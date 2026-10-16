#!/usr/bin/env python3
"""Capture near-live options order flow from quantdata.us.

Quant Data's REST API does not yet expose a WebSocket stream ("Streaming is on
the way"). This script polls Order Flow Consolidated (and optionally
Unconsolidated) on a cadence, dedupes by trade id, appends JSONL, and writes a
dashboard snapshot.

Examples:
  # One-shot snapshot for SPX / SPY / SPXW (needs QUANTDATA_API_KEY)
  python3 scripts/capture_option_flow.py once --tickers SPX,SPY,SPXW

  # Local "live" loop every 15s during market hours
  python3 scripts/capture_option_flow.py stream --tickers SPX,SPY --interval 15

  # Unusual / high-premium sweeps only
  python3 scripts/capture_option_flow.py once --tickers SPX --min-premium 100000 --unusual
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Allow `python3 scripts/capture_option_flow.py` without installing a package.
_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from quantdata_client import QuantDataClient, QuantDataError  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_DIR = ROOT / "data" / "orderflow"
DEFAULT_DASHBOARD = ROOT / "dashboard" / "option_flow.json"
DEFAULT_STATE = DEFAULT_OUT_DIR / ".capture_state.json"

# Fields kept on each stored row (projection + local extras).
ROW_FIELDS = (
    "id",
    "ticker",
    "osi",
    "contractType",
    "expirationDate",
    "dte",
    "strikePrice",
    "premium",
    "size",
    "volume",
    "optionPrice",
    "stockPrice",
    "tradeSideCode",
    "tradeType",
    "tradeConsolidationType",
    "sentimentType",
    "isGoldenSweep",
    "isUnusual",
    "isOpeningPosition",
    "impliedVolatility",
    "tradeTime",
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")


def session_date_et(now: datetime | None = None) -> str:
    """Rough US equity session calendar date in America/New_York."""
    now = now or utc_now()
    # Fixed offset is fine for sessionDate labeling; DST handled via zoneinfo when available.
    try:
        from zoneinfo import ZoneInfo

        return now.astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    except Exception:
        return now.strftime("%Y-%m-%d")


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"seen_ids": [], "last_trade_time_ms": 0, "updated_at": None}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return {"seen_ids": [], "last_trade_time_ms": 0, "updated_at": None}


def save_state(path: Path, state: dict[str, Any], *, keep_ids: int = 5000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    seen = list(dict.fromkeys(state.get("seen_ids") or []))[-keep_ids:]
    out = {
        "seen_ids": seen,
        "last_trade_time_ms": int(state.get("last_trade_time_ms") or 0),
        "updated_at": iso_now(),
        "polls": int(state.get("polls") or 0),
        "captured_total": int(state.get("captured_total") or 0),
    }
    path.write_text(json.dumps(out, indent=2) + "\n")


def project_row(row: dict[str, Any], *, source: str) -> dict[str, Any]:
    out = {k: row.get(k) for k in ROW_FIELDS if k in row}
    out["source"] = source
    out["capturedAt"] = iso_now()
    # Normalize tradeTime to ISO for humans while keeping epoch ms.
    ts = row.get("tradeTime")
    if isinstance(ts, (int, float)):
        out["tradeTimeMs"] = int(ts)
        out["tradeTimeIso"] = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    return out


def build_filter(
    tickers: list[str],
    *,
    min_premium: float | None,
    unusual: bool,
    dte_max: float | None,
) -> dict[str, Any]:
    filt: dict[str, Any] = {}
    if tickers:
        filt["tickers"] = tickers
    if unusual:
        filt["isUnusual"] = True
    if min_premium is not None:
        filt["premiumRange"] = {"min": float(min_premium)}
    if dte_max is not None:
        # Prefer filterExpression for comparisons; convenience filter has no dteMax.
        pass
    return filt


def build_body(
    args: argparse.Namespace,
    *,
    search_after: list[Any] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "size": int(args.size),
        "sort": {"field": "tradeTime", "direction": "DESCENDING"},
    }
    if args.session_date:
        body["sessionDate"] = args.session_date
    filt = build_filter(
        args.tickers,
        min_premium=args.min_premium,
        unusual=args.unusual,
        dte_max=args.dte_max,
    )
    if filt:
        body["filter"] = filt
    if args.dte_max is not None:
        body["filterExpression"] = {
            "conjunction": "AND",
            "filters": [
                {"field": "dte", "operation": "<=", "value": float(args.dte_max)},
            ],
        }
    if search_after:
        body["searchAfter"] = search_after
    if args.mode == "consolidated" or args.mode == "both":
        # Applied only on consolidated calls by the capturer.
        pass
    return body


def fetch_batch(
    client: QuantDataClient,
    args: argparse.Namespace,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    base = build_body(args)

    if args.mode in ("consolidated", "both"):
        body = dict(base)
        if args.include_comprising:
            body["includeComprisingTrades"] = True
        for _, resp in client.iter_pages(
            client.order_flow_consolidated, body, max_pages=args.max_pages
        ):
            for row in resp.get("data") or []:
                rows.append(project_row(row, source="consolidated"))

    if args.mode in ("unconsolidated", "both"):
        body = dict(base)
        # Unconsolidated allows up to 1000; keep request size as configured (capped).
        body["size"] = min(int(args.size), 1000)
        for _, resp in client.iter_pages(
            client.order_flow_unconsolidated, body, max_pages=args.max_pages
        ):
            for row in resp.get("data") or []:
                rows.append(project_row(row, source="unconsolidated"))

    # Newest first already; stable-sort by tradeTimeMs desc then id.
    rows.sort(key=lambda r: (r.get("tradeTimeMs") or 0, str(r.get("id") or "")), reverse=True)
    return rows


def dedupe_new(rows: list[dict[str, Any]], state: dict[str, Any]) -> list[dict[str, Any]]:
    seen = set(state.get("seen_ids") or [])
    fresh: list[dict[str, Any]] = []
    for row in rows:
        rid = str(row.get("id") or "")
        if not rid or rid in seen:
            continue
        seen.add(rid)
        fresh.append(row)
    # Keep chronological for append (oldest → newest among this batch).
    fresh.sort(key=lambda r: (r.get("tradeTimeMs") or 0, str(r.get("id") or "")))
    state["seen_ids"] = list(seen)
    if fresh:
        state["last_trade_time_ms"] = max(
            int(state.get("last_trade_time_ms") or 0),
            max(int(r.get("tradeTimeMs") or 0) for r in fresh),
        )
    return fresh


def append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")


def summarize(rows: list[dict[str, Any]], *, lookback: int = 200) -> dict[str, Any]:
    window = rows[-lookback:] if len(rows) > lookback else list(rows)
    call_prem = sum(
        float(r.get("premium") or 0)
        for r in window
        if str(r.get("contractType") or "").upper() == "CALL"
    )
    put_prem = sum(
        float(r.get("premium") or 0)
        for r in window
        if str(r.get("contractType") or "").upper() == "PUT"
    )
    unusual = sum(1 for r in window if r.get("isUnusual"))
    sweeps = sum(
        1
        for r in window
        if str(r.get("tradeConsolidationType") or "").upper() == "SWEEP"
        or r.get("isGoldenSweep")
    )
    bias = "NEUTRAL"
    if call_prem > put_prem * 1.15:
        bias = "CALL"
    elif put_prem > call_prem * 1.15:
        bias = "PUT"
    return {
        "updatedAt": iso_now(),
        "sessionDate": session_date_et(),
        "rowCount": len(window),
        "callPremium": round(call_prem, 2),
        "putPremium": round(put_prem, 2),
        "netPremium": round(call_prem - put_prem, 2),
        "unusualCount": unusual,
        "sweepCount": sweeps,
        "flowBias": bias,
        "recent": list(reversed(window[-50:])),  # newest first for UI
    }


def write_dashboard(path: Path, summary: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2) + "\n")


def jsonl_tail(path: Path, limit: int = 500) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    out: list[dict[str, Any]] = []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def run_once(args: argparse.Namespace) -> int:
    client = QuantDataClient(api_key=args.api_key)
    state = load_state(args.state)
    try:
        rows = fetch_batch(client, args)
    except QuantDataError as exc:
        print(f"error: {exc}", file=sys.stderr)
        if isinstance(exc.payload, dict) and exc.payload.get("agreementUrl"):
            print(f"OPRA agreement: {exc.payload['agreementUrl']}", file=sys.stderr)
        return 1

    fresh = dedupe_new(rows, state) if not args.no_dedupe else rows
    day = args.session_date or session_date_et()
    jsonl_path = args.out_dir / f"flow_{day}.jsonl"
    append_jsonl(jsonl_path, fresh)

    state["polls"] = int(state.get("polls") or 0) + 1
    state["captured_total"] = int(state.get("captured_total") or 0) + len(fresh)
    save_state(args.state, state)

    # Prefer on-disk history when streaming locally; fall back to this poll's
    # fetched rows so ephemeral CI runners still produce a useful snapshot.
    history = jsonl_tail(jsonl_path, limit=500)
    if not history:
        history = list(reversed(rows))  # oldest → newest for summarize window
    summary = summarize(history)
    summary["lastPoll"] = {
        "fetched": len(rows),
        "new": len(fresh),
        "mode": args.mode,
        "tickers": args.tickers,
    }
    write_dashboard(args.dashboard, summary)

    print(
        json.dumps(
            {
                "ok": True,
                "fetched": len(rows),
                "new": len(fresh),
                "jsonl": str(jsonl_path),
                "dashboard": str(args.dashboard),
                "flowBias": summary["flowBias"],
                "netPremium": summary["netPremium"],
            },
            indent=2,
        )
    )
    return 0


def run_stream(args: argparse.Namespace) -> int:
    print(
        f"streaming option flow every {args.interval}s "
        f"(mode={args.mode}, tickers={args.tickers or 'ALL'}) — Ctrl+C to stop",
        flush=True,
    )
    while True:
        code = run_once(args)
        if code != 0 and not args.keep_going:
            return code
        time.sleep(max(1.0, float(args.interval)))


def parse_tickers(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [t.strip().upper() for t in raw.split(",") if t.strip()]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    def add_common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--api-key", default=None, help="Override QUANTDATA_API_KEY")
        sp.add_argument(
            "--tickers",
            default=os.environ.get("QUANTDATA_TICKERS", "SPX,SPY,SPXW"),
            help="Comma-separated tickers (default: SPX,SPY,SPXW)",
        )
        sp.add_argument(
            "--mode",
            choices=("consolidated", "unconsolidated", "both"),
            default="consolidated",
            help="Which order-flow endpoint(s) to poll (default: consolidated)",
        )
        sp.add_argument("--session-date", default=None, help="YYYY-MM-DD; omit for latest session")
        sp.add_argument("--size", type=int, default=100, help="Page size (1-100; unconsolidated up to 1000)")
        sp.add_argument("--max-pages", type=int, default=3, help="Max pages per poll")
        sp.add_argument("--min-premium", type=float, default=None, help="Minimum premium filter")
        sp.add_argument("--dte-max", type=float, default=1.0, help="Max DTE (default 1 for 0DTE focus; set -1 to disable)")
        sp.add_argument("--unusual", action="store_true", help="Only unusual prints")
        sp.add_argument("--include-comprising", action="store_true", help="Include legs on consolidated")
        sp.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
        sp.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
        sp.add_argument("--state", type=Path, default=DEFAULT_STATE)
        sp.add_argument("--no-dedupe", action="store_true", help="Append every fetched row (testing)")

    once = sub.add_parser("once", help="Single poll + write files")
    add_common(once)

    stream = sub.add_parser("stream", help="Poll in a loop (local near-live capture)")
    add_common(stream)
    stream.add_argument("--interval", type=float, default=15.0, help="Seconds between polls")
    stream.add_argument(
        "--keep-going",
        action="store_true",
        help="Continue streaming after a failed poll",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.tickers = parse_tickers(args.tickers)
    if args.dte_max is not None and float(args.dte_max) < 0:
        args.dte_max = None
    args.out_dir = Path(args.out_dir)
    args.dashboard = Path(args.dashboard)
    args.state = Path(args.state)

    if args.command == "once":
        return run_once(args)
    if args.command == "stream":
        try:
            return run_stream(args)
        except KeyboardInterrupt:
            print("\nstopped", flush=True)
            return 0
    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
