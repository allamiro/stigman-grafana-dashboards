#!/usr/bin/env bash
# Obtain a client-credentials token and call GET /api/collections,
# showing HTTP status, the JSON body, and a diagnostic when the body is [].
# Also proves the account is read-only (write attempt must be rejected).
set -euo pipefail
cd "$(dirname "$0")/.."
source .env

KEYCLOAK_URL="${KEYCLOAK_URL:-http://localhost:8180}"
STIGMAN_URL="${STIGMAN_URL:-http://localhost:54000}"
CLIENT_ID="${NEXUS_REPORTER_CLIENT_ID:-nexus-reporter}"

TOKEN=$(curl -sf -X POST "$KEYCLOAK_URL/realms/stigman/protocol/openid-connect/token" \
  -d grant_type=client_credentials -d client_id="$CLIENT_ID" \
  -d client_secret="$NEXUS_REPORTER_CLIENT_SECRET" | jq -r .access_token)
[[ -n "$TOKEN" && "$TOKEN" != "null" ]] || { echo "FAIL: could not obtain token"; exit 1; }

BODY_FILE=$(mktemp)
HTTP=$(curl -s -o "$BODY_FILE" -w '%{http_code}' \
  -H "Authorization: Bearer $TOKEN" "$STIGMAN_URL/api/collections")
echo "GET /api/collections -> HTTP $HTTP"
jq . "$BODY_FILE"

if [[ "$HTTP" != "200" ]]; then
  echo "FAIL: expected HTTP 200"; exit 1
fi

COUNT=$(jq 'length' "$BODY_FILE")
if [[ "$COUNT" == "0" ]]; then
  cat <<'EOT'
NOTE: HTTP 200 with [] means AUTHENTICATION SUCCEEDED but the service
account has no Collection grants yet. STIG Manager only returns
collections the caller has been granted access to. Fix with:
    ./scripts/grant-reporter-access.sh <collectionId>
or in the STIG Manager UI: Collection -> Manage -> Grants -> add user
'service-account-nexus-reporter' with the Restricted role + read-only ACL.
EOT
  echo "PASS (auth) / WARN (no grants): Service account token"
  exit 0
fi

echo "collections visible: $COUNT"

# Least-privilege proof: a write must be rejected (403 expected; the token
# carries no write scopes and the grant ACL is read-only).
WRITE_HTTP=$(curl -s -o /dev/null -w '%{http_code}' -X POST \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  "$STIGMAN_URL/api/collections" -d '{"name":"should-not-exist","description":"x","metadata":{},"grants":[],"labels":[]}')
if [[ "$WRITE_HTTP" == "403" || "$WRITE_HTTP" == "401" ]]; then
  echo "ok: write attempt rejected (HTTP $WRITE_HTTP) — account is read-only"
else
  echo "FAIL: write attempt returned HTTP $WRITE_HTTP (expected 401/403)"; exit 1
fi

echo "PASS: Service account can read collections"
