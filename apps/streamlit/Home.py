"""OptionsCoach.AI — Streamlit home page.

Launch the whole multi-page app from the repo root with::

    streamlit run apps/streamlit/Home.py

Pages live in ``apps/streamlit/pages/`` and are auto-discovered by Streamlit.
"""

from __future__ import annotations

import streamlit as st
from _shared import DEFAULT_TICKERS, sidebar_controls

st.set_page_config(page_title="OptionsCoach.AI", page_icon="📈", layout="wide")

ticker, start, end = sidebar_controls()

st.title("📈 OptionsCoach.AI")
st.write(
    "Analytics, screening, backtesting and volatility-surface tools for listed "
    "equity options — powered by the `backtesting-engine`."
)

st.subheader("Pages")
st.markdown(
    """
- **Analytics** — implied/historical vol, IV rank & percentile, greeks, liquidity.
- **Screener** — run the `cheap_by_hv`, `high_iv_rank` and `iv_hv_gap` screens.
- **Backtester** — covered call, cash-secured put, short put/straddle/strangle, iron condor.
- **Surface** — fit and visualize the implied-volatility surface in 3D.
"""
)

st.info(
    f"Defaults: tickers {', '.join(DEFAULT_TICKERS)}; date range = last ~252 trading days. "
    "Set `OPTIONSCOACH_LIVE=1` with `FMP_API_KEY` to use live equity data."
)
st.caption(f"Current selection: {ticker} from {start} to {end}")
