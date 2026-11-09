# Market-data providers (P2)

Research date 2026-11-08, public official pages only (no sign-ups, no API calls). Facts carry their
source; anything not confirmed on an official page is marked UNCONFIRMED.

## Reconciling the conflicting records

- **Code (fact):** Tradier adapter (`app/providers/options/tradier.py`) plus a labelled synthetic
  mock. The old factory silently fell back to mock when the token was missing; removed.
- **Notion "resolved: FMP equities + Alpha Vantage historical options":** Alpha Vantage historical
  options is real (15+ years of full chains with IV and greeks, premium plans from $49.99/mo,
  alphavantage.co/documentation, /premium). **FMP options coverage could not be confirmed** on any
  official page. The note is therefore only half-supported, and both are personal-use plans.
- **Notion "evaluate ORATS, Polygon, Cboe DataShop, IBKR":** evaluated below.

## Comparison

| Provider | Cheapest options plan | Live? | Greeks/IV | Options history | Display to other users |
| --- | --- | --- | --- | --- | --- |
| Tradier | free with brokerage account | real-time (production), 15-min delayed (sandbox) | yes (source not named) | none for expired contracts | **personal use only; public apps need Tradier Partner status** (docs.tradier.com/docs/getting-started) |
| Alpha Vantage | $49.99 historical; $199.99 realtime | unclear | yes | 15+ yrs (2008+) | "personal use" per premium page; ToS unparsed |
| FMP | none confirmed (docs block automated reads; site search finds no options-chain endpoint, 2026-11-08) | — | — | — | — |
| ThetaData (v3) | Options Value $40, Standard $80, Pro $160 /mo | real-time on paid tiers | IV + first-order greeks on Standard+ (no gamma below Pro); IV from national bid/mid/ask | 6 / 10 / 14 yrs; EOD greeks history on Standard+ | **Individual: personal use only, no redistribution, display or business use; Business plan for commercial** (thetadata.net/pricing, /commercial-use, terms §1.1, §2.1) |
| ORATS | $199 delayed; $299 live | delayed / live | yes (own model, SMV) | EOD since 2007 | derived values "internal use only" |
| Massive (Polygon) | $29 delayed; $199 real-time | delayed / RT | yes (Starter+) | 2–5+ yrs | individual plans; business pricing custom |
| Cboe DataShop | unpublished | EOD files only | optional add-on | since 2012 | UNCONFIRMED |
| IBKR | ~$1.50/mo non-pro OPRA | RT with subscription | UNCONFIRMED | UNCONFIRMED | per-username licence; unsuitable for multi-user display |

Sources: docs.tradier.com/docs/market-data, /getting-started; alphavantage.co/documentation,
/premium; orats.com/data-api; massive.com/options, /business; datashop.cboe.com product pages;
interactivebrokers.com market-data pricing pages.

## Decision (reversible) and what is blocked

1. **Adapter shape:** keep the provider protocol (`OptionsDataProvider`: quote, expirations, chain,
   option quote, daily bars) with explicit `data_status`; every adapter must return quote
   timestamps, multipliers and sourced greeks, or `None`.
2. **Development and staging:** Tradier, labelled `delayed` by configuration even though Tradier
   production data is real-time, because nothing may be labelled real-time until the entitlement
   and display rights are confirmed in writing. Under-claiming is the safe failure.
3. **BLOCKED for production display to beta users:** no provider's public plan grants display
   rights to third-party users. Before production, obtain written terms from at least one of
   Tradier (Partner), Massive (business), or ORATS. This is the gating external dependency for T1,
   and for P3/P7 history.
4. **History for IV rank, backtests (P3/P7):** the candidates with real history are Alpha Vantage
   historical options ($49.99/mo, 15 yrs, IV/greeks) and ORATS ($199/mo, EOD since 2007, IV rank).
   Choose after the display-rights answer; the ingestion pipeline is provider-agnostic.
5. **Underlying daily bars (HV):** Tradier `markets/history` is implemented; ORATS has none (needs a
   second source).

## Unresolved owner choices

- Real-time vs end-of-day for v0 (EOD is cheaper and enough for analytics; chains benefit from live).
- Which vendor to approach for display rights, and budget.
- Whether the beta stays "personal use" (owner only) until licensing is in place.

## ThetaData adapter (2026-11-08)

Implemented in `app/providers/options/thetadata.py` from the v3 docs, the OpenAPI file and the source of
ThetaData's Python library (`thetadata` 1.0.17). It is tested only against fixtures: library dataframes and REST
shapes in `tests/test_thetadata_provider.py`.

- **Default transport (`THETADATA_ACCESS=library`):** the official library signs in with email/password (or an API key) at
  `nexus-api.thetadata.us` and fetches over gRPC from `mdds-01.thetadata.us:443`. No Terminal is needed. The library's
  logger prints the sign-in response, including the session id, at INFO, so the adapter holds that logger at WARNING.
- **`THETADATA_ACCESS=terminal`:** the local Theta Terminal REST server.
- **First live run:** `scripts/thetadata_smoke.py` (RUNBOOK §7).

- **Live:** `OPTIONS_PROVIDER=thetadata`. Chains use `snapshot/greeks/first_order` (Standard), falling back
  to `snapshot/quote` without greeks on Value; open interest and volume come from two more Value-tier
  calls; the underlying comes from `stock/snapshot/quote`, or from greeks rows when the plan has no stock data.
- **History:** `OPTIONS_HISTORY_PROVIDER=thetadata` enables `app/jobs/iv_backfill.py`. That job rebuilds a year of
  daily 30-day ATM IV from `option/history/greeks/eod` (Standard; falls back to `option/history/eod`), using the
  same model-IV method as daily ingestion, so IV rank and IV percentile become available right away.
  It costs about one request per expiry that brackets 30 days (roughly 60 to 80 per ticker-year for weeklies).
- **Licence:** suitable while the owner is the only user. Anyone else seeing ThetaData-derived values needs the Business plan.
- **Terminal-only unknowns** (not documented by ThetaData): which config key binds the Terminal to a non-local host, its
  memory needs, and whether it accepts more than one client IP (error 476). None of these apply to the library transport.
