"""T5: exact expiration bounds, independent of the chart grid."""
from __future__ import annotations

import json

import pytest

from app.core.payoff import (
    PayoffInputError,
    compute_payoff,
    exact_breakevens,
    payoff_bounds,
    premium_from_quote,
    resolve_premiums,
)
from app.models import OptionLeg, StockLeg
from app.providers.options.base import OptionQuote
from app.providers.options.mock import MockOptionsProvider

EXP = "2026-12-18"


def opt(t, side, k, prem, qty=1, mult=100, exp=EXP):
    return OptionLeg(option_type=t, side=side, strike=k, premium=prem, quantity=qty, multiplier=mult, expiration=exp)


def run(legs, underlying=100.0):
    out = compute_payoff("TEST", legs, [], underlying, "test")["computed"]
    json.dumps(out, allow_nan=False)  # must be strict JSON (no Infinity/NaN)
    return out


def test_recorded_short_put_example_reports_full_loss_to_zero():
    """Notion T5: short 100 put for 5, qty 1, x100, underlying 100. The old
    grid started at 50 and reported $4,500; the true worst case at S=0 is $9,500."""
    c = run([opt("put", "sell", 100, 5)])
    assert c["max_loss"] == 9500.0
    assert c["bounds"]["max_loss"] == {"unbounded": False, "value": -9500.0, "at_price": 0.0}
    assert c["max_profit"] == 500.0
    assert c["breakevens"] == [95.0]
    assert c["net_credit"] == 500.0


def test_bounds_do_not_depend_on_underlying_or_chart_range():
    legs = [opt("put", "sell", 100, 5)]
    for underlying in (1.0, 50.0, 100.0, 400.0, 10_000.0):
        c = run(legs, underlying)
        assert c["max_loss"] == 9500.0 and c["max_profit"] == 500.0 and c["breakevens"] == [95.0]


def test_naked_short_call_loss_is_unlimited():
    c = run([opt("call", "sell", 100, 4)])
    assert c["max_loss"] == "unlimited"
    assert c["bounds"]["max_loss"] == {"unbounded": True, "value": None, "at_price": None}
    assert c["max_profit"] == 400.0
    assert c["breakevens"] == [104.0]


def test_long_call_profit_unlimited_loss_is_premium():
    c = run([opt("call", "buy", 100, 3)])
    assert c["max_profit"] == "unlimited" and c["max_loss"] == 300.0 and c["breakevens"] == [103.0]


def test_long_put():
    c = run([opt("put", "buy", 100, 3)])
    assert c["max_profit"] == 9700.0 and c["bounds"]["max_profit"]["at_price"] == 0.0
    assert c["max_loss"] == 300.0 and c["breakevens"] == [97.0]


def test_bull_call_spread():
    c = run([opt("call", "buy", 100, 5), opt("call", "sell", 110, 2)])
    assert c["max_profit"] == 700.0 and c["max_loss"] == 300.0 and c["breakevens"] == [103.0]
    assert c["net_debit"] == 300.0


def test_bull_put_credit_spread():
    c = run([opt("put", "sell", 100, 4), opt("put", "buy", 90, 1)])
    assert c["max_profit"] == 300.0 and c["max_loss"] == 700.0 and c["breakevens"] == [97.0]


def test_iron_condor_two_breakevens():
    legs = [
        opt("put", "buy", 85, 0.5), opt("put", "sell", 90, 1.5),
        opt("call", "sell", 110, 1.5), opt("call", "buy", 115, 0.5),
    ]
    c = run(legs)
    assert c["max_profit"] == 200.0 and c["max_loss"] == 300.0
    assert c["breakevens"] == [88.0, 112.0]


def test_long_straddle_and_short_strangle():
    c = run([opt("call", "buy", 100, 4), opt("put", "buy", 100, 4)])
    assert c["max_profit"] == "unlimited" and c["max_loss"] == 800.0 and c["breakevens"] == [92.0, 108.0]
    c = run([opt("call", "sell", 110, 2), opt("put", "sell", 90, 2)])
    assert c["max_loss"] == "unlimited" and c["max_profit"] == 400.0 and c["breakevens"] == [86.0, 114.0]


def test_covered_call_is_bounded_and_stock_loss_runs_to_zero():
    legs = [StockLeg(side="buy", premium=100, quantity=100), opt("call", "sell", 110, 2)]
    c = run(legs)
    assert c["max_profit"] == 1200.0
    assert c["max_loss"] == 9800.0  # stock to zero minus call credit
    assert c["breakevens"] == [98.0]


def test_ratio_spread_quantities_and_unbounded_upside_loss():
    # buy 1 x 100 call, sell 2 x 110 calls -> net short a call above 110
    c = run([opt("call", "buy", 100, 5), opt("call", "sell", 110, 2, qty=2)])
    assert c["max_loss"] == "unlimited"
    assert c["max_profit"] == 900.0  # at 110: 10 - 5 + 4 = 9 per share
    assert c["breakevens"] == [101.0, 119.0]


def test_adjusted_multiplier_scales_bounds():
    c = run([opt("put", "sell", 100, 5, mult=150)])
    assert c["max_loss"] == 14250.0 and c["max_profit"] == 750.0


def test_breakeven_exactly_at_strike_and_flat_zero_segments():
    # long call with zero premium: payoff is exactly 0 for S <= 100; no breakeven is reported
    # across the flat-zero segment, but the strike itself is where it turns positive.
    legs = [opt("call", "buy", 100, 0.0)]
    assert exact_breakevens(legs) == [100.0]
    mp, ml = payoff_bounds(legs)
    assert mp.unbounded and ml.value == 0.0


def test_guaranteed_loss_is_reported_signed_not_zero():
    # buy 100 call @10, sell 90 call @1: best case is -9/share at S <= 90
    c = run([opt("call", "buy", 100, 10), opt("call", "sell", 90, 1)])
    assert c["max_profit"] == -900.0 and c["bounds"]["max_profit"]["value"] == -900.0
    assert c["max_loss"] == 1900.0


def test_mixed_expirations_rejected():
    with pytest.raises(PayoffInputError) as e:
        run([opt("call", "buy", 100, 3), opt("call", "sell", 110, 1, exp="2027-01-15")])
    assert e.value.code == "mixed_expirations"


def test_missing_premium_is_an_error_not_zero():
    with pytest.raises(PayoffInputError) as e:
        run([OptionLeg(option_type="put", side="sell", strike=100, expiration=EXP)])
    assert e.value.code == "premium_missing"


def test_premium_from_quote_conventions_and_bad_quotes():
    q = OptionQuote("call", 100, EXP, bid=2.0, ask=2.4, last=9.99, mark=None, symbol="X")
    assert premium_from_quote(q, "buy", "mid") == pytest.approx(2.2)
    assert premium_from_quote(q, "buy", "conservative") == 2.4
    assert premium_from_quote(q, "sell", "conservative") == 2.0
    with pytest.raises(PayoffInputError) as e:
        premium_from_quote(OptionQuote("call", 100, EXP, 2.5, 2.0, None, None), "buy", "mid")
    assert e.value.code == "quote_crossed"
    with pytest.raises(PayoffInputError) as e:
        premium_from_quote(OptionQuote("call", 100, EXP, None, 2.0, 1.0, None), "buy", "mid")
    assert e.value.code == "quote_one_sided"


async def test_resolve_premiums_records_provenance():
    provider = MockOptionsProvider()
    base = await provider.get_underlying_price("SPY")
    strike = round(base * 0.5 + 5 * 10, 2)
    legs = [OptionLeg(option_type="call", side="buy", strike=strike, expiration=EXP)]
    resolved, used, underlying = await resolve_premiums("SPY", legs, provider, convention="conservative")
    assert resolved[0].premium == used[0]["ask"]
    assert used[0]["premium_source"] == "quote" and used[0]["fill_convention"] == "conservative"
    assert used[0]["data_status"] == "synthetic" and used[0]["contract_symbol"].endswith("SYNTHETIC")
    assert underlying == base


async def test_resolve_premiums_unknown_strike_is_quote_unavailable():
    legs = [OptionLeg(option_type="call", side="buy", strike=1.23, expiration=EXP)]
    with pytest.raises(PayoffInputError) as e:
        await resolve_premiums("SPY", legs, MockOptionsProvider())
    assert e.value.code == "quote_unavailable"


def test_chart_curve_passes_through_zero_and_strikes():
    c = run([opt("put", "sell", 100, 5)])
    prices = [p["price"] for p in c["payoff_curve"]]
    assert 0.0 in prices and 100.0 in prices and 95.0 in prices
    assert min(p["payoff"] for p in c["payoff_curve"]) == -9500.0


async def test_premiums_for_all_legs_come_from_one_chain_read():
    # Regression: ISSUE-010 (QA 2026-11-09). Each blank-premium leg re-fetched the whole chain;
    # a 4-leg SPY condor took 41 s live.
    import datetime as _dt

    from app.core.payoff import PayoffInputError, resolve_premiums
    from app.models import OptionLeg
    from app.providers.options.mock import MockOptionsProvider

    class Counting(MockOptionsProvider):
        chains = 0

        async def get_chain(self, ticker, expiration):
            Counting.chains += 1
            return await super().get_chain(ticker, expiration)

    p = Counting(today=_dt.date(2026, 11, 9))
    exp = (await p.list_expirations("SPY"))[0]
    strikes = sorted({q.strike for q in await p.get_chain("SPY", exp)})
    Counting.chains = 0
    legs = [OptionLeg(option_type=t, side=s, strike=k, expiration=exp) for t, s, k in
            [("put", "buy", strikes[2]), ("put", "sell", strikes[5]), ("call", "sell", strikes[-6]), ("call", "buy", strikes[-3])]]
    resolved, used, _ = await resolve_premiums("SPY", legs, p)
    assert Counting.chains == 1 and all(r.premium is not None for r in resolved) and len(used) == 4
    with pytest.raises(PayoffInputError, match="not in the chain"):
        await resolve_premiums("SPY", [OptionLeg(option_type="call", side="buy", strike=12345, expiration=exp)], p)
