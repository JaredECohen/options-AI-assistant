"""Analytics page: IV/HV, IV rank & percentile, greeks, liquidity."""

from __future__ import annotations

import pandas as pd
import streamlit as st
from _shared import load_bars, load_chain, sidebar_controls

from optionscoach.analytics import (
    add_liquidity_metrics,
    compute_greeks_df,
    hv_windows,
    implied_vol_df,
    iv_percentile,
    iv_rank,
)

st.set_page_config(page_title="Analytics — OptionsCoach.AI", page_icon="📊", layout="wide")
ticker, start, end = sidebar_controls()
st.title(f"📊 Analytics — {ticker}")

bars = load_bars(ticker, start, end)
if bars.empty:
    st.warning("No price data for this selection.")
    st.stop()

closes = bars["close"]
spot = float(closes.iloc[-1])
chain = load_chain(ticker, end)

# Historical volatility ----------------------------------------------------
st.subheader("Historical volatility")
hv = hv_windows(closes)
latest_hv = hv.dropna().iloc[-1] if not hv.dropna().empty else hv.iloc[-1]
st.dataframe(latest_hv.to_frame("annualized HV").style.format("{:.1%}"))

# IV rank / percentile from the ATM IV history (approx via synthetic chain IV) --
st.subheader("Implied volatility rank & percentile")
atm = chain.iloc[(chain["strike"] - spot).abs().argsort()].head(20)
atm_iv = float(atm["iv"].mean())
# Build a short synthetic IV history around the realized vol for a live demo number.
iv_history = pd.Series((hv["hv_20"].dropna().tail(252) * 1.1).tolist() + [atm_iv])
col1, col2, col3 = st.columns(3)
col1.metric("ATM IV", f"{atm_iv:.1%}")
col2.metric("IV rank (252d)", f"{iv_rank(iv_history):.0f}")
col3.metric("IV percentile (252d)", f"{iv_percentile(iv_history):.0f}")

# Greeks + liquidity on the near-the-money chain ---------------------------
st.subheader("Greeks & liquidity (near the money)")
near = chain[(chain["strike"] > spot * 0.9) & (chain["strike"] < spot * 1.1)].copy()
near = compute_greeks_df(
    near.drop(columns=["delta", "gamma", "theta", "vega", "rho"]), spot, asof=end
)
near = add_liquidity_metrics(near)
solved = implied_vol_df(near, spot, asof=end, price_col="mark")
cols = [
    "expiry",
    "strike",
    "type",
    "mid",
    "spread",
    "rel_spread",
    "volume",
    "oi",
    "iv",
    "iv_solved",
    "delta",
    "gamma",
    "theta",
    "vega",
]
st.dataframe(solved[cols].sort_values(["expiry", "strike"]).reset_index(drop=True))

st.line_chart(closes.rename("close"))
