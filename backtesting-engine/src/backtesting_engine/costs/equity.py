"""Standard equity cost model: per-share commission + adverse fill + slippage."""

from __future__ import annotations

from dataclasses import dataclass

from backtesting_engine.core.types import MarketQuote, Order, OrderType, Side


@dataclass(frozen=True)
class StandardEquityCostModel:
    """Conservative equity costs.

    * Commission: ``per_share`` dollars per share (default $0.005).
    * Fills: at the bar close, pushed one ``slippage`` tick to the adverse side
      (buys fill higher, sells fill lower). Default slippage is $0.01.
    * Limit orders fill at the better of (limit price, adverse-adjusted close);
      if the adverse price is worse than the limit, the engine treats it as a
      fill at the limit (optimistic edge intentionally avoided by the caller,
      which only submits marketable limits).
    """

    per_share: float = 0.005
    slippage: float = 0.01

    def compute_fill_price(self, order: Order, market: MarketQuote) -> float:
        base = market.close if market.close is not None else market.mid()
        if base is None:
            raise ValueError("equity cost model requires a close price to fill against")
        adverse = base + order.side.sign * self.slippage
        if order.order_type is OrderType.LIMIT and order.limit_price is not None:
            if order.side is Side.BUY:
                return min(adverse, order.limit_price)
            return max(adverse, order.limit_price)
        return adverse

    def compute_commission(self, order: Order) -> float:
        return self.per_share * order.quantity
