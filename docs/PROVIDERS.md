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
| FMP | none confirmed | — | — | — | — |
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
