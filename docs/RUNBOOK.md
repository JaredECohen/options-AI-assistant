# Staging, cutover, rollback and Columbia retirement runbook

Every step that creates a paid resource, changes DNS, changes account/auth settings, applies a
production migration or deletes Columbia resources needs the owner's explicit approval at that
step. Nothing below has been executed. Commands assume a POSIX shell and absolute paths.

## 0. Preconditions (code)

```bash
cd /Users/jaredcohen/code/options-AI-assistant && git log --oneline -5
cd /Users/jaredcohen/code/options-AI-assistant && uv sync --frozen && APP_ENV=test .venv/bin/python -m pytest -q
cd /Users/jaredcohen/code/options-AI-assistant/frontend && npm ci && npm test && npm run build
```
Review and merge `feat/columbia-exit` (OptionsCoach) and `feat/optionscoach-contracts`
(MarketMosaic) through normal PRs. MarketMosaic ships first: OptionsCoach's staging needs its
`/api/v1/entitlements/me`.

## 1. Fresh personal accounts and keys (owner)

1. Anthropic Console (personal): create a workspace for OptionsCoach, set a monthly spend limit,
   create `ANTHROPIC_API_KEY`. Do not reuse any Columbia or MarketMosaic key.
2. Market data: open the chosen provider account (see `docs/PROVIDERS.md`); get the entitlement for
   the data status you will label (`TRADIER_DATA_STATUS`) **in writing**, including display rights.
3. Generate the service tokens (never commit):
   ```bash
   python3 -c "import secrets;print(secrets.token_urlsafe(48))"   # OPTIONSCOACH_SERVICE_TOKEN (MarketMosaic side)
   python3 -c "import secrets;print(secrets.token_urlsafe(48))"   # MarketMosaic -> OptionsCoach token
   python3 -c "import hashlib,sys;print(hashlib.sha256(sys.argv[1].encode()).hexdigest())" '<the second token>'
   ```
   OptionsCoach stores only the SHA-256 (`OC_SERVICE_TOKEN_HASHES_JSON={"marketmosaic":"<hex>"}`).

## 2. Clerk and domains (owner; account settings)

1. In MarketMosaic's Clerk production instance confirm the root domain is `marketmosaic.ai`, so
   `options.marketmosaic.ai` / `options-staging.marketmosaic.ai` share the session without satellites.
2. Add `https://options-staging.marketmosaic.ai` (later `https://options.marketmosaic.ai`) to
   MarketMosaic's `CLERK_AUTHORIZED_PARTIES`, or MarketMosaic will reject forwarded tokens.
3. Restrict the Frontend API's allowed origins to the exact subdomains (Clerk recommends an allowlist).
4. `optionscoach.ai` later: either a Clerk satellite domain (paid feature; sign-in stays on the primary)
   or a 301 redirect to `options.marketmosaic.ai`. Owner decision.

## 3. Render staging (approval: creates paid resources)

1. Apply `render.yaml` as a new Blueprint in the personal Render workspace. Resolve the UNRESOLVED
   plan/region values first. Region must equal MarketMosaic's for private networking.
2. Fill the `sync: false` secrets in the dashboard. Set `MARKETMOSAIC_BASE_URL` to MarketMosaic web's
   internal `http://<internal-host>:10000`.
3. On MarketMosaic: set `OPTIONSCOACH_SERVICE_TOKEN` (web service) and deploy the contracts branch.
4. First deploy runs `alembic upgrade head` (pre-deploy) on OptionsCoach's own new database.
   Backups: enable Render's daily backups on `optionscoach-db-staging` before any user data exists.
5. Add the custom domain `options-staging.marketmosaic.ai` (CNAME to the service's `onrender.com`
   host in Cloudflare; TLS is automatic; remove AAAA records; CAA must allow letsencrypt.org/pki.goog).

### Staging acceptance (separately budgeted, real providers)
- `GET /health` 200; `GET /ready` 200 with `migrations: ok`.
- Sign in through MarketMosaic's Clerk; beta-approved user reaches chat; pending user sees the
  beta message; Pro-only backtests return 402 for Free.
- Ten fixed prompts through Haiku 5.5: check attribution, usage rows, cost estimate per turn, refusal
  and truncation handling, and that **strict tool schemas are accepted** by the live API.
- Chain for SPY/QQQ: data-status label matches the written entitlement; quote times sensible.
- T5 example via the UI: max loss $9,500 at $0.
- MarketMosaic research read for a ticker with a stored memo: version, freshness and review shown.
- Budget: set `LLM_GLOBAL_DAILY_TOKEN_BUDGET` low (e.g. 200k) during acceptance.

## 4. Production and DNS cutover (approval)

1. Duplicate the staging service as `optionscoach` (APP_ENV=production, production domains).
2. Lower the TTL on the `optionscoach.ai` records in Cloudflare to 300 s a day ahead.
3. Point `options.marketmosaic.ai` (CNAME) at the Render service; verify TLS and sign-in.
4. For `optionscoach.ai`: replace the Google A records / `ghs.googlehosted.com` CNAME with the
   Render target (or a redirect rule). Keep the Cloud Run service running, untouched.
5. Watch `/ready`, error rates, `llm_usage_events` cost per day, and provider errors for 48 h.

## 5. Rollback

- App regression: Render "Rollback" to the previous deploy (schema is additive; old code ignores
  new tables).
- DNS: restore the previous Cloudflare records (Google A records `216.239.32.21`, `.34.21`, `.36.21`,
  `.38.21`; `www` CNAME `ghs.googlehosted.com`). TTL 300 s means minutes.
- Model incident: set `LLM_PROVIDER=disabled` (calculators and data views keep working) or
  `LLM_FALLBACK=heuristic` (labelled).
- Data-provider incident: routes return 503 `market_data_unavailable`; no synthetic substitution.

## 6. Columbia retirement (owner approval, ≥7 days after cutover)

Follow `docs/GOOGLE_EXIT.md` § Decommission. Do not delete before confirming export/retention terms.

## 7. ThetaData (IV rank, history, live chains)

The owner has an Options **Standard** plan ($80/mo). The individual licence covers personal use only.
OptionsCoach uses ThetaData's official Python library (`thetadata`), which signs in with the account email
and password and fetches directly from ThetaData. **No Theta Terminal is needed.** Commands work from any directory.

1. **Smoke check (local).** Put the credentials in your shell environment only, never in a file in the repo:
   `cd /Users/jaredcohen/code/options-AI-assistant && read -r "THETADATA_EMAIL?Email: " && read -rs "THETADATA_PASSWORD?Password: " && export THETADATA_EMAIL THETADATA_PASSWORD && .venv/bin/python scripts/thetadata_smoke.py SPY`
   Every step should report `"ok": true`, and `get_chain.with_iv` should be non-zero. If a step fails because the
   live data has a different shape, fix it in `thetadata.py` and add the live shape as a fixture.
2. **Render (OptionsCoach web service).** The owner enters the secrets `THETADATA_EMAIL` and `THETADATA_PASSWORD`
   (or `THETADATA_API_KEY`). Then set:
   - `OPTIONS_HISTORY_PROVIDER=thetadata`, which enables the IV backfill;
   - optionally `OPTIONS_PROVIDER=thetadata`, which serves live chains from ThetaData instead of Tradier;
   - `THETADATA_DATA_STATUS=live` (paid tiers are real-time);
   - `THETADATA_MAX_CONCURRENCY=4` (the Standard plan's limit).
   The library adds about 21 MB of memory when in use, which fits the free plan.
3. **Backfill IV history.** This needs `DATABASE_URL` for OptionsCoach's database. Run it from a Render shell or worker,
   or from your Mac with a temporary database IP allow-list entry:
   `cd /Users/jaredcohen/code/options-AI-assistant && .venv/bin/python -m app.jobs.backfill_cli SPY QQQ --days 365`
   A worker with `ENABLE_SCHEDULER=1` and `INGEST_UNIVERSE` set does this by itself: a full year once per ticker,
   then a nightly one-week top-up.
4. **Terminal alternative.** `THETADATA_ACCESS=terminal` with `THETADATA_BASE_URL` uses a running Theta Terminal instead.
   `deploy/theta-terminal/Dockerfile` is kept for that case and is unverified.
5. **Rollback.** Unset `OPTIONS_PROVIDER` (Tradier remains the default) and `OPTIONS_HISTORY_PROVIDER`. Stored
   `iv_daily` rows stay valid, because the method is the same whichever provider supplied the quotes.
