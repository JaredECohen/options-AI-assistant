# Backtesting (P7)

OptionsCoach runs its own options backtester on ThetaData end-of-day history.
This file is the contract between the engine (`app/backtest/`), the API
(`app/api/routes_backtest.py`) and the page (`frontend/src/components/Backtests.jsx`).

## What it simulates

- **Daily loop over trading days** (days with an underlying close in range).
- **Entry** on scheduled days, at most `entry.max_open` positions at once.
  - The expiration is the one nearest `dte_target` within `[dte_min, dte_max]`
    that matches the `expirations` filter.
  - The expiration's chain is the closing chain on the entry day; legs are
    selected from it.
- **Marks:** open legs are marked daily at the closing mid. A missing quote
  carries the last mark and is counted under `stale_marks`.
- **Exits** are checked on each close in this order: expiration, stop loss,
  profit target, exit DTE, max hold days. Fills follow the cost model.
  - When a closing quote is missing, the exit check waits for the next day that
    has one.
  - At expiration each leg settles at intrinsic value against the underlying
    close (cash-settled approximation). On a covered call, shares called away
    are sold at the strike.
- **Equity** = starting capital + realized P&L + open positions marked to mid
  (+ shares at the close for covered calls).
- **Benchmark:** buy and hold the underlying with the same starting capital, no dividends.

Caveats are returned with every result. End-of-day data means intraday stop
breaches are not seen. Early assignment and dividends are not modelled.
Buying power is an estimate. Results are history, not a forecast.

## Strike selection (per leg)

| method | value | meaning |
|---|---|---|
| `delta` | 0.01–0.99 | listed strike whose \|delta\| is closest to value; must be within `entry.delta_tolerance` |
| `atm` | (ignored) | listed strike closest to the underlying close |
| `pct_otm` | -50–50 | strike nearest spot × (1 − v/100) for puts, spot × (1 + v/100) for calls; negative = ITM |
| `offset` | -500–500 | dollars from the strike of leg `from_leg` (which must come earlier): the nearest listed strike strictly on that side, and within half the offset of the target, else the entry is skipped (`strike_not_found`). Far from the money strikes may be $5 apart, so a $5 wing can land $2–3 off; trades report the actual strikes |

Vendor delta is used when present, else Black-Scholes delta from the mid. Entry chains are
requested with 200 listed strikes either side of the money; with 40, near-term SPY ($1 strikes)
covered only about ±6% and cut off 16-delta shorts and their wings.

**Expiration choice.** Weeklies and dailies are listed only a few weeks ahead, monthlies months
ahead. When the nearest candidate has no chain yet, monthlies are tried next, and other
non-monthlies at least that far out are skipped (at most 6 tries; `no_chain` if none).

**Re-running.** The same request from the same user returns the same job, keyed on
`engine.ENGINE_VERSION`. Bump that version when a change would alter results.

## API

All routes require the `backtest` entitlement.

### `GET /api/backtests/presets`
```json
{"presets": [{"key": "iron_condor", "label": "Iron condor", "kind": "credit", "description": "...",
  "legs": [{"right": "put", "side": "sell", "ratio": 1, "strike": {"method": "delta", "value": 0.16}},
           {"right": "put", "side": "buy", "ratio": 1, "strike": {"method": "offset", "from_leg": 0, "value": -5}},
           {"right": "call", "side": "sell", "ratio": 1, "strike": {"method": "delta", "value": 0.16}},
           {"right": "call", "side": "buy", "ratio": 1, "strike": {"method": "offset", "from_leg": 2, "value": 5}}],
  "stock": null,
  "exit": {"profit_target_pct": 50, "stop_loss_pct": 200, "exit_dte": 21, "max_hold_days": null}}],
 "defaults": { ...a complete BacktestRequest... },
 "available": true, "blocked_by": []}
```
Preset keys: `short_put, cash_secured_put, covered_call, bull_put_spread, bear_call_spread,
bull_call_spread, bear_put_spread, iron_condor, iron_butterfly, short_strangle, short_straddle,
long_straddle, long_strangle, custom`. `available` is false (with reasons) when no history
provider is configured.

### BacktestRequest (body of POST /api/backtests and /api/backtests/estimate)
```json
{
  "underlying": "SPY",
  "start": "2025-01-02", "end": "2025-12-31",
  "strategy": "iron_condor",
  "legs": [ ...1-4 legs as above; omitted or [] => the preset's legs (required for custom)... ],
  "stock": null,
  "entry": {"dte_target": 45, "dte_min": 30, "dte_max": 60, "expirations": "any",
            "frequency": "weekly", "weekday": 0, "max_open": 3,
            "min_iv": null, "max_iv": null, "min_credit": null, "delta_tolerance": 0.05},
  "exit": {"profit_target_pct": 50, "stop_loss_pct": 200, "exit_dte": 21, "max_hold_days": null},
  "costs": {"commission_per_contract": 0.65, "fill": "conservative", "spread_fraction": 0.25,
            "assignment_fee": 0},
  "sizing": {"capital": 100000, "mode": "fixed", "contracts": 1, "risk_pct": 5}
}
```
- `stock`: `{"shares_per_contract": 100}` for covered calls, else null.
- `entry.expirations`: `any | fridays | monthly` (monthly = third Friday, or the Thursday before when Friday is a holiday).
- `entry.frequency`: `daily | weekly | monthly | when_flat`. `weekday` 0=Mon..4=Fri is used by weekly.
  Monthly = first trading day of each month.
- `entry.min_iv` / `max_iv`: decimal ATM IV of the chosen expiration on the entry day.
- `entry.min_credit`: minimum net credit per share. Credit strategies only.
- `exit.*`: any may be null (disabled).
  - **Credit trades:** profit target = % of the credit captured; stop = loss as % of the credit.
  - **Debit trades:** profit target = % gain on the debit; stop = % of the debit lost.
- `costs.fill`:
  - `conservative`: sell at the bid, buy at the ask.
  - `mid`.
  - `spread_fraction`: the mid moved against you by fraction × half the spread
    (0 = mid, 1 = the bid or ask; 0.25 default).
- `sizing.mode`:
  - `fixed`: `contracts` per entry.
  - `risk_pct`: floor(equity × risk_pct% / max loss per unit). For undefined-risk shorts the
    buying-power estimate stands in for max loss.
- Limits: underlying `^[A-Za-z.]{1,10}$`; end ≤ yesterday; start < end; span ≤ 5 years;
  dte_min ≤ dte_target ≤ dte_max ≤ 365; contracts 1–100; capital 1,000–100,000,000.
  Validation errors are 422 with FastAPI's `detail` list.

### `POST /api/backtests/estimate` → 200
```json
{"entry_days": 52, "est_requests": 260, "est_minutes": 2.5, "trading_days": 250}
```

### `POST /api/backtests` → 200
Returns the job (`id, kind, status, attempts, max_attempts, error, result, cancel_requested`)
plus `"estimate"`. The same request from the same user returns the same job (idempotent).
503 `backtest_unavailable` with `blocked_by` when no history provider is configured.

### `GET /api/backtests` → 200
The caller's 20 most recent backtests, newest first:
```json
{"backtests": [{"id": "...", "status": "succeeded", "created_at": "...", "underlying": "SPY",
  "strategy": "iron_condor", "start": "2025-01-02", "end": "2025-12-31",
  "summary": {"total_return": 0.08, "sharpe": 1.1, "max_drawdown": 0.05, "trades": 52} }]}
```
`summary` is null until the run succeeds.

### `GET /api/jobs/{id}` (existing) and `POST /api/jobs/{id}/cancel` (existing)
While a run is in progress, `result` is `{"progress": {"pct": 23.5, "day": "2025-03-04", "trades": 4,
"open": 2, "requests": 40}}`. Status values: `queued | running | succeeded | failed | cancelled | dead`.
On success `result` is a BacktestResult:
```json
{
  "config": { ...normalized BacktestRequest, legs filled in... },
  "summary": {"total_return": 0.12, "cagr": 0.11, "sharpe": 1.2, "sortino": 1.6, "max_drawdown": 0.08,
              "win_rate": 0.8, "cvar_95": 0.01, "trades": 52, "avg_pnl": 120.5, "median_pnl": 140.0,
              "best_trade": 410.0, "worst_trade": -880.0, "profit_factor": 2.1, "avg_days_held": 21.3,
              "final_equity": 112000.0, "time_in_market": 0.9, "max_buying_power": 12000.0,
              "total_commissions": 270.4},
  "benchmark": {"name": "Buy and hold SPY", "total_return": 0.15, "cagr": 0.14, "sharpe": 1.0,
                "max_drawdown": 0.12},
  "equity": [{"date": "2025-01-02", "equity": 100000.0, "benchmark": 100000.0, "drawdown": 0.0}],
  "monthly_returns": [{"month": "2025-01", "return": 0.012}],
  "trades": [{"id": 1, "entry_date": "2025-01-06", "exit_date": "2025-01-23", "expiration": "2025-02-21",
              "contracts": 1, "entry_value": 210.0, "exit_value": -95.0, "pnl": 112.4,
              "exit_reason": "profit_target", "days_held": 17,
              "underlying_entry": 590.1, "underlying_exit": 601.3,
              "legs": [{"right": "put", "side": "sell", "strike": 560.0, "ratio": 1,
                        "entry_price": 2.1, "exit_price": 0.95, "entry_delta": -0.16}]}],
  "exit_reasons": {"profit_target": 30, "stop_loss": 4, "exit_dte": 12, "expired": 5, "end_of_test": 1},
  "diagnostics": {"skipped_entries": {"no_expiration_in_window": 0, "no_chain": 1, "strike_not_found": 0,
                  "iv_filter": 5, "min_credit": 0, "max_open": 10, "insufficient_capital": 0},
                  "stale_marks": 4, "requests": 120, "cache_hits": 30,
                  "data_source": "ThetaData end-of-day (closing NBBO, vendor greeks)", "runtime_seconds": 95.2},
  "caveats": ["..."],
  "synthetic": false
}
```
Money values are USD for the whole position (×100 multiplier × contracts × ratio).
`entry_value` > 0 is a credit received, < 0 a debit paid; `exit_value` is the same sign
convention at the close. Every `trades[].pnl` already includes commissions and fees.
`drawdown` is a positive fraction from the running peak. `exit_reason` is one of
`profit_target | stop_loss | exit_dte | max_hold | expired | end_of_test`.

## Running jobs

Backtests are `backtest` jobs on the durable queue. `python -m app.worker` runs them.
On a single free web service, set `EMBEDDED_WORKER=1` to run the same worker loop
inside the web process instead.
