# OptionsCoach architecture and decisions

Status as of 2026-11-08, branch `feat/columbia-exit` (base `762fe0d`). Each entry separates **facts**
(verified in code, tests or official docs), **assumptions** (reversible defaults chosen here) and
**unresolved choices** (owner decisions not yet made).

## ADR-001 (P1): modular analytics inside the OptionsCoach repo; separate from MarketMosaic

**Decision.** Options analytics (IV, greeks, HV, IV rank, skew, surfaces, screener, backtest contract)
live as modules inside this repo (`app/analytics`, `app/backtest`, `app/jobs`). OptionsCoach stays a
separate deployable service from MarketMosaic: its own process, database, release lifecycle and
Render blueprint.

**Why.**
- The analytics are numpy-vectorized and fast enough in-process for one expiry (≈21–200 contracts).
  The only work that does not belong in a web request is multi-day ingestion and backtesting, which
  go through the durable job queue to a separate worker entrypoint (`python -m app.worker`) of the same
  image. While production is a single free web service, `EMBEDDED_WORKER=1` runs that same loop inside
  the web process (2026-11-09); jobs stay durable rows, so a worker service can take over unchanged.
  No runtime evidence justifies a separate analytics service.
- MarketMosaic's web/worker split exists because of memory-profile incidents (MarketMosaic memory:
  worker OOM on the 512 MB plan). Co-hosting OptionsCoach would re-couple those profiles.
- Integration is by narrow, versioned HTTP contracts (`docs/API_CONTRACTS.md`), never shared database
  credentials or merged schemas.

**Revisit if:** a worker is approved and ingestion volume or backtest CPU shows contention with web
requests (then raise worker plan, not split the codebase).

## Hosting (A)

- **Fact:** the old deployment was Cloud Run (`options-chatbot`, region `us-central1`) in a Columbia
  account (`jc6640@columbia.edu`), with Vertex Gemini as the model. See `docs/GOOGLE_EXIT.md`.
- **Fact:** Render injects `PORT` (default 10000) and requires binding `0.0.0.0`; health checks gate
  zero-downtime deploys (≤5 s per check, 15 min deploy window). The Dockerfile and `render.yaml` follow
  this. Private networking requires the same region and workspace; fixed outbound IPs require a Pro
  workspace. (Render docs, fetched 2026-11-08.)
- **Assumption:** staging first at `options-staging.marketmosaic.ai`, region `oregon`, smallest paid
  plan, autodeploy off. All marked UNRESOLVED in `render.yaml`.
- **Unresolved:** final plan, region (must match MarketMosaic for private networking), cost, whether
  the worker is provisioned at all.

## Model provider (B)

- **Fact (Anthropic API reference cached 2026-10-06):** `claude-haiku-5-5` costs $0.10 / $0.50 per
  MTok (prompts ≤100K tokens); `claude-sonnet-5-5` costs $2 / $10. SDK `anthropic` 2.6.0 sends API
  version `2026-10-21`, which rejects `temperature`/`top_p`/`top_k`. Haiku 5.5's effort default is
  `medium`; we set `low` explicitly. The old adapter's `claude-sonnet-4-20250514` was retired
  2026-06-15 and would fail.
- **Decision:** Haiku 5.5 primary. Sonnet 5.5 is an opt-in escalation (`LLM_ESCALATION_ENABLED`)
  used only when Haiku's answer fails numerical validation against the deterministic calculator.
  The heuristic provider exists only as a labelled demo mode or an explicitly configured, bounded
  fallback while the circuit breaker is open. No OpenAI path.
- **Not claimed:** answer quality. No paid evaluation has been run (`eval/run_eval.py --allow-paid`
  is the separately budgeted step).
- **Unverified until staging:** that the live API accepts our strict tool schemas (pattern,
  minItems/maxItems, optional properties) — first item of real-provider acceptance.

## Identity and entitlements (C)

- **Fact (MarketMosaic `origin/main` a302ea4):** Clerk session JWTs, RS256, exact `iss`, `azp`
  allowlist (skipped when empty), 60 s leeway, JWKS cache failing closed; template `marketmosaic`;
  entitlements = `users.account_state`, `beta_access` status, `PlanState`.
- **Decision:** OptionsCoach verifies the same tokens the same way, with `azp` *required* and a
  non-empty allowlist enforced at startup in staging/production, 10 s leeway. Entitlements are
  fetched from MarketMosaic's new `mm.entitlements.v1` endpoint using the user's own token, cached
  ≤60 s (the revocation bound), failing closed. Local users are keyed by Clerk `sub`, never email.
- **Fact (Clerk docs):** a subdomain of MarketMosaic's root domain shares the session with no extra
  setup; a different registrable domain (`optionscoach.ai`) needs a satellite domain, a paid Clerk
  feature, with sign-in on the primary domain.
- **Unresolved:** whether `optionscoach.ai` becomes a Clerk satellite or a redirect to
  `options.marketmosaic.ai`. MarketMosaic must add the OptionsCoach origin to its own
  `CLERK_AUTHORIZED_PARTIES` before forwarded tokens verify there.

## State (C)

- Postgres via Alembic (`migrations/versions/0001_initial_schema.py`), additive, owner-scoped.
  Verified: upgrade → `alembic check` (no drift) → downgrade → upgrade on Postgres 16 and SQLite.
- Retention: conversations purged after `RETENTION_DAYS_CONVERSATIONS` (default 365) by
  `repo.purge_expired_conversations` (to be scheduled on the worker); export `GET /api/me/export`;
  deletion `DELETE /api/me/data` (hard delete + tombstoned user row).
- Old anonymous in-memory sessions are not migrated or adopted; client `session_id` is ignored.

## Data (D, P2, P3)

See `docs/PROVIDERS.md`. Market data is labelled with the provider's status (`live | delayed | eod |
synthetic`) everywhere; nothing is called real-time unless configured as `live`. Tradier defaults to
`delayed` until the entitlement is confirmed. Synthetic data cannot enter the market-data tables.

## UI (P8)

The historical plan said Streamlit; the reviewed app is React/Vite. Decision: cohesive React modules
in the existing SPA (Chains, Builder & payoff, Compare, Analytics, Screener, Backtests tabs) so auth,
styling and deployment stay single. No Streamlit app exists to maintain; nothing was dropped.
