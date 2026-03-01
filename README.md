# Options Strategy Explainer + Payoff Calculator

Live URL (Cloud Run): https://YOUR_CLOUD_RUN_URL_HERE

This project is a narrow-domain chatbot that explains listed equity options strategies and calculates expiration payoffs using live option premiums. It includes a full evaluation harness with deterministic checks and model-as-judge (MaaJ) tests.

## What This App Does
- Explains vanilla listed equity options strategies
- Fetches live option chains and underlying prices
- Evaluates user-specified legs and computes payoff metrics
- Provides a strategy builder that asks users to choose expirations and strikes (no strike recommendations)

## Local Setup
1. Create and activate a virtual env if desired.
2. Install dependencies with uv:

```bash
uv sync
```

3. Copy env example and set keys:

```bash
cp .env.example .env
```

4. Run the API and UI:

```bash
uv run uvicorn app.main:app --reload
```

Open http://localhost:8000 in a browser.

## Running Evals
The eval harness runs 20+ cases with deterministic checks plus MaaJ grading. It supports offline mock mode.

```bash
uv run python eval/run_eval.py --mock --deterministic
```

## API Endpoints
- `POST /api/chat`
- `GET /api/chain?ticker=...&expiration=...`
- `GET /api/quote?ticker=...`
- `GET /health`

## Cloud Run Deploy (Example)
1. Build container:

```bash
gcloud builds submit --tag gcr.io/$PROJECT_ID/options-chatbot
```

2. Deploy:

```bash
gcloud run deploy options-chatbot \
  --image gcr.io/$PROJECT_ID/options-chatbot \
  --platform managed \
  --region $REGION \
  --allow-unauthenticated \
  --set-env-vars LLM_PROVIDER=vertex,VERTEX_PROJECT_ID=$PROJECT_ID,VERTEX_LOCATION=$REGION,VERTEX_MODEL=gemini-2.5-flash-lite
```

Replace the live URL placeholder above with the resulting Cloud Run URL.

## Vertex Cost Controls
- Low temperature and capped output tokens
- In-memory cache for repeated prompts
- Simple per-minute rate limit
- Deterministic eval mode can bypass the LLM when possible

## Notes
- If Vertex credentials are missing, the app falls back to a deterministic strategy-card mode so evals still run offline.
- Payoff calculations are at expiration only and use the contract multiplier of 100 for options.
