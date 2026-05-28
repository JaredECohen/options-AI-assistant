"""Backtester page: run a pre-built option strategy and view the results."""

from __future__ import annotations

import streamlit as st
from _shared import get_options_source, sidebar_controls

from optionscoach.backtester import (
    CashSecuredPut,
    CoveredCall,
    IronCondor,
    ShortPut,
    ShortStraddle,
    ShortStrangle,
    StrategyConfig,
    run_strategy,
)

st.set_page_config(page_title="Backtester — OptionsCoach.AI", page_icon="🧪", layout="wide")
ticker, start, end = sidebar_controls()
st.title(f"🧪 Backtester — {ticker}")

STRATEGIES = {
    "Short Put": ShortPut,
    "Cash-Secured Put": CashSecuredPut,
    "Covered Call": CoveredCall,
    "Short Straddle": ShortStraddle,
    "Short Strangle": ShortStrangle,
    "Iron Condor": IronCondor,
}

name = st.selectbox("Strategy", list(STRATEGIES))
c1, c2, c3 = st.columns(3)
target_delta = c1.slider("Target delta", 0.05, 0.50, 0.30, 0.05)
dte = c2.slider("DTE", 7, 90, 30, 1)
profit_target = c3.slider("Profit target %", 0.1, 0.9, 0.5, 0.1)
c4, c5 = st.columns(2)
stop_mult = c4.slider("Stop-loss multiple", 1.0, 5.0, 2.0, 0.5)
time_exit = c5.slider("Time-exit DTE", 0, 21, 7, 1)

if st.button("Run backtest", type="primary"):
    config = StrategyConfig(
        symbol=ticker,
        target_delta=target_delta,
        dte=dte,
        profit_target_pct=profit_target,
        stop_loss_multiple=stop_mult,
        time_exit_dte=time_exit,
    )
    with st.spinner("Running backtest..."):
        result = run_strategy(
            STRATEGIES[name], start, end, config=config, data_source=get_options_source(ticker)
        )
    m = result.metrics
    a, b, c, d = st.columns(4)
    a.metric("Final equity", f"${result.final_equity:,.0f}")
    b.metric("Total return", f"{m['total_return']:.1%}")
    c.metric("Sharpe", f"{m['sharpe']:.2f}")
    d.metric("Max drawdown", f"{m['max_drawdown']:.1%}")
    e, f, g, h = st.columns(4)
    e.metric("CAGR", f"{m['cagr']:.1%}")
    f.metric("Win rate", f"{m['win_rate']:.0%}")
    g.metric("Profit factor", f"{m['profit_factor']:.2f}")
    h.metric("Trades", f"{int(m['num_trades'])}")

    st.subheader("Equity curve")
    st.line_chart(result.equity_curve)
    st.subheader("Trade log")
    st.dataframe(result.trade_log)
else:
    st.info("Configure the strategy and click **Run backtest**.")
