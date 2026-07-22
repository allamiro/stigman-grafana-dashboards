#!/usr/bin/env bash
# Create .env from .env.example with strong random secrets.
# Usage: ./scripts/generate-env.sh [--force]
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ -f .env && "${1:-}" != "--force" ]]; then
  echo ".env already exists. Use --force to overwrite." >&2
  exit 1
fi

rand() { openssl rand -hex 32; }

sed \
  -e "s|^NEXUS_REPORTER_CLIENT_SECRET=.*|NEXUS_REPORTER_CLIENT_SECRET=$(rand)|" \
  -e "s|^GRAFANA_OIDC_CLIENT_SECRET=.*|GRAFANA_OIDC_CLIENT_SECRET=$(rand)|" \
  -e "s|^KEYCLOAK_DB_PASSWORD=.*|KEYCLOAK_DB_PASSWORD=$(rand)|" \
  -e "s|^STIGMAN_DB_PASSWORD=.*|STIGMAN_DB_PASSWORD=$(rand)|" \
  .env.example > .env

echo "Wrote .env with random client and database secrets."
echo "Interactive lab passwords (Keycloak/Grafana admin, stigadmin) keep their"
echo "documented defaults — change them in .env if you want."
