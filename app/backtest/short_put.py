"""Short-put trade accounting (P7), hand-checkable.

Per contract (multiplier 100):
- open: sell at bid (conservative) minus slippage; credit = (price) * 100 - commission.
- close early: buy at ask plus slippage; debit = price * 100 + commission.
- expire OTM: no closing cost.
- assigned at expiry (S_T < K): cost = (K - S_T) * 100 + assignment_fee, i.e. the
  intrinsic value is realized immediately (shares assumed sold at S_T).
- cash-secured collateral = K * 100 per contract (credit not netted).
"""
from __future__ import annotations

from app.backtest.interfaces import Costs

MULT = 100


def open_credit(bid: float, ask: float, costs: Costs, contracts: int = 1) -> float:
    if bid is None or ask is None or bid < 0 or ask < bid:
        raise ValueError("cannot open: no valid two-sided quote")
    price = bid if costs.fill == "conservative" else (bid + ask) / 2
    price = max(price - costs.slippage_per_share, 0.0)
    return (price * MULT - costs.commission_per_contract) * contracts


def close_debit(bid: float, ask: float, costs: Costs, contracts: int = 1) -> float:
    if bid is None or ask is None or bid < 0 or ask < bid:
        raise ValueError("cannot close: no valid two-sided quote")
    price = ask if costs.fill == "conservative" else (bid + ask) / 2
    price = price + costs.slippage_per_share
    return (price * MULT + costs.commission_per_contract) * contracts


def expiry_cost(strike: float, underlying_close: float, costs: Costs, contracts: int = 1) -> float:
    intrinsic = max(strike - underlying_close, 0.0)
    if intrinsic == 0:
        return 0.0
    return (intrinsic * MULT + costs.assignment_fee) * contracts


def collateral(strike: float, contracts: int = 1) -> float:
    return strike * MULT * contracts
