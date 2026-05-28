"""Screener page: run the three starter screens over the chain's contracts."""

from __future__ import annotations

import streamlit as st
from _shared import load_bars, load_chain, sidebar_controls

from optionscoach.analytics import add_liquidity_metrics, hv_windows
from optionscoach.screener import SCREENS, run_screen

st.set_page_config(page_title="Screener — OptionsCoach.AI", page_icon="🔎", layout="wide")
ticker, start, end = sidebar_controls()
st.title(f"🔎 Screener — {ticker}")

bars = load_bars(ticker, start, end)
if bars.empty:
    st.warning("No price data for this selection.")
    st.stop()

closes = bars["close"]
spot = float(closes.iloc[-1])
hv20 = float(hv_windows(closes)["hv_20"].dropna().iloc[-1])

# Build a per-contract screening frame from the chain. iv_rank is approximated
# per-contract from its IV relative to the chain's IV range (demo-friendly).
chain = add_liquidity_metrics(load_chain(ticker, end)).copy()
chain["hv"] = hv20
iv_min, iv_max = chain["iv"].min(), chain["iv"].max()
chain["iv_rank"] = 100.0 * (chain["iv"] - iv_min) / max(iv_max - iv_min, 1e-9)

screen_name = st.selectbox("Screen", sorted(SCREENS))
st.caption(SCREENS[screen_name].__doc__)

result = run_screen(screen_name, chain)
st.write(f"**{len(result)}** contracts matched out of {len(chain)}.")
display_cols = ["expiry", "strike", "type", "iv", "hv", "iv_rank", "volume", "rel_spread"]
st.dataframe(result[display_cols].sort_values(["expiry", "strike"]).reset_index(drop=True))
