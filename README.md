# OptionsCoach.AI

Options education and analytics: a strategy coach (chat), options chains, a position builder with
exact expiration payoff, cross-ticker IV comparison, volatility analytics and surfaces, and a
screener. Educational only, not investment advice.

The original version ran on Columbia-owned Cloud Run with Vertex. This branch moves it to
personally owned infrastructure: Render, direct Anthropic models, MarketMosaic's Clerk identity and
entitlements, and its own Postgres. See `docs/` for decisions, contracts and the release runbook.

## Run locally (no accounts needed)

```bash
cd /Users/jaredcohen/code/oc-columbia-exit && uv sync --frozen
cd /Users/jaredcohen/code/oc-columbia-exit/frontend && npm ci && npm run build
cd /Users/jaredcohen/code/oc-columbia-exit && cp -n .env.example .env && .venv/bin/uvicorn app.main:app_factory --factory --port 8000
```

Open http://localhost:8000. Development mode uses **synthetic market data** and **canned answers**,
both labelled in the UI. For the Vite dev server, run `npm run dev` in `frontend/` (proxies `/api`
to port 8000).

## Tests

```bash
cd /Users/jaredcohen/code/oc-columbia-exit && .venv/bin/python -m pytest -q
cd /Users/jaredcohen/code/oc-columbia-exit/frontend && npm test
cd /Users/jaredcohen/code/oc-columbia-exit && .venv/bin/python eval/run_eval.py
```

Tests never touch the network (a socket guard enforces it) or paid APIs. The eval runs offline by
default; `--generator anthropic` / `--judge claude` spend money and require `--allow-paid`.

## Layout

| Path | What |
| --- | --- |
| `app/main.py` | FastAPI app factory (`uvicorn app.main:app_factory --factory`) |
| `app/config.py` | Every environment variable (docs: `docs/ENV_VARS.md`) and startup safety checks |
| `app/auth/` | Clerk token verification and MarketMosaic entitlements |
| `app/chat/` | Chat pipeline (split from the old 3,100-line `chat_service.py`) |
| `app/core/payoff.py` | Exact expiration payoff, bounds and breakevens |
| `app/analytics/` | BSM/IV/greeks, HV and IV rank, skew, surfaces, comparison, screener |
| `app/llm/` | Anthropic gateway, budgets, tools, validation |
| `app/db/`, `migrations/` | Own Postgres schema (Alembic) |
| `app/jobs/`, `app/worker.py` | Durable jobs and ingestion (worker entrypoint) |
| `app/backtest/` | Backtest contract and metrics (engine blocked) |
| `app/integrations/` | MarketMosaic research client, course context |
| `frontend/` | React/Vite SPA |
| `eval/` | Golden-case eval |

## Docs

- `docs/ARCHITECTURE.md`: decisions, facts vs assumptions vs open choices
- `docs/API_CONTRACTS.md`: versioned contracts with MarketMosaic
- `docs/PROVIDERS.md`: market-data provider evaluation and licensing blocker
- `docs/GOOGLE_EXIT.md`: Columbia/Google inventory and decommission
- `docs/RUNBOOK.md`: staging, cutover, rollback, retirement
- `docs/TRACEABILITY.md`: backlog items to code and tests
- `docs/ENV_VARS.md`: environment variable names
