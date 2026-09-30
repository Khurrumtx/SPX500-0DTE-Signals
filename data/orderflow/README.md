# Order-flow ingest (future subscription)

When you buy an options order-flow feed, drop each dump here (CSV or Parquet).

Recognized columns (any subset is fine):

- `timestamp` (required)
- `bid_size`, `ask_size`
- `delta` / `volume_delta`
- `cumulative_delta` / `cvd`
- `large_print_count`
- `buy_aggression`, `sell_aggression`

```bash
python3 scripts/run_agent.py ingest-orderflow /path/to/vendor_dump.csv
python3 scripts/run_agent.py retrain          # picks up new files automatically
```

Until files exist, order-flow features are simply absent — the ES/SPX/VIX/CL
model still trains and signals.
