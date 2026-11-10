"""Strategy finder: a view in, several exact candidate structures out.

For a view (direction, target or move, horizon, risk limits):
1. take the expiry at or just after the horizon and read its chain once;
2. size distances with the market's own expected move, spot x ATM IV x sqrt(T);
3. build each candidate from rules (strike = a price), snapped to listed strikes
   with real two-sided quotes, priced at mid;
4. measure it with the exact payoff engine (max profit and loss, breakevens), the
   P&L if the view plays out, and a risk-neutral probability of profit from the
   chain's ATM IV (a model estimate, labelled so);
5. drop what breaks the user's limits, rank by fit to the view, explain each.

Every candidate also carries ready-made inputs for the builder, the backtester
and the chain viewer, so it can be opened in each.
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

import numpy as np

from app.analytics import bsm
from app.analytics.chain import AnalyticsConfig, time_to_expiry
from app.backtest.engine import naked_shorts, reg_t_requirement
from app.core.payoff import compute_payoff, position_payoff
from app.jobs.iv_backfill import close_time
from app.models import OptionLeg, StockLeg
from app.providers.options.base import DATA_STATUS_LABELS
from app.strategy.view import View

MAX_CANDIDATES = 5
NY = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class LegRule:
    kind: str  # "call" | "put" | "stock"
    side: str  # "buy" | "sell"
    at: str = "spot"  # where the strike goes: see _target_price
    shares: int = 0


@dataclass(frozen=True)
class Template:
    key: str
    name: str
    directions: tuple[str, ...]
    legs: tuple[LegRule, ...]
    how: str
    wins: str
    loses: str
    backtest_preset: str = "custom"
    needs_shares: bool = False
    undefined_risk: bool = False


TEMPLATES: list[Template] = [
    Template("bull_call_spread", "Bull call spread", ("bullish",),
             (LegRule("call", "buy", "spot"), LegRule("call", "sell", "target")),
             "Buy an at-the-money call and sell a call at your target. The sold call pays for part of the bought one.",
             "Earns its maximum if the stock is at or above your target at expiry.",
             "Loses the debit if the stock finishes at or below the lower strike.", "bull_call_spread"),
    Template("long_call", "Long call", ("bullish",), (LegRule("call", "buy", "spot"),),
             "Buy an at-the-money call: the right to buy 100 shares at the strike until expiry.",
             "Gains without limit above the breakeven, so it suits a move bigger than your target.",
             "Loses the whole premium if the stock is at or below the strike at expiry; time decay works against it.",
             "custom"),
    Template("bull_put_spread", "Bull put spread", ("bullish", "income"),
             (LegRule("put", "sell", "down_half"), LegRule("put", "buy", "down_half_wing")),
             "Sell a put below the price and buy a lower put as protection; you collect a credit up front.",
             "Keeps the whole credit if the stock stays above the sold put. It does not need the stock to rise.",
             "Loses up to the width minus the credit if the stock falls through both strikes.", "bull_put_spread"),
    Template("cash_secured_put", "Cash-secured put", ("bullish", "income"), (LegRule("put", "sell", "down_half"),),
             "Sell a put below the price with cash set aside to buy 100 shares at the strike if assigned.",
             "Keeps the premium if the stock stays above the strike; otherwise you buy the shares at a discount to "
             "today's price.",
             "Losses grow like owning the stock below the breakeven, down to the strike x 100 minus the premium.",
             "cash_secured_put"),
    Template("covered_call", "Covered call", ("income", "bullish", "neutral"),
             (LegRule("stock", "buy", "spot", shares=100), LegRule("call", "sell", "up_half")),
             "Own 100 shares and sell a call above the price, collecting premium for capping the upside.",
             "Adds the premium to the stock's return if it stays below the strike; if called away you keep gains up to "
             "the strike.",
             "Keeps the stock's downside (cushioned by the premium) and gives up gains above the strike.",
             "covered_call", needs_shares=True),
    Template("bear_put_spread", "Bear put spread", ("bearish",),
             (LegRule("put", "buy", "spot"), LegRule("put", "sell", "target")),
             "Buy an at-the-money put and sell a put at your target, cutting the cost of the bet on a fall.",
             "Earns its maximum if the stock is at or below your target at expiry.",
             "Loses the debit if the stock finishes at or above the upper strike.", "bear_put_spread"),
    Template("long_put", "Long put", ("bearish",), (LegRule("put", "buy", "spot"),),
             "Buy an at-the-money put: the right to sell 100 shares at the strike until expiry.",
             "Gains as the stock falls below the breakeven, most on a large drop.",
             "Loses the whole premium if the stock is at or above the strike at expiry.", "custom"),
    Template("bear_call_spread", "Bear call spread", ("bearish", "income"),
             (LegRule("call", "sell", "up_half"), LegRule("call", "buy", "up_half_wing")),
             "Sell a call above the price and buy a higher call as protection; you collect a credit.",
             "Keeps the credit if the stock stays below the sold call. It does not need a fall.",
             "Loses up to the width minus the credit if the stock rallies through both strikes.", "bear_call_spread"),
    Template("iron_condor", "Iron condor", ("neutral", "income"),
             (LegRule("put", "buy", "range_down_wing"), LegRule("put", "sell", "range_down"),
              LegRule("call", "sell", "range_up"), LegRule("call", "buy", "range_up_wing")),
             "Sell a put spread below and a call spread above the price; you collect two credits.",
             "Keeps the credit if the stock finishes between the sold strikes.",
             "Loses up to one spread's width minus the credit if the stock breaks out either way.", "iron_condor"),
    Template("iron_butterfly", "Iron butterfly", ("neutral",),
             (LegRule("put", "buy", "move_down"), LegRule("put", "sell", "spot"), LegRule("call", "sell", "spot"),
              LegRule("call", "buy", "move_up")),
             "Sell the at-the-money put and call, and buy wings one expected move away.",
             "Earns most if the stock pins near today's price; the credit is larger than a condor's.",
             "Profit shrinks quickly as the stock moves; it loses past the breakevens, capped by the wings.",
             "iron_butterfly"),
    Template("short_strangle", "Short strangle", ("neutral", "income"),
             (LegRule("put", "sell", "range_down"), LegRule("call", "sell", "range_up")),
             "Sell an out-of-the-money put and call with no protection.",
             "Keeps both premiums if the stock stays between the strikes.",
             "Losses are unlimited above the call and large below the put: a margin trade.", "short_strangle",
             undefined_risk=True),
    Template("long_straddle", "Long straddle", ("volatile",),
             (LegRule("call", "buy", "spot"), LegRule("put", "buy", "spot")),
             "Buy the at-the-money call and put: you profit from a large move either way.",
             "Gains once the stock moves past either breakeven, without limit upward.",
             "Loses up to both premiums if the stock stays near the strike; time decay is fast.", "long_straddle"),
    Template("long_strangle", "Long strangle", ("volatile",),
             (LegRule("call", "buy", "move_up_half"), LegRule("put", "buy", "move_down_half")),
             "Buy an out-of-the-money call and put: cheaper than a straddle, needs a bigger move.",
             "Gains on a move beyond either breakevens.",
             "Loses both premiums if the stock stays between the strikes.", "long_strangle"),
]


@dataclass
class Market:
    ticker: str
    spot: float
    expiration: str
    dte: int
    atm_iv: float
    chain: list  # OptionQuote
    provider: str
    data_status: str
    T: float = 0.0
    quotes: dict = field(default_factory=dict)  # (type, strike) -> quote with a real two-sided market

    @property
    def expected_move(self) -> float:
        return self.spot * self.atm_iv * math.sqrt(max(self.T, 1 / 365))


def pick_expiration(expirations: list[str], horizon_days: int, today: dt.date) -> str | None:
    """The first expiry on or after the horizon (a view needs time to play out), else the last listed."""
    dated = sorted((dt.date.fromisoformat(e), e) for e in expirations)
    future = [(d, e) for d, e in dated if (d - today).days >= max(1, min(horizon_days, 7))]
    if not future:
        return None
    for d, e in future:
        if (d - today).days >= horizon_days:
            return e
    return future[-1][1]


def atm_iv_from_chain(chain, spot: float, expiration: str, now: dt.datetime, cfg: AnalyticsConfig) -> float | None:
    T = time_to_expiry(expiration, now)
    best = None
    for q in chain:
        if q.bid is None or q.ask is None or q.bid <= 0 or q.ask < q.bid:
            continue
        d = abs(q.strike - spot)
        if best is None or d < best[0]:
            best = (d, q)
    if best is None or T <= 0:
        return None
    ivs = []
    for q in chain:
        if q.strike == best[1].strike and q.bid and q.bid > 0:
            iv, reason = bsm.implied_vol((q.bid + q.ask) / 2, spot, q.strike, T, cfg.risk_free_rate,
                                         cfg.dividend_yield, q.option_type == "call")
            v = float(iv)
            if reason == "" and math.isfinite(v) and v > 0:
                ivs.append(v)
    return sum(ivs) / len(ivs) if ivs else None


def target_price(view: View, m: Market) -> float:
    if view.target_price:
        return view.target_price
    if view.move_pct is not None:
        return m.spot * (1 + view.move_pct / 100)
    em = m.expected_move
    return m.spot + 0.75 * em if view.direction == "bullish" else m.spot - 0.75 * em if view.direction == "bearish" \
        else m.spot


def _wing(m: Market) -> float:
    return max(0.02 * m.spot, 0.25 * m.expected_move)


def _target(at: str, view: View, m: Market) -> float:
    em, s, tgt = m.expected_move, m.spot, target_price(view, m)
    half = 0.5 * em
    rng = abs(view.move_pct) / 100 * s if view.direction == "neutral" and view.move_pct else 0.75 * em
    return {
        "spot": s, "target": tgt,
        "up_half": s + half, "up_half_wing": s + half + _wing(m),
        "down_half": s - half, "down_half_wing": s - half - _wing(m),
        "range_up": s + rng, "range_up_wing": s + rng + _wing(m),
        "range_down": s - rng, "range_down_wing": s - rng - _wing(m),
        "move_up": s + em, "move_down": s - em,
        "move_up_half": s + half, "move_down_half": s - half,
    }[at]


def build_legs(t: Template, view: View, m: Market) -> list[OptionLeg | StockLeg] | None:
    """Snap each rule to a listed, two-sided strike. Same-type legs never share a strike, and
    a spread's second leg lands on the correct side of its first (or the template is skipped)."""
    legs: list[OptionLeg | StockLeg] = []
    used: dict[str, list[float]] = {"call": [], "put": []}
    for rule in t.legs:
        if rule.kind == "stock":
            legs.append(StockLeg(side=rule.side, quantity=rule.shares, premium=round(m.spot, 2)))
            continue
        strikes = sorted(k for (kind, k) in m.quotes if kind == rule.kind and k not in used[rule.kind])
        if not strikes:
            return None
        goal = _target(rule.at, view, m)
        strike = min(strikes, key=lambda k: (abs(k - goal), k))
        prev = used[rule.kind][-1] if used[rule.kind] else None
        if prev is not None and rule.at.endswith(("_wing", "target", "move_up", "move_down")):
            outward = goal > prev
            side = [k for k in strikes if (k > prev if outward else k < prev)]
            if not side:
                return None
            strike = min(side, key=lambda k: (abs(k - goal), k))
        used[rule.kind].append(strike)
        q = m.quotes[(rule.kind, strike)]
        legs.append(OptionLeg(option_type=rule.kind, side=rule.side, strike=strike, expiration=m.expiration,
                              premium=round((q.bid + q.ask) / 2, 2)))
    return legs


def probability_of_profit(legs, m: Market, cfg: AnalyticsConfig) -> float | None:
    """Risk-neutral P(P&L > 0 at expiry) under a lognormal at the chain's ATM IV. A model
    estimate: it ignores skew and real-world drift."""
    sigma, T = m.atm_iv, max(m.T, 1 / 365)
    if not sigma or sigma <= 0:
        return None
    sd = sigma * math.sqrt(T)
    mu = math.log(m.spot) + (cfg.risk_free_rate - cfg.dividend_yield - 0.5 * sigma ** 2) * T
    z = np.linspace(-6, 6, 2401)
    prices = np.exp(mu + sd * z)
    pdf = np.exp(-0.5 * z ** 2)
    pnl = np.array([position_payoff(legs, float(p)) for p in prices])
    return float(pdf[pnl > 0].sum() / pdf.sum())


def capital_required(t: Template, legs, computed: dict, m: Market) -> float:
    """Cash or margin to hold one unit: max loss for defined-risk spreads, the strike for a
    cash-secured put, the shares for a covered call, a Reg-T estimate for naked shorts."""
    shape = [(leg.option_type, leg.side, leg.quantity, leg.strike) for leg in legs if isinstance(leg, OptionLeg)]
    shares = sum(leg.quantity for leg in legs if isinstance(leg, StockLeg))
    if t.key == "cash_secured_put":
        return sum(k * 100 for _r, side, _q, k in shape if side == "sell")
    if shares:
        return m.spot * shares
    if naked_shorts(shape, 0):
        prices = [leg.premium for leg in legs if isinstance(leg, OptionLeg)]
        return reg_t_requirement(shape, prices, m.spot)
    loss = computed["max_loss"]
    return float(loss) if loss != "unlimited" else float("inf")


def backtest_inputs(t: Template, legs, m: Market, view: View) -> dict:
    """The same structure as a backtest request: strikes as delta (first leg of each type) or
    a $ offset from that leg, at this DTE, over the last year."""
    out_legs: list[dict] = []
    first: dict[str, int] = {}
    for leg in legs:
        if isinstance(leg, StockLeg):
            continue
        idx = len(out_legs)
        if leg.option_type in first:
            ref = out_legs[first[leg.option_type]]
            rule = {"method": "offset", "from_leg": first[leg.option_type], "value": round(leg.strike - ref["_strike"], 2)}
        else:
            first[leg.option_type] = idx
            q = m.quotes.get((leg.option_type, leg.strike))
            delta = abs(q.greeks.delta) if q is not None and q.greeks and q.greeks.delta else None
            if abs(leg.strike - m.spot) <= 0.005 * m.spot:
                rule = {"method": "atm", "value": 0}
            elif delta and 0.01 <= delta <= 0.99:
                rule = {"method": "delta", "value": round(delta, 2)}
            else:
                pct = (1 - leg.strike / m.spot) * 100 if leg.option_type == "put" else (leg.strike / m.spot - 1) * 100
                rule = {"method": "pct_otm", "value": round(max(-50, min(50, pct)), 1)}
        out_legs.append({"right": leg.option_type, "side": leg.side, "ratio": 1, "strike": rule, "_strike": leg.strike})
    for leg in out_legs:
        leg.pop("_strike")
    end = dt.date.today() - dt.timedelta(days=1)
    dte = max(m.dte, 1)
    credit = sum((1 if leg.side == "sell" else -1) * leg.premium for leg in legs if isinstance(leg, OptionLeg)) > 0
    return {
        "underlying": m.ticker, "start": (end - dt.timedelta(days=365)).isoformat(), "end": end.isoformat(),
        "strategy": t.backtest_preset if t.backtest_preset != "custom" else "custom", "legs": out_legs,
        "stock": {"shares_per_contract": 100} if t.needs_shares else None,
        "entry": {"dte_target": dte, "dte_min": max(0, dte - 10), "dte_max": dte + 10, "expirations": "any",
                  "frequency": "weekly", "weekday": 0, "max_open": 3, "min_iv": None, "max_iv": None,
                  "min_credit": None, "delta_tolerance": 0.05},
        "exit": ({"profit_target_pct": 50, "stop_loss_pct": 200, "exit_dte": min(21, dte // 2) if dte > 14 else None,
                  "max_hold_days": None} if credit else
                 {"profit_target_pct": 100, "stop_loss_pct": 50, "exit_dte": min(21, dte // 2) if dte > 14 else None,
                  "max_hold_days": None}),
    }


def _money(v) -> str:
    return "unlimited" if v == "unlimited" else f"${float(v):,.2f}"


def evaluate(t: Template, view: View, m: Market, cfg: AnalyticsConfig) -> dict | None:
    legs = build_legs(t, view, m)
    if legs is None:
        return None
    computed = compute_payoff(m.ticker, legs, [], m.spot, m.provider, m.data_status)["computed"]
    tgt = target_price(view, m)
    if view.direction == "volatile":
        scenarios = [m.spot + m.expected_move, m.spot - m.expected_move]
        at_view = sum(position_payoff(legs, p) for p in scenarios) / 2
        scenario = f"a one-expected-move swing either way (±{_money(m.expected_move)})"
    else:
        at_view = position_payoff(legs, tgt)
        scenario = f"{m.ticker} at {_money(tgt)} at expiry" if view.direction in ("bullish", "bearish") else \
            f"{m.ticker} unchanged at {_money(m.spot)}"
    capital = capital_required(t, legs, computed, m)
    pop = probability_of_profit(legs, m, cfg)
    max_loss = computed["max_loss"]
    risk = capital if math.isfinite(capital) and capital > 0 else None
    ror = (float(computed["max_profit"]) / float(max_loss)) if (
        computed["max_profit"] != "unlimited" and max_loss != "unlimited" and float(max_loss) > 0) else None
    fit = (at_view / risk if risk else 0.0) + 0.5 * (pop or 0)
    if t.needs_shares and view.owns_shares:
        # The shares are already held: no new capital, and this is the standard way to earn on them.
        fit += 10.0
    net = computed["net_credit"] or -computed["net_debit"]
    if t.needs_shares and view.owns_shares:
        fits = (f"Uses the 100 shares you hold: the call premium ({_money(abs(legs[1].premium) * 100)}) is income "
                f"now, in exchange for selling the shares at {_money(legs[1].strike)} if {m.ticker} finishes above it.")
    else:
        fits = (f"If your view plays out ({scenario}), this makes {_money(at_view)} on {_money(capital)} of capital "
            f"at risk." if at_view >= 0 else
            f"At your scenario ({scenario}) this loses {_money(-at_view)}; it needs a different outcome to pay.")
    return {
        "key": t.key, "name": t.name, "type": "credit" if net > 0 else "debit",
        "legs": [leg.model_dump() for leg in legs],
        "metrics": {
            "net_debit": computed["net_debit"], "net_credit": computed["net_credit"],
            "max_profit": computed["max_profit"], "max_loss": max_loss, "breakevens": computed["breakevens"],
            "pnl_at_view": round(at_view, 2), "scenario": scenario,
            "probability_of_profit": None if pop is None else round(pop, 4),
            "return_on_risk": None if ror is None else round(ror, 4),
            "capital": None if not math.isfinite(capital) else round(capital, 2),
        },
        "explanation": {"how": t.how, "wins": t.wins, "loses": t.loses, "fit": fits},
        "undefined_risk": t.undefined_risk or max_loss == "unlimited",
        "fit_score": round(fit, 4),
        "payoff_curve": computed["payoff_curve"],
        "links": {
            "builder": {"ticker": m.ticker, "legs": [leg.model_dump() for leg in legs]},
            "backtest": backtest_inputs(t, legs, m, view),
            "chain": {"ticker": m.ticker, "expiration": m.expiration},
        },
    }


async def find_strategies(view: View, ticker: str, provider, cfg: AnalyticsConfig | None = None,
                          now: dt.datetime | None = None, spot: float | None = None) -> dict:
    cfg = cfg or AnalyticsConfig()
    now = now or dt.datetime.now(dt.UTC)
    ticker = ticker.upper()
    today = now.astimezone(NY).date()
    spot = spot if spot is not None else await provider.get_underlying_price(ticker)
    expiration = pick_expiration(await provider.list_expirations(ticker), view.horizon_days, today)
    if expiration is None:
        raise LookupError(f"No {ticker} expiration a week or more out.")
    chain = await provider.get_chain(ticker, expiration)
    iv = atm_iv_from_chain(chain, spot, expiration, now, cfg)
    if iv is None:
        raise LookupError(f"Could not read an at-the-money implied volatility for {ticker} {expiration}.")
    m = Market(ticker, spot, expiration, (dt.date.fromisoformat(expiration) - today).days, iv, chain,
               provider.name, provider.data_status, T=time_to_expiry(expiration, close_time(today)))
    m.quotes = {(q.option_type, q.strike): q for q in chain
                if q.bid is not None and q.ask is not None and q.bid > 0 and q.ask >= q.bid}
    direction = view.direction or "bullish"
    pool = [t for t in TEMPLATES if direction in t.directions]
    results, excluded = [], []
    for t in pool:
        if t.needs_shares and not view.owns_shares and direction != "income":
            continue
        cand = evaluate(t, view.model_copy(update={"direction": direction}), m, cfg)
        if cand is None:
            excluded.append({"name": t.name, "reason": "the listed strikes cannot express it"})
            continue
        mp, ml = cand["metrics"]["max_profit"], cand["metrics"]["max_loss"]
        if mp != "unlimited" and float(mp) <= 0:
            excluded.append({"name": t.name, "reason": "cannot profit at today's mid prices"})
            continue
        if ml != "unlimited" and float(ml) <= 0:
            excluded.append({"name": t.name, "reason": "quotes look inconsistent (no possible loss)"})
            continue
        if view.defined_risk_only and cand["undefined_risk"]:
            excluded.append({"name": t.name, "reason": "undefined risk (you asked for defined risk)"})
            continue
        loss = cand["metrics"]["max_loss"]
        if view.max_loss and (loss == "unlimited" or float(loss) > view.max_loss):
            excluded.append({"name": t.name, "reason": f"max loss {_money(loss)} is above your {_money(view.max_loss)}"})
            continue
        results.append(cand)
    results.sort(key=lambda c: c["fit_score"], reverse=True)
    for rank, c in enumerate(results[:MAX_CANDIDATES], start=1):
        c["rank"] = rank
    return {
        "ticker": ticker, "spot": round(spot, 2), "expiration": expiration, "dte": m.dte,
        "atm_iv": round(iv, 4), "expected_move": round(m.expected_move, 2),
        "target_price": round(target_price(view.model_copy(update={"direction": direction}), m), 2),
        "candidates": results[:MAX_CANDIDATES], "excluded": excluded,
        "provider": provider.name, "data_status": provider.data_status,
        "data_status_label": DATA_STATUS_LABELS.get(provider.data_status, provider.data_status),
        "method": ("Strikes come from your target and the market's expected move (price x ATM IV x sqrt(time)), "
                   "snapped to listed strikes with real bids and priced at mid. Probability of profit is a "
                   "risk-neutral lognormal estimate at the ATM IV (no skew, no drift): a model figure, not a forecast. "
                   "Figures are per one contract and exclude commissions."),
    }
