"""Standard options cost model: per-contract commission + adverse spread fill."""

from __future__ import annotations

from dataclasses import dataclass

from backtesting_engine.core.types import MarketQuote, Order, OrderType, Side


@dataclass(frozen=True)
class StandardOptionsCostModel:
    """Conservative options costs.

    * Commission: ``per_contract`` dollars per contract (default $0.65).
    * Fills: buys at the **ask**, sells at the **bid**, then pushed one tick
      further adverse. Tick size is a penny for options trading under $3 and a
      nickel otherwise (the standard penny/nickel pilot convention).
    * If a bid/ask is missing the model falls back to the mark/last with a
      synthetic half-spread so the loop never crashes on a thin quote.
    """

    per_contract: float = 0.65
    penny_threshold: float = 3.0
    penny_tick: float = 0.01
    nickel_tick: float = 0.05
    fallback_half_spread: float = 0.05

    def _tick(self, price: float) -> float:
        return self.penny_tick if price < self.penny_threshold else self.nickel_tick

    def compute_fill_price(self, order: Order, market: MarketQuote) -> float:
        bid, ask = market.bid, market.ask
        if bid is None or ask is None:
            ref = market.mid()
            if ref is None:
                raise ValueError("options cost model requires bid/ask or a mark to fill")
            bid = ref - self.fallback_half_spread
            ask = ref + self.fallback_half_spread

        # Buys cross the ask, sells hit the bid; then one tick further adverse.
        adverse = ask + self._tick(ask) if order.side is Side.BUY else bid - self._tick(bid)

        if order.order_type is OrderType.LIMIT and order.limit_price is not None:
            if order.side is Side.BUY:
                adverse = min(adverse, order.limit_price)
            else:
                adverse = max(adverse, order.limit_price)
        return max(adverse, 0.0)

    def compute_commission(self, order: Order) -> float:
        return self.per_contract * order.quantity
