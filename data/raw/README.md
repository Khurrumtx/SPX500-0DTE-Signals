# Historical data drop zone

Place **24 months** of daily (or intraday) CSVs here. Filenames match
[`scripts/agent/config.yaml`](../../scripts/agent/config.yaml):

| File | Instrument | Required columns |
| --- | --- | --- |
| `es.csv` | E-mini S&P 500 futures | `timestamp`, `open`, `high`, `low`, `close` (+ optional `volume`) |
| `spx.csv` | S&P 500 index | same |
| `vix.csv` | VIX | same |
| `cl.csv` | Crude oil futures | same |
| `spx_0dte.csv` *(optional)* | 0DTE option aggregates | `timestamp` + any of `atm_iv`, `call_volume`, `put_volume`, `call_oi`, `put_oi`, `gamma_flip`, `put_call_ratio` |

Column names are case-insensitive. Accepted aliases: `date`/`datetime` → timestamp,
`settle`/`last` → close, `vol` → volume, `pcr` → put_call_ratio, etc.

## Quick start without your files yet

```bash
pip install -r scripts/requirements-agent.txt
python3 scripts/run_agent.py sample    # writes demo CSVs here
python3 scripts/run_agent.py train
python3 scripts/run_agent.py signal
```

Replace the demo files with your real history (same schema), then:

```bash
python3 scripts/run_agent.py retrain --force
```

## Order-flow (subscription later)

Dumps from your options order-flow vendor go in [`../orderflow/`](../orderflow/).
See that folder's README. The agent auto-merges recognized columns on retrain.
