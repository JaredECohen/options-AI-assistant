#!/usr/bin/env bash
# Local container acceptance: real image, real Postgres 16, staging config,
# fixture Clerk JWKS + fixture MarketMosaic entitlements. No external network,
# no paid calls (synthetic market data + labelled demo answers via DEMO_MODE=1).
# Usage: scripts/container_check.sh [image]   (default optionscoach:local)
set -euo pipefail
IMAGE="${1:-optionscoach:local}"
NET=oc-check-net
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$ROOT/.container-check"
FIX="$(mktemp -d "$ROOT/.container-check/run.XXXXXX")"  # under $HOME so Colima/Docker Desktop can mount it
cleanup() { docker rm -f oc-check-app oc-check-fix oc-check-pg >/dev/null 2>&1 || true; docker network rm "$NET" >/dev/null 2>&1 || true; }
trap cleanup EXIT
cleanup
docker network create "$NET" >/dev/null
docker run -d --name oc-check-pg --network "$NET" --network-alias pg -e POSTGRES_PASSWORD=octest -e POSTGRES_USER=oc -e POSTGRES_DB=oc postgres:16-alpine >/dev/null
docker run -d --name oc-check-fix --network "$NET" --network-alias fixtures -u 0 -v "$FIX:/fixtures" \
  -v "$(cd "$(dirname "$0")" && pwd)/container_fixtures.py:/app/container_fixtures.py:ro" "$IMAGE" python /app/container_fixtures.py >/dev/null
for _ in $(seq 1 30); do docker exec oc-check-pg pg_isready -U oc >/dev/null 2>&1 && break; sleep 1; done
for _ in $(seq 1 30); do [ -s "$FIX/token_b" ] && break; sleep 1; done

ENV=(-e APP_ENV=staging -e DEMO_MODE=1 -e DATABASE_URL=postgresql://oc:octest@pg:5432/oc
     -e AUTH_ENABLED=true -e CLERK_ISSUER=http://fixtures:9000 -e CLERK_JWKS_URL=http://fixtures:9000/.well-known/jwks.json
     -e CLERK_AUTHORIZED_PARTIES=http://localhost:18080 -e ALLOWED_ORIGINS=http://localhost:18080
     -e ENTITLEMENTS_SOURCE=marketmosaic -e MARKETMOSAIC_BASE_URL=http://fixtures:9000
     -e MARKETMOSAIC_SERVICE_TOKEN=fixture-service-token -e OPTIONS_PROVIDER=mock -e LLM_PROVIDER=heuristic
     -e RELEASE_REVISION=container-check)

echo "== migrations (pre-deploy step)"
docker run --rm --network "$NET" "${ENV[@]}" "$IMAGE" alembic upgrade head
echo "applied revision: $(docker exec oc-check-pg psql -U oc -d oc -tAc 'select version_num from alembic_version')"
echo "== boot"
docker run -d --name oc-check-app --network "$NET" -p 127.0.0.1:18080:10000 "${ENV[@]}" "$IMAGE" >/dev/null
for _ in $(seq 1 40); do curl -fs http://127.0.0.1:18080/health >/dev/null 2>&1 && break; sleep 1; done
A="Authorization: Bearer $(cat "$FIX/token_a")"
B="Authorization: Bearer $(cat "$FIX/token_b")"
fail=0
check() { if [ "$2" = "$3" ]; then echo "ok   $1 ($3)"; else echo "FAIL $1: expected $2 got $3"; fail=1; fi; }
code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }

check "health" 200 "$(code http://127.0.0.1:18080/health)"
check "ready (db + migrations at head)" 200 "$(code http://127.0.0.1:18080/ready)"
check "chat without token" 401 "$(code -X POST -H 'Content-Type: application/json' -d '{"message":"hi"}' http://127.0.0.1:18080/api/chat)"
RESP=$(curl -s -X POST -H "$A" -H 'Content-Type: application/json' -d '{"message":"what is a covered call"}' http://127.0.0.1:18080/api/chat)
CONV=$(printf '%s' "$RESP" | python3 -c 'import sys,json;print(json.load(sys.stdin)["conversation_id"])')
check "authenticated fixture chat stored" 200 "$(code -H "$A" http://127.0.0.1:18080/api/conversations/$CONV/messages)"
check "other user cannot read it" 404 "$(code -H "$B" http://127.0.0.1:18080/api/conversations/$CONV/messages)"
check "free user blocked from backtests" 402 "$(code -X POST -H "$B" -H 'Content-Type: application/json' -d '{}' http://127.0.0.1:18080/api/backtests)"
LOSS=$(curl -s -X POST -H "$A" -H 'Content-Type: application/json' \
  -d '{"ticker":"SPY","legs":[{"instrument":"option","option_type":"put","side":"sell","strike":100,"expiration":"2026-12-18","premium":5}]}' \
  http://127.0.0.1:18080/api/payoff | python3 -c 'import sys,json;print(json.load(sys.stdin)["computed"]["max_loss"])')
check "T5 max loss through container" 9500.0 "$LOSS"
check "frontend served" 200 "$(code http://127.0.0.1:18080/)"
ROWS=$(docker exec oc-check-pg psql -U oc -d oc -tAc "select count(*) from messages")
check "messages persisted in Postgres" 2 "$ROWS"
echo "== app log (last lines; no secrets, no message text)"
docker logs oc-check-app 2>&1 | tail -5
exit $fail
