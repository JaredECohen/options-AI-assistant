# backtesting-engine

A clear, end-of-day, **per-bar event-driven** backtesting engine for equities and
options. Built for readability first: the loop is easy to follow, the cost model
is deliberately conservative, and every public class/function is documented.

> **Note on packaging.** This engine is developed inside the OptionsCoach.AI
> monorepo (see [`../BLOCKERS.md`](../BLOCKERS.md) for why a standalone GitHub
> repo could not be created from the build environment). It is a fully
> self-contained package — own `pyproject.toml`, `src/` layout, tests, CI — and
> can be split into its own repo with `git subtree split` at any time with zero
> code changes. Once that repo exists, install it with:
>
> ```bash
> pip install git+https://github.com/JaredECohen/backtesting-engine
> ```
>
> Until then, install it locally (editable):
>
> ```bash
> pip install -e ./backtesting-engine
> ```

## Features

- **Equity engine** — bar-based, market & limit orders, cash account.
- **Options engine** — multi-leg positions, Black-Scholes greeks at fill,
  expiration settlement (assign ITM / expire OTM), simplified Reg-T warnings.
- **Conservative fills** — adverse side of the spread + one tick slippage +
  commissions ($0.65/contract options, $0.005/share equities).
- **Pluggable data** via a `DataSource` protocol with three adapters:
  - `FMPEquityAdapter` (live equity OHLCV),
  - `AlphaVantageOptionsAdapter` (live option chains),
  - `SyntheticOptionsAdapter` (deterministic Black-Scholes generator, used by all
    unit tests so CI needs no network or credentials).
- **Mandatory rate limiting + caching** for live adapters: in-process token
  bucket + on-disk Parquet cache under `~/.cache/backtesting-engine/`. Cache hits
  bypass the limiter.
- **Metrics** — total return, CAGR, Sharpe, Sortino, max drawdown, Calmar, win
  rate, avg win/loss, profit factor, CVaR(5%), per-trade P&L distribution.

## Quickstart

A 20-line SPY 20/50 moving-average crossover that runs as-is:

```python
from datetime import date

from backtesting_engine import (
    Backtest, Instrument, Order, Side, Strategy,
    StandardEquityCostModel, StandardOptionsCostModel, SyntheticOptionsAdapter,
)


class MaCross(Strategy):
    def on_bar(self, bar, portfolio):
        df = self.history  # appended below
        self.history.append(bar.price("SPY"))
        if len(self.history) < 50:
            return []
        fast = sum(self.history[-20:]) / 20
        slow = sum(self.history[-50:]) / 50
        pos = portfolio.position(Instrument.equity("SPY")).quantity
        if fast > slow and pos == 0:
            return [Order(Instrument.equity("SPY"), 100, Side.BUY)]
        if fast < slow and pos > 0:
            return [Order(Instrument.equity("SPY"), pos, Side.SELL)]
        return []

    def on_start(self, portfolio):
        self.history = []


bt = Backtest(MaCross(), SyntheticOptionsAdapter(start_price=400),
              StandardEquityCostModel(), StandardOptionsCostModel(),
              equity_symbols=["SPY"])
result = bt.run(date(2020, 1, 1), date(2022, 1, 1))
print(result.summary())
```

See [`examples/short_put_spy.py`](examples/short_put_spy.py) for a full options
strategy (rolling 30-DTE short put on SPY).

## Install for development

```bash
cd backtesting-engine
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
```

## Run checks

```bash
ruff check src tests
ruff format --check src tests
mypy
pytest --cov=backtesting_engine --cov-report=term-missing tests
```

## Live data

Set the relevant environment variables; tests never require them.

```bash
export FMP_API_KEY=...            # FMPEquityAdapter
export ALPHA_VANTAGE_API_KEY=...  # AlphaVantageOptionsAdapter
```

Always wrap a live adapter in `CachedDataSource` so re-runs are cheap:

```python
from backtesting_engine import CachedDataSource, FMPEquityAdapter
source = CachedDataSource(FMPEquityAdapter(), namespace="fmp")
```

## License

MIT — see [LICENSE](LICENSE).
