#!/usr/bin/env bash
# Seed the lab with demonstration data:
#   - imports two STIG benchmarks (RHEL 7, Windows 10) from the STIG Manager
#     project's public test fixtures (DISA content, public release)
#   - creates two Collections: "Linux Production" and "Windows Production"
#   - creates assets, assigns STIGs, and posts deliberately mixed review
#     results (open CAT I/II/III findings, passes, N/A, and unassessed rules)
#
# Auth: uses the stigadmin user via the OAuth2 password grant on the public
# stig-manager client. Direct Access Grants is a LAB-ONLY convenience.
#
# Idempotence: safe to re-run; STIG imports use clobber=true and collections
# are reused by name if they already exist.
set -euo pipefail
cd "$(dirname "$0")/.."
source .env

KEYCLOAK_URL="${KEYCLOAK_URL:-http://localhost:8180}"
STIGMAN_URL="${STIGMAN_URL:-http://localhost:54000}"
FIXTURE_BASE="https://raw.githubusercontent.com/NUWCDIVNPT/stig-manager/main/test/api/form-data-files"
RHEL_XML="U_RHEL_7_STIG_V3R0-3_Manual-xccdf.xml"
WIN_XML="U_MS_Windows_10_STIG_V1R23_Manual-xccdf.xml"

mkdir -p test-data
for f in "$RHEL_XML" "$WIN_XML"; do
  if [[ ! -s "test-data/$f" ]]; then
    echo "downloading $f ..."
    curl -sfL -o "test-data/$f" "$FIXTURE_BASE/$f"
  fi
done

echo "obtaining stigadmin token ..."
TOKEN=$(curl -sf -X POST "$KEYCLOAK_URL/realms/stigman/protocol/openid-connect/token" \
  -d grant_type=password -d client_id=stig-manager \
  -d username="$STIGMAN_ADMIN_USERNAME" -d password="$STIGMAN_ADMIN_PASSWORD" \
  -d 'scope=openid stig-manager:collection stig-manager:stig stig-manager:user stig-manager:op' \
  | jq -r .access_token)
[[ -n "$TOKEN" && "$TOKEN" != "null" ]] || { echo "failed to get stigadmin token" >&2; exit 1; }
AUTH=(-H "Authorization: Bearer $TOKEN")

api() { # api METHOD PATH [curl args...]
  local method="$1" path="$2"; shift 2
  curl -sf -X "$method" "${AUTH[@]}" "$STIGMAN_URL/api$path" "$@"
}

echo "importing STIG benchmarks (elevate=true, clobber=true) ..."
for f in "$RHEL_XML" "$WIN_XML"; do
  api POST "/stigs?elevate=true&clobber=true" -F "importFile=@test-data/$f;type=text/xml" | \
    jq -c '{benchmarkId: (.benchmarkId // .[0].benchmarkId // "imported"), status: "ok"}' || {
      echo "STIG import failed for $f" >&2; exit 1; }
done

RHEL_BENCH=$(api GET "/stigs" | jq -r '.[] | select(.benchmarkId | test("RHEL_7")) | .benchmarkId' | head -1)
WIN_BENCH=$(api GET "/stigs" | jq -r '.[] | select(.benchmarkId | test("Windows_10")) | .benchmarkId' | head -1)
echo "benchmarks: $RHEL_BENCH , $WIN_BENCH"

MY_UID_STIGMAN=$(api GET "/user" | jq -r .userId)

ensure_collection() { # NAME -> collectionId
  local name="$1" id
  id=$(api GET "/collections?elevate=true" | jq -r --arg n "$name" '.[] | select(.name == $n) | .collectionId' | head -1)
  if [[ -z "$id" ]]; then
    # The grants array is authoritative: include the creator as owner (roleId 4)
    id=$(api POST "/collections" -H 'Content-Type: application/json' -d "$(jq -nc --arg n "$name" --arg uid "$MY_UID_STIGMAN" \
      '{name: $n, description: "Lab demo collection", metadata: {}, grants: [{userId: $uid, roleId: 4}], labels: []}')" \
      | jq -r .collectionId)
  fi
  # Repair path: make sure stigadmin holds an owner grant even if the
  # collection pre-existed without one.
  if ! api GET "/collections/$id/grants?elevate=true" | jq -e --arg uid "$MY_UID_STIGMAN" '.[] | select(.user.userId == $uid)' >/dev/null; then
    api POST "/collections/$id/grants?elevate=true" -H 'Content-Type: application/json' \
      -d "[{\"userId\": \"$MY_UID_STIGMAN\", \"roleId\": 4}]" >/dev/null
  fi
  echo "$id"
}

ensure_asset() { # COLLECTION_ID NAME IP BENCHMARK -> assetId
  local cid="$1" name="$2" ip="$3" bench="$4" id
  id=$(api GET "/assets?collectionId=$cid" | jq -r --arg n "$name" '.[] | select(.name == $n) | .assetId' | head -1)
  if [[ -z "$id" ]]; then
    id=$(api POST "/assets" -H 'Content-Type: application/json' -d "$(jq -nc \
      --arg cid "$cid" --arg n "$name" --arg ip "$ip" --arg b "$bench" \
      '{name: $n, collectionId: $cid, description: "lab asset", ip: $ip, noncomputing: false, metadata: {}, stigs: [$b]}')" \
      | jq -r .assetId)
  fi
  echo "$id"
}

# post_reviews CID ASSET BENCH assessedPct failHigh failMed failLow naCount
# Deterministically slices the rule list: fails first, then N/A, then passes,
# leaving (100-assessedPct)% of rules unassessed.
post_reviews() {
  local cid="$1" aid="$2" bench="$3" pct="$4" fh="$5" fm="$6" fl="$7" na="$8"
  local rules reviews
  rules=$(api GET "/stigs/$bench/revisions/latest/rules")
  reviews=$(echo "$rules" | jq -c --argjson pct "$pct" --argjson fh "$fh" --argjson fm "$fm" --argjson fl "$fl" --argjson na "$na" '
    def take_sev(sev; n): [.[] | select(.severity == sev)][:n];
    . as $all
    | (take_sev("high"; $fh) + take_sev("medium"; $fm) + take_sev("low"; $fl)) as $fails
    | ($fails | map(.ruleId)) as $failIds
    | [$all[] | select(.ruleId as $r | $failIds | index($r) | not)] as $rest
    | ($all | length) as $total
    | (($total * $pct / 100 | floor)) as $target
    | ($rest[:$na]) as $nas
    | ($rest[$na:] | .[: ([$target - ($fails|length) - $na, 0] | max)]) as $passes
    | ($fails | map({ruleId: .ruleId, result: "fail", detail: "Lab finding: control not implemented.", comment: "Seeded by seed-test-data.sh", status: "submitted"}))
      + ($nas | map({ruleId: .ruleId, result: "notapplicable", detail: "Lab: control not applicable to this asset.", comment: "Seeded", status: "submitted"}))
      + ($passes | map({ruleId: .ruleId, result: "pass", detail: "Lab: control verified.", comment: "Seeded", status: "saved"}))
  ')
  local n
  n=$(echo "$reviews" | jq 'length')
  echo "  asset $aid: posting $n reviews (target ${pct}% assessed; fails H:$fh M:$fm L:$fl; NA:$na)"
  echo "$reviews" | api POST "/collections/$cid/reviews/$aid" -H 'Content-Type: application/json' -d @- | \
    jq -c '{affected: (.affected // .)}' >/dev/null
}

echo "creating collections ..."
LINUX_CID=$(ensure_collection "Linux Production")
WIN_CID=$(ensure_collection "Windows Production")
echo "Linux Production = collection $LINUX_CID ; Windows Production = collection $WIN_CID"

echo "creating assets ..."
L1=$(ensure_asset "$LINUX_CID" linux-web-01 10.10.10.11 "$RHEL_BENCH")
L2=$(ensure_asset "$LINUX_CID" linux-db-01  10.10.10.12 "$RHEL_BENCH")
L3=$(ensure_asset "$LINUX_CID" linux-app-01 10.10.10.13 "$RHEL_BENCH")
W1=$(ensure_asset "$WIN_CID" win-dc-01 10.20.20.11 "$WIN_BENCH")
W2=$(ensure_asset "$WIN_CID" win-fs-01 10.20.20.12 "$WIN_BENCH")

echo "posting reviews (mixed results, partial coverage) ..."
post_reviews "$LINUX_CID" "$L1" "$RHEL_BENCH" 85 2 10 3 6
post_reviews "$LINUX_CID" "$L2" "$RHEL_BENCH" 60 1 5 2 4
post_reviews "$LINUX_CID" "$L3" "$RHEL_BENCH" 40 0 8 5 2
post_reviews "$WIN_CID" "$W1" "$WIN_BENCH" 90 5 20 2 8
post_reviews "$WIN_CID" "$W2" "$WIN_BENCH" 50 3 10 1 3

echo
echo "seeded:"
echo "  Linux Production   collectionId=$LINUX_CID  assets: $L1,$L2,$L3  ($RHEL_BENCH)"
echo "  Windows Production collectionId=$WIN_CID  assets: $W1,$W2  ($WIN_BENCH)"
echo
echo "Next: ./scripts/grant-reporter-access.sh $LINUX_CID && ./scripts/grant-reporter-access.sh $WIN_CID"
