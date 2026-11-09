#!/bin/sh
# Container entrypoint. Render's free plan has no pre-deploy step, so when
# MIGRATE_ON_START=1 the schema is brought to head before the server starts.
# Safe for the single-instance free service only; with more than one instance,
# use a pre-deploy command (alembic upgrade head) and leave this unset.
set -eu

# Keep these two messages identical to DATABASE_URL_MISSING and
# DATABASE_URL_NOT_POSTGRES in app/config.py. Printed here so a missing URL
# fails before Alembic's traceback, and so production never migrates SQLite.
database_url_missing() {
  echo "DATABASE_URL is required and is unset. On Render it must be the internal connection string of the optionscoach-db Postgres database, wired in render.yaml with fromDatabase name optionscoach-db property connectionString. Link that variable on the optionscoach web service or sync the blueprint, then redeploy. Do not commit the value. Staging and production do not fall back to SQLite." >&2
  exit 1
}

database_url_not_postgres() {
  echo "DATABASE_URL must be a Postgres URL for the optionscoach-db database in staging/production. SQLite is only for local development and is not a fallback here." >&2
  exit 1
}

case "${APP_ENV:-}" in
  staging|production)
    case "${DATABASE_URL:-}" in
      "") database_url_missing ;;
      sqlite:*) database_url_not_postgres ;;
    esac
    ;;
  *)
    if [ "${MIGRATE_ON_START:-0}" = "1" ] && [ -z "${DATABASE_URL:-}" ]; then
      database_url_missing
    fi
    ;;
esac

if [ "${MIGRATE_ON_START:-0}" = "1" ]; then
  alembic upgrade head
fi
exec uvicorn app.main:app_factory --factory --host 0.0.0.0 --port "${PORT:-10000}" \
  --proxy-headers --forwarded-allow-ips='*'
