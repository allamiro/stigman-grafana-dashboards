#!/usr/bin/env bash
# Build the exporter image and run it standalone with `docker run -d`.
# (The lab's docker-compose.yml already runs the same image as the
# `stigman-exporter` service — use this script when you want the container
# outside the compose stack, e.g. against a remote STIG Manager.)
set -euo pipefail
cd "$(dirname "$0")/../.."
source .env

docker build -t stigman-exporter:1.2.0 metrics-history/exporter

docker rm -f stigman-exporter 2>/dev/null || true
docker run -d --name stigman-exporter \
  --restart unless-stopped \
  -p 9633:9633 \
  -e STIGMAN_API_URL="${STIGMAN_API_URL:-http://host.docker.internal:54000/api}" \
  -e KEYCLOAK_TOKEN_URL="${KEYCLOAK_TOKEN_URL:-http://host.docker.internal:8180/realms/stigman/protocol/openid-connect/token}" \
  -e OIDC_CLIENT_ID="${NEXUS_REPORTER_CLIENT_ID:-nexus-reporter}" \
  -e OIDC_CLIENT_SECRET="$NEXUS_REPORTER_CLIENT_SECRET" \
  stigman-exporter:1.2.0

echo "exporter running: http://localhost:9633/metrics"
