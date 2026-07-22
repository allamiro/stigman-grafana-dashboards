#!/usr/bin/env bash
# Validate GET /api/collections/{id}/metrics/summary/collection for a
# collection id: required fields present and numeric.
#   ./scripts/test-collection-metrics.sh 1
set -euo pipefail
cd "$(dirname "$0")/.."
source .env

KEYCLOAK_URL="${KEYCLOAK_URL:-http://localhost:8180}"
STIGMAN_URL="${STIGMAN_URL:-http://localhost:54000}"
CID="${1:-1}"

TOKEN=$(curl -sf -X POST "$KEYCLOAK_URL/realms/stigman/protocol/openid-connect/token" \
  -d grant_type=client_credentials -d client_id="${NEXUS_REPORTER_CLIENT_ID:-nexus-reporter}" \
  -d client_secret="$NEXUS_REPORTER_CLIENT_SECRET" | jq -r .access_token)
[[ -n "$TOKEN" && "$TOKEN" != "null" ]] || { echo "FAIL: could not obtain token"; exit 1; }

BODY_FILE=$(mktemp)
HTTP=$(curl -s -o "$BODY_FILE" -w '%{http_code}' -H "Authorization: Bearer $TOKEN" \
  "$STIGMAN_URL/api/collections/$CID/metrics/summary/collection")
echo "GET /api/collections/$CID/metrics/summary/collection -> HTTP $HTTP"
if [[ "$HTTP" != "200" ]]; then
  jq . "$BODY_FILE" 2>/dev/null || cat "$BODY_FILE"
  echo "FAIL: expected HTTP 200 (is collection $CID granted to the reporter?)"
  exit 1
fi

jq '{collectionId, name, assets, metrics: {assessments: .metrics.assessments,
     assessed: .metrics.assessed, results: .metrics.results,
     findings: .metrics.findings,
     assessmentsBySeverity: .metrics.assessmentsBySeverity,
     assessedBySeverity: .metrics.assessedBySeverity}}' "$BODY_FILE"

echo "validating required numeric fields ..."
jq -e '
  def num(f): (f | type) == "number";
  .metrics as $m
  | num($m.assessments) and num($m.assessed)
    and num($m.results.pass) and num($m.results.fail)
    and num($m.results.notapplicable) and num($m.results.other)
    and num($m.findings.high) and num($m.findings.medium) and num($m.findings.low)
    and num($m.assessmentsBySeverity.high) and num($m.assessmentsBySeverity.medium)
    and num($m.assessmentsBySeverity.low)
    and num($m.assessedBySeverity.high) and num($m.assessedBySeverity.medium)
    and num($m.assessedBySeverity.low)
    and num($m.statuses.saved) and num($m.statuses.submitted)
    and num($m.statuses.accepted) and num($m.statuses.rejected)
' "$BODY_FILE" >/dev/null || { echo "FAIL: missing or non-numeric metric fields"; exit 1; }

# Consistency checks
jq -e '.metrics | (.assessed <= .assessments)
  and ((.results.pass + .results.fail + .results.notapplicable + .results.other) == .assessed)
' "$BODY_FILE" >/dev/null || { echo "FAIL: metric consistency check failed"; exit 1; }

echo "PASS: collection $CID metrics are well-formed"
