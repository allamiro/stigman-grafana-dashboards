#!/usr/bin/env bash
# Run the exporter directly on your workstation (no Docker).
# Creates a virtualenv on first run, reads secrets from the lab .env, and
# points at the lab's host-published ports.
#
#   ./metrics-history/local/run-local.sh
#   curl http://localhost:9633/metrics
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
  ./.venv/bin/pip install --quiet -r ../exporter/requirements.txt
fi

LAB_ENV="../../.env"
[[ -f "$LAB_ENV" ]] || { echo "lab .env not found at $LAB_ENV" >&2; exit 1; }
# shellcheck disable=SC1090
source "$LAB_ENV"

export STIGMAN_API_URL="${STIGMAN_API_URL:-http://localhost:54000/api}"
export KEYCLOAK_TOKEN_URL="${KEYCLOAK_TOKEN_URL:-http://localhost:8180/realms/stigman/protocol/openid-connect/token}"
export OIDC_CLIENT_ID="${NEXUS_REPORTER_CLIENT_ID:-nexus-reporter}"
export OIDC_CLIENT_SECRET="${NEXUS_REPORTER_CLIENT_SECRET:?NEXUS_REPORTER_CLIENT_SECRET missing from .env}"
export EXPORTER_PORT="${EXPORTER_PORT:-9633}"

exec ./.venv/bin/python ../exporter/stigman_exporter.py
