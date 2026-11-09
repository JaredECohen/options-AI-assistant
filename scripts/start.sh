#!/bin/sh
# Container entrypoint. Render's free plan has no pre-deploy step, so when
# MIGRATE_ON_START=1 the schema is brought to head before the server starts.
# Safe for the single-instance free service only; with more than one instance,
# use a pre-deploy command (alembic upgrade head) and leave this unset.
set -eu
if [ "${MIGRATE_ON_START:-0}" = "1" ]; then
  alembic upgrade head
fi
exec uvicorn app.main:app_factory --factory --host 0.0.0.0 --port "${PORT:-10000}" \
  --proxy-headers --forwarded-allow-ips='*'
