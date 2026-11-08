# Google / Columbia dependency inventory and exit plan

Collected 2026-11-08 without touching any Google resource: local gcloud configuration (non-secret
fields only), repository history and README, and public DNS. Credential stores in `.gcloud/` were
not opened. Nothing here was changed in Google Cloud.

## Inventory

| Area | What exists | Evidence | Owner / status |
| --- | --- | --- | --- |
| Account | `jc6640@columbia.edu` (university identity) | `.gcloud/configurations/config_default` | Columbia-owned. Recovery and lifetime depend on enrolment. |
| GCP project | **Unknown**: no `core/project` in local config | config_default | GAP: read the project ID, billing account and organisation in the Cloud console. |
| Billing / org | Unknown | — | GAP: confirm whether billing is a Columbia org account or education credits. |
| Cloud Run | service `options-chatbot`, region `us-central1`, `--allow-unauthenticated` | README "Cloud Run Deploy"; config `run/region` | Live status not checked (no production calls). Oct 17 HTTP 500 report is historical. |
| Image build | `gcloud builds submit --tag gcr.io/$PROJECT_ID/options-chatbot` (Container Registry / Cloud Build) | README | Replaced by the repo Dockerfile + Render build. |
| Model | Vertex AI `gemini-2.5-flash-lite` via `google-cloud-aiplatform`, ADC credentials | old `app/providers/llm/vertex.py`, `.env.example` | **Removed** in this branch (code and dependency). |
| IAM / service accounts | Cloud Run default runtime SA (presumed) | — | GAP: list SAs and keys in console; none exist in the repo. |
| Secrets | Env vars only: `LLM_PROVIDER`, `VERTEX_PROJECT_ID`, `VERTEX_LOCATION`, `VERTEX_MODEL`, `TRADIER_API_TOKEN` (if set) | README deploy command | No Secret Manager usage in code. |
| Storage | None (memory was in-process) | code | Nothing to export from GCS / Firestore / SQL. |
| Scheduled jobs | None in code | code | GAP: confirm no Cloud Scheduler jobs in console. |
| DNS | `optionscoach.ai` A → 216.239.32–38.21 (Google), `www` CNAME `ghs.googlehosted.com`; nameservers **Cloudflare** | public `dig` | Cloud Run domain mapping. DNS lives in a Cloudflare account; confirm it is personally owned. |
| Local credentials | `.gcloud/` (gitignored CLOUDSDK_CONFIG) with Columbia ADC/refresh tokens | directory listing | Must never be copied to personal accounts or images (`.dockerignore` excludes it). |
| Integrations | Eval script's `--judge vertex` | old `eval/run_eval.py` | **Removed.** |

## Export and transfer rights

There is no application data to export (no persistent storage was used). The code is in the
personal GitHub repository. Before decommissioning, the owner should confirm with Columbia's terms
that the deployed image and any Cloud Run logs need no retention, and that the domain mapping and
any Google-side verification of `optionscoach.ai` can be removed by the owner.

## Fresh personal ownership (to be done by the owner, not by this branch)

1. Personally owned Anthropic Console account and API key (`ANTHROPIC_API_KEY`), with a spend limit.
2. Personally owned Render workspace (or the existing MarketMosaic workspace, if that is personal),
   with the OptionsCoach blueprint applied only after approval.
3. Tradier (or the chosen provider) under a personal account, data status confirmed in writing.
4. Clerk: reuse MarketMosaic's application (personal).
5. Cloudflare DNS: confirm the account holding `optionscoach.ai` is personal.

No Columbia ADC, refresh token or service-account key is reused anywhere.

## Decommission (owner-approved, after production acceptance)

1. Confirm the new deployment has passed production acceptance and DNS has been cut over for 7+ days.
2. In Cloud Run: remove the domain mapping for `optionscoach.ai`, then set the service's traffic to 0
   or delete `options-chatbot`.
3. Delete the Container Registry images and Cloud Build artifacts for `options-chatbot`.
4. Disable the Vertex AI API in the project if nothing else uses it; check the billing report shows
   no further charges.
5. Locally, delete `/Users/jaredcohen/code/options-AI-assistant/.gcloud` (Columbia credentials) once
   nothing needs them, or revoke with `gcloud auth revoke` first.
