#!/usr/bin/env bash
# Verify Keycloak: realm, discovery, token endpoint, and that nexus-reporter
# can obtain a client-credentials token with the expected claims.
# Never prints the client secret (only its length).
set -euo pipefail
cd "$(dirname "$0")/.."
source .env

KEYCLOAK_URL="${KEYCLOAK_URL:-http://localhost:8180}"
REALM_URL="$KEYCLOAK_URL/realms/stigman"
CLIENT_ID="${NEXUS_REPORTER_CLIENT_ID:-nexus-reporter}"
fail=0
check() { # check LABEL EXIT_OK
  if [[ "$2" == "0" ]]; then echo "  ok: $1"; else echo "  FAIL: $1"; fail=1; fi
}

echo "[1/4] realm + OpenID discovery"
DISC=$(curl -sf "$REALM_URL/.well-known/openid-configuration") || { echo "  FAIL: discovery endpoint unreachable"; exit 1; }
ISSUER=$(echo "$DISC" | jq -r .issuer)
TOKEN_URL=$(echo "$DISC" | jq -r .token_endpoint)
[[ "$ISSUER" == "$REALM_URL" ]]; check "issuer is $ISSUER" $?
[[ -n "$TOKEN_URL" && "$TOKEN_URL" != "null" ]]; check "token endpoint advertised: $TOKEN_URL" $?

echo "[2/4] token endpoint responds"
curl -sf -o /dev/null -X POST "$TOKEN_URL" -d grant_type=client_credentials \
  -d client_id=does-not-exist -d client_secret=x -w '' || true
HTTP=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$TOKEN_URL" \
  -d grant_type=client_credentials -d client_id=does-not-exist -d client_secret=x)
[[ "$HTTP" == "401" || "$HTTP" == "400" ]]; check "rejects unknown client (HTTP $HTTP)" $?

echo "[3/4] client-credentials grant for $CLIENT_ID (secret length: ${#NEXUS_REPORTER_CLIENT_SECRET})"
RESP=$(curl -sf -X POST "$TOKEN_URL" -d grant_type=client_credentials \
  -d client_id="$CLIENT_ID" -d client_secret="$NEXUS_REPORTER_CLIENT_SECRET")
TOKEN=$(echo "$RESP" | jq -r .access_token)
[[ -n "$TOKEN" && "$TOKEN" != "null" ]]; check "access token issued (expires_in=$(echo "$RESP" | jq -r .expires_in)s)" $?

echo "[4/4] token claims"
CLAIMS=$(python3 -c "
import base64, json, sys
p = sys.argv[1].split('.')[1]
p += '=' * (-len(p) % 4)
print(json.dumps(json.loads(base64.urlsafe_b64decode(p))))" "$TOKEN")
iss=$(echo "$CLAIMS" | jq -r .iss)
aud=$(echo "$CLAIMS" | jq -r 'if (.aud|type)=="array" then .aud|join(",") else .aud end')
azp=$(echo "$CLAIMS" | jq -r .azp)
sub=$(echo "$CLAIMS" | jq -r .sub)
pun=$(echo "$CLAIMS" | jq -r .preferred_username)
scope=$(echo "$CLAIMS" | jq -r .scope)
[[ "$iss" == "$REALM_URL" ]];                     check "iss = $iss" $?
[[ "$aud" == *"stig-manager"* ]];                 check "aud contains stig-manager ($aud)" $?
[[ "$azp" == "$CLIENT_ID" ]];                     check "azp = $azp" $?
[[ -n "$sub" && "$sub" != "null" ]];              check "sub = $sub" $?
[[ "$pun" == "service-account-$CLIENT_ID" ]];     check "preferred_username = $pun" $?
[[ "$scope" == *"stig-manager:collection:read"* ]]; check "scope includes stig-manager:collection:read" $?
if echo "$scope" | grep -qE 'stig-manager:(collection|stig|user|op)( |$)'; then
  echo "  FAIL: scope contains WRITE scopes: $scope"; fail=1
else
  echo "  ok: scope is read-only ($scope)"
fi

[[ $fail -eq 0 ]] && echo "PASS: Keycloak authentication" || { echo "FAIL: Keycloak authentication"; exit 1; }
