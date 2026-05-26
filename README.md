# SPX500-0DTE-Signals

A mobile-first **PWA** dashboard for SPX500 0DTE signals that runs on iPhone
(installable to the Home Screen).

## Layout

- `dashboard/` — the PWA (HTML/CSS/JS, service worker, manifest, icons)
- `dashboard/signals/signal.json` — signal data the dashboard polls
- `signals/signal.json` — source-of-truth signal file (mirror)
- `scripts/` — signal generators (TBD)

## Run locally

```bash
cd dashboard
python3 -m http.server 8000
# open http://localhost:8000
```

## Deploy to GitHub Pages

1. Push to `main`.
2. In the repo on GitHub: **Settings → Pages → Source: Deploy from a branch**.
3. Branch: `main`, Folder: `/dashboard`. Save.
4. After a minute the site is live at `https://<user>.github.io/SPX500-0DTE-Signals/`.

## Install on iPhone

1. Open the URL above in **Safari** (not Chrome — only Safari can install PWAs on iOS).
2. Tap the Share icon → **Add to Home Screen** → Add.
3. Launch the app from your Home Screen.
4. Open Settings (gear icon) → toggle **Signal notifications** on and allow when prompted.

Notification support requires **iOS 16.4+** and only works once the PWA has
been installed to the Home Screen.

## Updating signals

The dashboard fetches `dashboard/signals/signal.json` every 15s. Update that
file (e.g. from a scheduled GitHub Action that runs your signal script) and
the app picks it up on the next poll. Expected schema:

```json
{ "signal": "CALL" | "PUT" | "WAIT" | "NONE", "timestamp": "2026-05-23T14:30:00Z", "confidence": 0-100 }
```

When `signal` or `timestamp` changes to a CALL/PUT, the app fires a local
notification (if notifications are enabled).

## Institutional ownership (SEC 13F)

`scripts/collect.py` builds a per-stock ownership summary for the top-100 S&P
names. It downloads SEC's **bulk quarterly 13F data sets** (every filer's
holdings) for the two most recent quarters, diffs each manager's position per
stock, and writes `dashboard/events.json`:

- `stocks[]` — per stock: how many institutions are buying vs selling, net
  share/value change, and the top buyers and top sellers.
- `events[]` — the largest individual buy/sell moves across all filers.
- `fred[]` — optional macro context (needs `FRED_API_KEY`).

Watchlists live in `scripts/watchlists/`:
- `sp100.json` — the stock universe (top-100 S&P tickers).
- `institutions.json` — institutions/banks to highlight (★) in the feed.

This runs in **GitHub Actions** (`.github/workflows/collect.yml`), because SEC's
servers are not reachable from the web-session sandbox. Trigger it manually via
**Actions → Collect institutional flows → Run workflow**, or wait for the cron.
Add a `FRED_API_KEY` repo secret to populate the macro grid.

The committed `dashboard/events.json` ships with illustrative sample data so the
UI renders before the first CI run replaces it.

## Daily activity (Form 4 + 13D/13G)

13F is quarterly, so it can't show daily trading. For day-to-day buying/selling,
`scripts/collect_daily.py` scans SEC's **daily filing index** for the last few
days and keeps filings about the top-100 companies:

- **Form 4** — insider transactions (officers, directors, 10%+ owners); the
  collector parses each one for open-market buys (P) / sells (S), share counts
  and dollar value.
- **SC 13D / 13G (+ /A)** — institutions crossing or changing a 5%+ stake.

Output `dashboard/daily.json` powers the app's "Daily Activity" card (filter by
Buys / Sells / 5%+ stakes). It runs **every weekday** after market close via
`.github/workflows/collect_daily.yml`.

Note: there is no public feed of *every* institution's trades intraday — 13F
(quarterly) is the only comprehensive institutional source. Form 4 + 13D/13G are
the timely, daily-updating signals of who is actually buying and selling.

## 0DTE options signal

> **EDUCATIONAL ONLY — NOT FINANCIAL ADVICE.** 0DTE options are extremely high
> risk and can expire worthless within hours. The signal is a transparent,
> rule-based suggestion, not a recommendation.

`scripts/signal.py` produces a **defined-risk, directional** 0DTE idea:
- Pulls recent daily candles for a liquid proxy (default `SPY`, ×10 ≈ SPX) via
  one Alpha Vantage call, then computes SMA(5/20), RSI(14) and realized vol.
- Scores a bias + conviction and picks a structure (capped risk only):
  **single long call/put** (aggressive, when conviction is high and vol low) or
  a **bull-call / bear-put debit spread** (balanced, otherwise). Neutral/low
  conviction → **no trade**.
- Prices legs with a built-in Black-Scholes estimate to show est. debit, capped
  **max risk** (sized to `MAX_RISK`, default $1,000), max reward and breakeven.
- Writes `signals/signal.json`, rendered in the app's top card.

**Pricing:** with a **premium** Alpha Vantage key the engine pulls the real 0DTE
option chain (`REALTIME_OPTIONS`) and shows **exact** strikes/debit/risk on SPY
(1/10 of SPX, same exposure) — the card shows a green **LIVE** badge. With a free
key it falls back to Black-Scholes **estimates** on SPX (grey **ESTIMATE** badge).
Set `USE_LIVE_CHAIN=0` to force estimate mode.

Runs a few times per US session via `.github/workflows/signal.yml` (needs
`ALPHAVANTAGE_API_KEY`). GitHub's scheduler isn't real-time, so this suits a few
signals a day, not second-by-second trading.

## Data sources & keys

Two ways to get the daily insider feed:

| Source | Script | Key needed | Notes |
| --- | --- | --- | --- |
| **SEC EDGAR (direct)** | `collect_daily.py` | none (just a User-Agent email) | Free, unlimited, scrapes the daily index + Form 4 XML. Default. |
| **Alpha Vantage** | `collect_av.py` | `ALPHAVANTAGE_API_KEY` | Parsed SEC insider JSON (no XML). Free tier = **25 req/day, 1/sec**, so it only covers ~20 tickers/day; a premium key is needed for all 100. |

The committed `dashboard/daily.json` currently holds **real AAPL insider data**
pulled from Alpha Vantage as a working sample.

**Keys live in GitHub Actions secrets, never in the repo or chat.** Add them at
**Settings → Secrets and variables → Actions**:
- `FRED_API_KEY` — macro grid (optional).
- `ALPHAVANTAGE_API_KEY` — only if you use the Alpha Vantage collector.

SEC EDGAR needs **no key** — its APIs are free and open; the only requirement is
a descriptive `User-Agent` with a contact email, already set in the workflows.
