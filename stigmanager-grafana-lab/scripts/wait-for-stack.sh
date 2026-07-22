#!/usr/bin/env bash
# Wait until every component of the lab is healthy and answering.
# Bounded retries; prints a useful error and exits nonzero on timeout.
set -euo pipefail
cd "$(dirname "$0")/.."

KEYCLOAK_URL="${KEYCLOAK_URL:-http://localhost:8180}"
STIGMAN_URL="${STIGMAN_URL:-http://localhost:54000}"
GRAFANA_URL="${GRAFANA_URL:-http://localhost:3200}"
TRIES="${TRIES:-60}"          # per check
SLEEP="${SLEEP:-5}"           # seconds between attempts

wait_for() {
  local label="$1" cmd="$2" i
  printf '%-45s' "waiting: $label"
  for ((i=1; i<=TRIES; i++)); do
    if eval "$cmd" >/dev/null 2>&1; then
      echo "OK"
      return 0
    fi
    sleep "$SLEEP"
  done
  echo "FAILED after $((TRIES*SLEEP))s"
  echo "  check: $cmd" >&2
  return 1
}

fail=0

wait_for "keycloak-db container healthy" \
  "docker inspect -f '{{.State.Health.Status}}' sml-keycloak-db | grep -q healthy" || fail=1
wait_for "stigman-db container healthy" \
  "docker inspect -f '{{.State.Health.Status}}' sml-stigman-db | grep -q healthy" || fail=1
wait_for "Keycloak master ready" \
  "curl -sf ${KEYCLOAK_URL}/realms/master/.well-known/openid-configuration" || fail=1
wait_for "Keycloak stigman realm imported" \
  "curl -sf ${KEYCLOAK_URL}/realms/stigman/.well-known/openid-configuration" || fail=1
wait_for "STIG Manager API responding" \
  "curl -sf ${STIGMAN_URL}/api/op/configuration" || fail=1
wait_for "Grafana health endpoint" \
  "curl -sf ${GRAFANA_URL}/api/health" || fail=1

if [[ $fail -ne 0 ]]; then
  echo
  echo "Stack did not become healthy. Inspect with:" >&2
  echo "  docker compose ps" >&2
  echo "  docker compose logs keycloak stigman grafana" >&2
  exit 1
fi
echo
echo "Stack is up:"
echo "  STIG Manager: ${STIGMAN_URL}"
echo "  Keycloak:     ${KEYCLOAK_URL}"
echo "  Grafana:      ${GRAFANA_URL}"
