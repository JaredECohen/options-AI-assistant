# API contracts

All contracts are versioned in the payload (`contract`) and are additive-only within a version.
A breaking change means a new version string and a period where both are served.

## Inbound to OptionsCoach (MarketMosaic → OptionsCoach)

Auth for every `/api/v1/contracts/*` call: **both**
- `X-Service-Token: <token>`; OptionsCoach stores only its SHA-256 in `OC_SERVICE_TOKEN_HASHES_JSON`
  (`{"marketmosaic": "<hex>"}`), compared in constant time;
- `Authorization: Bearer <end user's Clerk token>`; OptionsCoach verifies it and applies its own
  entitlements (`market_data` / `payoff`).

No model calls, no ingestion, no paid fetch beyond the configured provider's quote/chain reads.

### `oc.options_context.v1`, `GET /api/v1/contracts/options-context`
Query: `ticker` (`^[A-Za-z.]{1,10}$`), `expiration` (`YYYY-MM-DD`), `page` (1–100), `page_size` (1–60 strikes).
```
{ "contract": "oc.options_context.v1", "generated_at": ISO8601, "caller": "marketmosaic",
  "ticker": "SPY", "expiration": "2026-12-18", "underlying": {"price": 600.0, "currency": "USD"},
  "provider": "tradier", "data_status": "live|delayed|eod|synthetic", "data_status_label": "...", "currency": "USD",
  "freshness": {"as_of": ISO8601|null, "age_seconds": int|null, "stale": bool|null, "stale_after_seconds": int|null},
  "freshness_policy": {"stale_after_seconds": {"live":120,"delayed":1800,"eod":345600,"synthetic":null}},
  "conventions": {model, rate/dividend assumptions and sources, day count, units, timezone, multiplier},
  "page", "page_size", "total_strikes", "has_more",
  "contracts": [{ "contract_id": OCC symbol, "option_type", "strike", "expiration", "bid", "ask", "mid", "last",
      "volume", "open_interest", "quote_time": ISO8601+offset, "multiplier": int, "adjusted_deliverable": bool,
      "iv": {"provider": {"value","source"}|null, "model": {"value","model"}|null, "unavailable_reason"},
      "greeks": {"provider": {...,"source","as_of"}|null, "model": {delta,gamma,theta,vega,rho}|null} }] }
```
Unsupported greeks are `null`, never filled. Errors: 401 service/user auth, 403/402 entitlement,
404 quote, 422 input, 503 `market_data_unavailable`.

### `oc.payoff.v1`, `POST /api/v1/contracts/payoff`
Body: `{"ticker", "legs": [OptionLeg|StockLeg] (1–8)}`. Returns the exact bounds block
(`max_profit`, `max_loss` as number or `"unlimited"`, `bounds.*.{unbounded,value,at_price}`,
`breakevens`, `premiums_used` with provenance, `conventions`), with no curve. 422 codes:
`mixed_expirations`, `premium_missing`, `quote_unavailable`, `quote_crossed`, `quote_one_sided`.

Consumer: `backend/app/integrations/optionscoach.py` on MarketMosaic branch
`feat/optionscoach-contracts` (validates the contract, drops unknown fields, forwards the user token).

## Outbound from OptionsCoach (OptionsCoach → MarketMosaic)

Auth: the end user's Clerk bearer **and** `X-Service-Token = OPTIONSCOACH_SERVICE_TOKEN` (MarketMosaic
setting; unset ⇒ 503). MarketMosaic's normal wall (beta, plan, suspension, `memo_view` meter) applies.

### `mm.entitlements.v1`, `GET /api/v1/entitlements/me`
`{"contract", "external_id" (must equal token sub), "account_state", "beta_status", "plan": "none|free|pro", "plan_source"}`.
Beta-exempt so a pending user learns why access is refused. A suspended account answers 403
`{"detail":{"code":"account_suspended"}}`, which OptionsCoach maps to "suspended" (not an outage).

### `mm.research_memo_index.v1`, `GET /api/v1/research/{ticker}/memos?limit=1..20`
`{"contract", "ticker", "versions": [{"version", "generated_at", "trigger"}]}`. Live stored versions only
(backtest snapshots excluded), no memo content.

### `mm.research_memo.v1`, `GET /api/v1/research/{ticker}/memos/{version}`
Exact stored version only (no "latest", no generation, no refresh, no enqueue). Metered as `memo_view`.
```
{ "contract", "ticker", "version", "generated_at", "as_of", "trigger", "rating_label",
  "confidence": {"score", "available", "caps": [...]}, "summary" (≤4000 chars, untrusted text),
  "citations": [{"title","source","url","date"}] (≤30),
  "review": {"status","verdict","open_material_issues","open_minor_issues"} | null,
  "freshness": {"stale": bool|null, "reason"}, "sections_unavailable": [...] }
```
OptionsCoach client: `app/integrations/marketmosaic.py` validates the contract, bounds sizes (256 KB),
caches per (user, ticker, version) for 5 minutes, and marks research text `untrusted_text`.

## Deferred consumer contract

`oc.course_context.v1` (T8): `GET /api/course-context?q=` returns ≤3 attributed snippets from
rights-cleared materials. Blocked: no materials or rights were available.
