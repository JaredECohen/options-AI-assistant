# Options Strategy Explainer + Payoff Calculator

Live URL (Cloud Run): https://YOUR_CLOUD_RUN_URL_HERE

This project is a narrow-domain chatbot that explains listed equity options strategies and calculates expiration payoffs. Premiums are optional; if missing, the app describes intrinsic-only payoff and notes the assumption. It includes a full evaluation harness with deterministic checks and model-as-judge (MaaJ) tests. The frontend is a React app (Vite).

## What This App Does
- Explains vanilla listed equity options strategies and when they make sense
- Answers market-view questions (bullish/bearish/neutral/volatile) with a best-match trade plus alternatives using % moneyness examples
- Explains convexity and gamma exposure and how they shape payoff curvature
- Evaluates user-specified legs and computes payoff metrics from user-provided premiums when available (otherwise intrinsic-only)
- Provides a strategy builder with market-view and strategy filtering
- Remembers recent chat context per browser session; use "Clear Chat" to reset
  - Server-side memory is kept in-process; use the Clear Chat button to reset the session

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
uv run python -m uvicorn app.main:app --reload
```

Open http://localhost:8000 in a browser after building the frontend, or run the React dev server (below).

### Frontend Dev (React + Vite)
```bash
cd frontend
npm install
npm run dev
```
Then open http://localhost:5173. The dev server proxies `/api` to the FastAPI backend.

### Frontend Build
```bash
cd frontend
npm run build
```
The FastAPI app will serve `frontend/dist` when it exists.

Logging:
- Set `LOG_LEVEL` in `.env` (e.g., `INFO`, `DEBUG`) to control server-side logging.

## Running Evals
The eval harness runs 20+ cases with deterministic checks plus MaaJ grading. It supports offline mock mode.

```bash
uv run python eval/run_eval.py --mock --deterministic
```

## API Endpoints
- `POST /api/chat`
- `GET /health`
- `POST /api/clear` (clears server-side chat memory for a session)

Note: `/api/chain` and `/api/quote` exist for future live-data extensions but are not used by the current UI.

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
- Premiums are optional in this simplified mode; no live chain or quote fetch is required.
- Strike suggestions are expressed as % moneyness only (e.g., 10% OTM) and are educational examples tied to the user's stated market view.

## Future Improvements
- Display real-time options chains.
- Add an options package builder panel with legs auto-populated based on strategy selection, plus custom leg editing.
- Compute payoff, max profit, max loss, and breakevens using real premium data.
- Support comparing pricing and implied volatility across tickers to help with strategy selection.
