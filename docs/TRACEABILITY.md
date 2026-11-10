# Scope-to-test ledger

Status vocabulary: **implemented** (code merged on the branch), **verified** (automated tests or a
recorded local run prove it), **partial**, **blocked** (an external dependency or decision prevents
completion), **already satisfied**. Nothing here is DEPLOYED or PRODUCTION ACCEPTED. Original Notion
statuses are unchanged (Notion was not edited).

| ID | Item (Notion status left as-is) | Status | Evidence | Remaining / blocker |
| --- | --- | --- | --- | --- |
| T1 | Real-time options chains in UI | implemented, verified locally | `GET /api/chain` (paging, side filter, units, freshness, provenance); `ChainViewer.jsx` (abort on supersede, loading/empty/error/stale); tests `test_chain_*`, `test_freshness_*`, frontend `T1 chain viewer` (3) | Live feed acceptance; **display licence** (docs/PROVIDERS.md); never labelled real-time unless `TRADIER_DATA_STATUS=live` |
| T2 | Package builder, editable legs | implemented, verified locally | `Builder.jsx` templates, add/remove/reorder, validation, mixed-expiry guard, save to user-scoped `/api/positions`; frontend tests (3); `test_positions_and_screens_are_owned`; browser run 2026-11-08 | — |
| T3 | Payoff with real premiums | implemented, verified locally | `resolve_premiums` (mid/conservative fills, contract + quote time + provider + data status recorded, crossed/one-sided/missing refused); chat path no longer assumes zero premiums; tests `test_premium_from_quote_*`, `test_resolve_premiums_*`, `test_payoff_endpoint_*`, `test_chat_payoff_never_assumes_zero_premium` | Real-provider acceptance |
| T4 | Cross-ticker pricing / IV comparison | implemented, verified locally | `app/analytics/compare.py` (30-day CM ATM IV, exclusion reasons for currency/status/as-of/unavailable, deterministic sort, no mispricing claims); `POST /api/compare`; `Compare.jsx`; test `test_compare_*` | — |
| T5 | Chart-bounded max-loss bug | **fixed and verified locally** | Reproduced: old grid started at 50 → $4,500. Analytic bounds (S=0, strikes, upper slope); `test_payoff.py` (19 cases incl. naked call, spreads, condor, ratio, multipliers, exact breakevens, chart-range invariance, strict JSON, mixed expiry); browser shows $9,500 at $0 | Deployed exposure **unverified** (no production calls permitted) |
| T6 | Restore + migrate off Columbia GCP/Vertex | partial | Vertex code/dep removed; Anthropic gateway; Dockerfile (locked); render.yaml; health vs readiness; `docs/GOOGLE_EXIT.md`, `docs/RUNBOOK.md`; container boot (see receipts) | Deploy, paid acceptance, DNS cutover, Columbia retirement all need approval; GCP project ID/billing unknown |
| T7 | Split chat_service.py | implemented, verified | 3,101-line file → `app/chat/*` (8 modules, acyclic) by AST move; 96 characterization cases byte-identical (`test_chat_characterization.py`) | — |
| T8 | Options-class context | blocked (OptionsCoach side implemented) | `app/integrations/course_context.py`: manifest with per-file rights, ≤3 attributed snippets, untrusted marking; `/api/course-context`; test `test_course_context_blocked_without_cleared_rights` | **No materials or rights available.** QuantX / trading dashboard not touched; export contract `oc.course_context.v1` documented |
| P1 | Architecture decision | implemented | `docs/ARCHITECTURE.md` ADR-001 | — |
| P2 | Provider selection | partial | `docs/PROVIDERS.md`; portable provider protocol; Tradier and ThetaData v3 adapters (fixtures only, `tests/test_thetadata_provider.py`) | Display licence (owner); ThetaData subscription + live smoke check (`scripts/thetadata_smoke.py`) |
| P3 | Ingestion / storage | implemented, verified locally | tables with provenance + natural-key dedupe (migration 0001); `app/jobs/ingest.py` (bounded, idempotent, partial recovery, refuses synthetic); worker; tests `test_ingest_*`, `test_worker_*`, migration on Postgres 16 | Production import needs approval + provider; worker not provisioned |
| P4 | Analytics (IV, HV, rank, greeks, skew, term) | implemented, verified | `app/analytics/bsm.py`, `vol.py`, `chain.py`, `snapshot.py`; Hull reference tests, parity, IV round-trip, unidentifiable-IV reporting, no-lookahead slicing | IV rank needs ≥~200 stored observations: a one-year ThetaData backfill (`app/jobs/iv_backfill.py`, `tests/test_iv_backfill.py`) supplies them once subscribed |
| P5 | Surfaces | implemented, verified | `app/analytics/surface.py` (observed vs fitted, no extrapolation, dislocations, not-arbitrage-free disclaimer); `Analytics.jsx`; test `test_surface_*` | SVI/SABR deliberately out of scope |
| P6 | Screener | implemented, verified | DSL v1 with strict validation, presets, keyset paging, saved screens; `test_screener.py` (golden presets, 11 rejection cases, pagination) | Live screens capped at 10 tickers until stored snapshots exist |
| P7 | Backtester | verified (hand-checked tests + live ThetaData runs, 2026-11-09) | `app/backtest/` (`spec.py` request and 14 presets, `engine.py` multi-leg daily simulation, `data.py` cached history, `metrics.py`), `app/api/routes_backtest.py`, job kind `backtest` (worker or `EMBEDDED_WORKER`); `tests/test_backtest_engine.py`; contract `docs/BACKTESTING.md` | Built in-house on ThetaData end-of-day history (the shared engine repo was never found). Individual ThetaData plans are personal-use only. Limits: end-of-day exits, no early assignment or dividends |
| P8 | UI integration | implemented, verified locally | React tabs (Coach, Chains, Builder, Compare, Analytics, Screener, Backtests), ARIA tabs, labelled statuses; Streamlit reconciliation in ARCHITECTURE.md; 11 frontend tests; browser check | Visual design review not done |
| P9 | Chat tools | implemented, verified with scripted model | `app/llm/tools.py` (4 strict tools, per-tool entitlement re-check, provenance, untrusted research), validator + Sonnet escalation; tests in `test_llm_gateway.py` | Live API acceptance of strict schemas |
| P10 | Deterministic + golden evals | implemented, verified | Free eval 29/29 (heuristic generator, deterministic judge); paid paths need `--allow-paid`; judge failure = INCONCLUSIVE (exit 2) | Paid eval not run (needs budget approval) |

## Cross-cutting requirements

| Requirement | Status | Evidence |
| --- | --- | --- |
| Auth: missing/forged/expired/nbf/issuer/azp/alg/unknown-kid | verified | `test_bad_tokens_are_rejected` (7), `test_forged_signature_rejected`, query-token ignored |
| JWKS failure fails closed | verified | `test_jwks_unavailable_fails_closed` |
| Beta/plan/suspension/revocation | verified | `test_entitlement_denials`, `test_plan_gate_on_backtests`, MarketMosaic cache TTL + subject mismatch + suspended mapping tests |
| Cross-user isolation (sessions, positions, screens, jobs, export, research cache) | verified | `test_conversations_are_owned`, `test_positions_and_screens_are_owned`, `test_jobs_are_owned`, research client per-user cache test |
| No anonymous-session takeover | verified | `test_client_session_id_is_not_trusted` |
| Durable state across restarts | verified | `test_history_is_durable_across_app_restarts`; Postgres migration up/down/check |
| Model failures: 429/5xx/timeout/refusal/truncation/budget/rate/circuit/fallback/attribution | verified (scripted transport) | `test_llm_gateway.py` (11) |
| Jobs: idempotency, claim, retry/backoff, dead letter, lease recovery, cancellation, ownership | verified | `test_jobs_ingest_backtest.py` |
| Contracts both directions, bounded, versioned, fixture-tested | verified | OptionsCoach `test_contracts_research_config.py`; MarketMosaic `test_v1_optionscoach.py` (13) |
| Config refuses unsafe production settings | verified | `test_production_config_refuses_unsafe_settings` (8) |
| No network in tests | enforced | `tests/conftest.py` socket guard |
