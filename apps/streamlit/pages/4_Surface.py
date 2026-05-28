"""Surface page: fit and render the implied-volatility surface in 3D."""

from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st
from _shared import load_chain, sidebar_controls

from optionscoach.surface import fit_surface, plot_surface_3d, skew_curves, term_structure

st.set_page_config(page_title="IV Surface — OptionsCoach.AI", page_icon="🌐", layout="wide")
ticker, start, end = sidebar_controls()
st.title(f"🌐 IV Surface — {ticker}")

chain = load_chain(ticker, end)
if chain.empty:
    st.warning("No option chain for this selection.")
    st.stop()

spot = float(chain["underlying_price"].iloc[0])
surface = fit_surface(chain, spot, end)

st.subheader("3D implied-volatility surface")
st.plotly_chart(plot_surface_3d(surface), use_container_width=True)

st.subheader("Skew curves (IV vs log-moneyness, per expiry)")
curves = skew_curves(chain, spot, end)
skew_fig = go.Figure()
for t, curve in sorted(curves.items()):
    skew_fig.add_trace(
        go.Scatter(
            x=curve["log_moneyness"], y=curve["iv"], mode="lines+markers", name=f"{t * 365:.0f}d"
        )
    )
skew_fig.update_layout(xaxis_title="log-moneyness ln(K/S)", yaxis_title="IV")
st.plotly_chart(skew_fig, use_container_width=True)

st.subheader("Term structure (IV vs time, per moneyness bucket)")
ts = term_structure(surface)
term_fig = go.Figure()
for m, group in ts.groupby("moneyness"):
    term_fig.add_trace(
        go.Scatter(x=group["t"], y=group["iv"], mode="lines+markers", name=f"{m:.0%}")
    )
term_fig.update_layout(xaxis_title="time to expiry (years)", yaxis_title="IV")
st.plotly_chart(term_fig, use_container_width=True)
