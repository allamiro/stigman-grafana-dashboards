#!/usr/bin/env bash
# Grant the nexus-reporter service account READ-ONLY access to a collection.
#
#   ./scripts/grant-reporter-access.sh <collectionId>
#
# Implementation (STIG Manager 1.6.x role/ACL model):
#   1. find the user record for service-account-nexus-reporter
#   2. POST a grant with roleId=1 ("restricted" — lowest role)
#   3. PUT an ACL on that grant of [{"access":"r"}] (collection-wide read),
#      which makes the grant read-only for every asset/STIG in the collection.
set -euo pipefail
cd "$(dirname "$0")/.."
source .env

KEYCLOAK_URL="${KEYCLOAK_URL:-http://localhost:8180}"
STIGMAN_URL="${STIGMAN_URL:-http://localhost:54000}"
SVC_USERNAME="service-account-${NEXUS_REPORTER_CLIENT_ID:-nexus-reporter}"
CID="${1:-}"
[[ -n "$CID" ]] || { echo "usage: $0 <collectionId>" >&2; exit 2; }

TOKEN=$(curl -sf -X POST "$KEYCLOAK_URL/realms/stigman/protocol/openid-connect/token" \
  -d grant_type=password -d client_id=stig-manager \
  -d username="$STIGMAN_ADMIN_USERNAME" -d password="$STIGMAN_ADMIN_PASSWORD" \
  -d 'scope=openid stig-manager:collection stig-manager:user stig-manager:op' \
  | jq -r .access_token)
[[ -n "$TOKEN" && "$TOKEN" != "null" ]] || { echo "failed to get stigadmin token" >&2; exit 1; }
AUTH=(-H "Authorization: Bearer $TOKEN")

USER_ID=$(curl -sf "${AUTH[@]}" "$STIGMAN_URL/api/users?elevate=true" | \
  jq -r --arg u "$SVC_USERNAME" '.[] | select(.username == $u) | .userId' | head -1)
if [[ -z "$USER_ID" ]]; then
  echo "User '$SVC_USERNAME' not found in STIG Manager." >&2
  echo "It is created on the service account's first API call. Run:" >&2
  echo "  ./scripts/test-service-account.sh" >&2
  exit 1
fi
echo "$SVC_USERNAME has userId=$USER_ID"

GRANT_ID=$(curl -sf "${AUTH[@]}" "$STIGMAN_URL/api/collections/$CID/grants" | \
  jq -r --arg u "$USER_ID" '.[] | select(.user.userId == $u) | .grantId' | head -1)
if [[ -z "$GRANT_ID" ]]; then
  GRANT_ID=$(curl -sf -X POST "${AUTH[@]}" -H 'Content-Type: application/json' \
    "$STIGMAN_URL/api/collections/$CID/grants" \
    -d "[{\"userId\": \"$USER_ID\", \"roleId\": 1}]" | jq -r '.[0].grantId')
  echo "created grant $GRANT_ID (roleId=1, restricted) on collection $CID"
else
  echo "grant $GRANT_ID already exists on collection $CID"
fi

curl -sf -X PUT "${AUTH[@]}" -H 'Content-Type: application/json' \
  "$STIGMAN_URL/api/collections/$CID/grants/$GRANT_ID/acl" \
  -d '[{"access": "r"}]' >/dev/null
echo "applied collection-wide read-only ACL to grant $GRANT_ID"

echo "verify with: ./scripts/test-service-account.sh"
