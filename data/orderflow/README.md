# Quant Data option-flow captures

Near-live options order flow from [quantdata.us](https://quantdata.us), polled via REST.

Quant Data does **not** ship a WebSocket stream yet (docs: “Streaming is on the way”).
`scripts/capture_option_flow.py` polls:

- `POST /v1/options/tool/order-flow/consolidated` — blocks / sweeps / splits
- `POST /v1/options/tool/order-flow/unconsolidated` — trade-by-trade tape

## Local live capture

```bash
export QUANTDATA_API_KEY=qd_…
python3 scripts/capture_option_flow.py stream --tickers SPX,SPY,SPXW --interval 15
```

Files written here:

| File | Purpose |
| --- | --- |
| `flow_YYYY-MM-DD.jsonl` | Append-only raw prints (gitignored) |
| `.capture_state.json` | Dedupe cursor of seen trade ids (gitignored) |

The dashboard snapshot lives at `dashboard/option_flow.json` (committed by CI).

## One-shot

```bash
python3 scripts/capture_option_flow.py once --tickers SPX --dte-max 1 --min-premium 25000
```

## MCP (agents)

Point Cursor at `https://api.quantdata.us/mcp` with the same Bearer key
(see `.cursor/mcp.json.example`). Tools: `qd_get_order_flow_consolidated`,
`qd_get_order_flow_unconsolidated`.
