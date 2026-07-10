"""Streamlit dashboard for the NVDA next-day direction predictor.

Run from the nvda-predictor directory:
    python cli.py dashboard        # or: streamlit run app/dashboard.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from nvda_predictor.backtest import backtest, classification_metrics
from nvda_predictor.config import Config
from nvda_predictor.data import load_prices
from nvda_predictor.persist import artifacts_exist, load_meta, load_oos

# dataviz palette (validated): series blue, recessive benchmark, status colors
BLUE = "#2a78d6"
GRAY = "#8a897d"
GOOD = "#008300"
BAD = "#e34948"

st.set_page_config(page_title="NVDA Direction Predictor", page_icon="📈", layout="wide")

cfg = Config()

st.title("NVDA next-day direction predictor")
st.caption(
    "Stacked-LSTM classifier replicated from the top-starred GitHub stock-prediction "
    "repos, with walk-forward validation and classical baselines. "
    "**Educational only — not financial advice.**"
)

if not artifacts_exist(cfg):
    st.warning("No trained model found. Run `python cli.py evaluate` first.")
    st.stop()

meta = load_meta(cfg)
refresh = st.sidebar.toggle(
    "Refresh prices from yfinance",
    value=False,
    help="Off = use the bundled CSV snapshot (works offline).",
)
threshold = st.sidebar.slider("Long threshold (P(up) ≥ …)", 0.40, 0.70, cfg.prob_threshold, 0.01)
cost_bps = st.sidebar.slider("Cost per trade (bps)", 0.0, 25.0, cfg.cost_bps, 0.5)

# ---------------------------------------------------------------- prediction
col_pred, col_price = st.columns([1, 2])

with col_pred:
    st.subheader("Latest prediction")
    try:
        from nvda_predictor.predict import predict_latest

        pred = predict_latest(cfg, refresh=refresh)
        color = GOOD if pred["direction"] == "UP" else BAD
        arrow = "▲" if pred["direction"] == "UP" else "▼"
        st.markdown(
            f"<div style='font-size:3rem;font-weight:700;color:{color}'>"
            f"{arrow} {pred['direction']}</div>",
            unsafe_allow_html=True,
        )
        st.metric("P(next close higher)", f"{pred['p_up']:.1%}")
        st.write(
            f"As of **{pred['as_of']}** (close ${pred['close']:,.2f}), "
            f"model trained through {pred['model_trained_through']}."
        )
    except Exception as exc:
        st.error(f"Prediction failed: {exc}")

with col_price:
    st.subheader("NVDA close")
    prices = load_prices(cfg, refresh=False)
    fig = go.Figure(
        go.Scatter(x=prices.index, y=prices["close"], mode="lines",
                   line=dict(color=BLUE, width=2), name="NVDA close",
                   hovertemplate="%{x|%Y-%m-%d}<br>$%{y:,.2f}<extra></extra>")
    )
    fig.update_layout(margin=dict(l=0, r=0, t=10, b=0), height=320,
                      yaxis_title="Close ($)", showlegend=False)
    st.plotly_chart(fig, use_container_width=True)

# --------------------------------------------------------------- evaluation
st.divider()
st.subheader("Walk-forward out-of-sample results")
st.caption(
    "Every point below was predicted by a model that had never seen that date "
    "or anything after it."
)

oos = load_oos(cfg)
model_cols = [c.removeprefix("proba_") for c in oos.columns if c.startswith("proba_")]
model = st.selectbox("Model", model_cols, index=model_cols.index("lstm") if "lstm" in model_cols else 0)

proba = oos[f"proba_{model}"].to_numpy()
dates = pd.DatetimeIndex(oos["date"])
bt = backtest(dates, proba, oos["next_ret"].to_numpy(), threshold=threshold, cost_bps=cost_bps)
cls = classification_metrics(oos["y"].to_numpy(), proba, threshold)
curve = bt.pop("equity_curve")

m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Accuracy", f"{cls['accuracy']:.1%}", help=f"Base rate (always-up): {cls['base_rate_up']:.1%}")
m2.metric("ROC AUC", f"{cls['roc_auc']:.3f}")
m3.metric("Strategy return", f"{bt['strategy']['total_return']:+.1%}")
m4.metric("Buy & hold", f"{bt['buy_hold']['total_return']:+.1%}")
m5.metric("Strategy Sharpe", f"{bt['strategy']['sharpe']:.2f}",
          help=f"Buy & hold Sharpe: {bt['buy_hold']['sharpe']:.2f}")

fig = go.Figure()
fig.add_trace(go.Scatter(x=curve.index, y=curve["buy_hold"], mode="lines", name="Buy & hold",
                         line=dict(color=GRAY, width=2),
                         hovertemplate="%{x|%Y-%m-%d}<br>%{y:.2f}x<extra>Buy & hold</extra>"))
fig.add_trace(go.Scatter(x=curve.index, y=curve["strategy"], mode="lines", name=f"{model} strategy",
                         line=dict(color=BLUE, width=2),
                         hovertemplate="%{x|%Y-%m-%d}<br>%{y:.2f}x<extra>Strategy</extra>"))
fig.update_layout(margin=dict(l=0, r=0, t=10, b=0), height=380,
                  yaxis_title="Growth of $1", legend=dict(orientation="h", y=1.08))
st.plotly_chart(fig, use_container_width=True)

with st.expander("Model comparison (fixed 0.50 threshold, walk-forward)"):
    rows = []
    for name in model_cols:
        p = oos[f"proba_{name}"].to_numpy()
        c = classification_metrics(oos["y"].to_numpy(), p, 0.5)
        b = backtest(dates, p, oos["next_ret"].to_numpy(), threshold=0.5, cost_bps=cost_bps)
        rows.append({
            "model": name,
            "accuracy": c["accuracy"],
            "roc_auc": c["roc_auc"],
            "strategy_return": b["strategy"]["total_return"],
            "sharpe": b["strategy"]["sharpe"],
            "max_drawdown": b["strategy"]["max_drawdown"],
            "exposure": b["exposure"],
        })
    bh = bt["buy_hold"] if "buy_hold" in bt else None
    st.dataframe(pd.DataFrame(rows).set_index("model"), use_container_width=True)
    st.caption(
        f"Buy & hold over the same period: {bt['buy_hold']['total_return']:+.1%} "
        f"(Sharpe {bt['buy_hold']['sharpe']:.2f}, max drawdown {bt['buy_hold']['max_drawdown']:.1%})."
    )

with st.expander("Prediction probabilities over time"):
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=dates, y=proba, mode="lines", name=f"P(up) — {model}",
                             line=dict(color=BLUE, width=1.5),
                             hovertemplate="%{x|%Y-%m-%d}<br>P(up)=%{y:.2f}<extra></extra>"))
    fig.add_hline(y=threshold, line_dash="dash", line_color=GRAY,
                  annotation_text=f"threshold {threshold:.2f}")
    fig.update_layout(margin=dict(l=0, r=0, t=10, b=0), height=300,
                      yaxis_title="P(up)", yaxis_range=[0, 1], showlegend=False)
    st.plotly_chart(fig, use_container_width=True)

st.divider()
st.caption(
    f"Model: 3× LSTM({meta.get('window', 50)}-day window) trained on "
    f"{meta.get('n_samples', '?')} samples {meta.get('train_start', '?')} → "
    f"{meta.get('train_end', '?')}. Data: yfinance (bundled snapshot fallback)."
)
