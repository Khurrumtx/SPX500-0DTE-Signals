# NVDA Next-Day Direction Predictor

> **EDUCATIONAL ONLY — NOT FINANCIAL ADVICE.** No model reliably predicts
> stock prices. This project replicates the most popular public methodology
> and reports honest out-of-sample results so you can judge it yourself.

A full application — Python library, CLI, REST API, and web dashboard — that
predicts the probability that NVIDIA (NVDA) closes **higher tomorrow**.

## Where the model comes from

The model replicates the two highest-starred GitHub repositories dedicated to
stock price prediction, combined ("hybrid best-of"):

| Source | Stars | What we took |
| --- | --- | --- |
| [huseinzol05/Stock-Prediction-Models](https://github.com/huseinzol05/Stock-Prediction-Models) | 9.4k | LSTM methodology, Adam optimizer, holdout evaluation concept |
| [jaungiers/LSTM-Neural-Network-for-Time-Series-Prediction](https://github.com/jaungiers/LSTM-Neural-Network-for-Time-Series-Prediction) | 5.2k | Exact architecture: 3× LSTM(100) + Dropout(0.2), 50-day window |

**Deliberate improvements over the sources** (their two most-criticized flaws):

1. **Classification, not regression.** The source repos regress tomorrow's
   *price* with MSE, which produces charts that look impressive but mostly
   lag the price by one day. We predict *direction* (up/down probability),
   which is honestly measurable: accuracy vs. base rate, ROC AUC, and a
   costed backtest.
2. **Walk-forward validation.** Every reported number is out-of-sample: the
   model is retrained per fold on an expanding window and tested on the next
   ~year it has never seen. Baselines (logistic regression, random forest,
   gradient boosting) run on the same folds — if the LSTM can't beat them,
   you'll see it.

Architecture: `LSTM(100, seq) → Dropout(0.2) → LSTM(100, seq) → LSTM(100) →
Dropout(0.2) → Dense(1, sigmoid)`, binary cross-entropy, Adam(1e-3), early
stopping. Inputs: 50-day windows of 15 technical-indicator features (returns,
SMA/EMA/MACD, RSI, Bollinger %B, ATR, ROC, volume z-score…), standardized on
each training fold only.

## Quick start

```bash
cd nvda-predictor
pip install -r requirements.txt

python cli.py fetch       # download NVDA daily history (yfinance, no key)
python cli.py evaluate    # walk-forward eval + train final model (~15-30 min CPU)
python cli.py predict     # tomorrow's direction as JSON
python cli.py dashboard   # Streamlit web dashboard
python cli.py serve       # FastAPI service on :8080
```

`evaluate` writes everything the API/dashboard need into `artifacts/`:
the trained model (`lstm.keras`), the feature scaler, metadata with all
metrics (`meta.json`), and per-day out-of-sample predictions
(`oos_predictions.csv`).

### REST API

| Endpoint | Returns |
| --- | --- |
| `GET /health` | service + model status |
| `GET /prediction` | latest next-day direction, `?refresh=false` for offline |
| `GET /model` | architecture, training range, walk-forward metrics |
| `GET /history?limit=250` | recent out-of-sample predictions |

Interactive docs at `/docs` (Swagger UI).

### Dashboard

`python cli.py dashboard` — latest prediction, price chart, walk-forward
equity curves vs. buy & hold, model comparison table, and adjustable
threshold/costs.

## Data

- **Primary:** [yfinance](https://github.com/ranaroussi/yfinance) daily OHLCV
  (split/dividend-adjusted), no API key.
- **Fallback:** `data/NVDA.csv` — a committed snapshot so everything runs
  offline (CI, sandboxes). `python cli.py fetch` refreshes it.

## CI

`.github/workflows/nvda-train.yml` re-fetches data, re-runs the walk-forward
evaluation and commits refreshed artifacts weekly (or on demand via
*Actions → NVDA predictor → Run workflow*) — the same pattern the rest of
this repo uses for SEC data, because market-data hosts aren't reachable from
the web-session sandbox.

## Tests

```bash
python -m pytest tests/ -q
```

Covers: no feature lookahead (features at day *t* are unchanged by future
data), label alignment, walk-forward fold chronology/non-overlap, backtest
accounting (oracle beats buy-and-hold; costs reduce returns; always-long
equals buy-and-hold), and API endpoints.

## Honest expectations

Daily direction of a single stock is close to a coin flip. NVDA's up-day base
rate is ~53%, so an accuracy of 53% means *nothing was learned* — compare
against the base rate and AUC, not 50%. Even the celebrated repos this
replicates do not demonstrate durable out-of-sample edge; treat any strategy
outperformance in a single backtest as fragile until it survives costs,
regime changes, and repeated retraining.
