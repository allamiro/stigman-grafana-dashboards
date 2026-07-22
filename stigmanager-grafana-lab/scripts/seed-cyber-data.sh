#!/usr/bin/env bash
# Additive seed for the cyber-analyst dashboards:
#   - imports a third benchmark (VPN SRG)
#   - creates two more collections with LABELS (site/os/dept) applied to
#     assets: "Network Infrastructure" (VPN SRG) and
#     "Corporate Workstations" (Windows 10)
#   - posts reviews with the full workflow-status mix: saved, submitted,
#     accepted and rejected
#   - adds os labels to the two original collections so label drill-down
#     works everywhere
#   - grants nexus-reporter read-only access to the new collections
# Safe to re-run.
set -euo pipefail
cd "$(dirname "$0")/.."
source .env

KEYCLOAK_URL="${KEYCLOAK_URL:-http://localhost:8180}"
STIGMAN_URL="${STIGMAN_URL:-http://localhost:54000}"
FIXTURE_BASE="https://raw.githubusercontent.com/NUWCDIVNPT/stig-manager/main/test/api/form-data-files"
VPN_XML="U_VPN_SRG_V1R1_Manual-xccdf.xml"

mkdir -p test-data
[[ -s "test-data/$VPN_XML" ]] || curl -sfL -o "test-data/$VPN_XML" "$FIXTURE_BASE/$VPN_XML"

TOKEN=$(curl -sf -X POST "$KEYCLOAK_URL/realms/stigman/protocol/openid-connect/token" \
  -d grant_type=password -d client_id=stig-manager \
  -d username="$STIGMAN_ADMIN_USERNAME" -d password="$STIGMAN_ADMIN_PASSWORD" \
  -d 'scope=openid stig-manager:collection stig-manager:stig stig-manager:user stig-manager:op' \
  | jq -r .access_token)
[[ -n "$TOKEN" && "$TOKEN" != "null" ]] || { echo "no admin token" >&2; exit 1; }
AUTH=(-H "Authorization: Bearer $TOKEN")
api() { local m="$1" p="$2"; shift 2; curl -sf -X "$m" "${AUTH[@]}" "$STIGMAN_URL/api$p" "$@"; }

echo "importing VPN SRG benchmark ..."
api POST "/stigs?elevate=true&clobber=true" -F "importFile=@test-data/$VPN_XML;type=text/xml" >/dev/null
VPN_BENCH=$(api GET "/stigs" | jq -r '.[] | select(.benchmarkId | test("VPN")) | .benchmarkId' | head -1)
WIN_BENCH=$(api GET "/stigs" | jq -r '.[] | select(.benchmarkId | test("Windows_10")) | .benchmarkId' | head -1)
RHEL_BENCH=$(api GET "/stigs" | jq -r '.[] | select(.benchmarkId | test("RHEL_7")) | .benchmarkId' | head -1)
echo "benchmarks: $VPN_BENCH / $WIN_BENCH / $RHEL_BENCH"

MY_UID_STIGMAN=$(api GET "/user" | jq -r .userId)

ensure_collection() { # NAME LABELS_JSON -> collectionId
  local name="$1" labels="$2" id
  id=$(api GET "/collections?elevate=true" | jq -r --arg n "$name" '.[] | select(.name == $n) | .collectionId' | head -1)
  if [[ -z "$id" ]]; then
    id=$(api POST "/collections" -H 'Content-Type: application/json' -d "$(jq -nc \
      --arg n "$name" --arg uid "$MY_UID_STIGMAN" --argjson labels "$labels" \
      '{name: $n, description: "Cyber lab collection", metadata: {},
        grants: [{userId: $uid, roleId: 4}], labels: $labels,
        settings: {fields: {detail: {enabled: "always", required: "optional"},
                            comment: {enabled: "always", required: "optional"}},
                   status: {canAccept: true, minAcceptGrant: 3, resetCriteria: "result"},
                   history: {maxReviews: 5}}}')" | jq -r .collectionId)
  fi
  echo "$id"
}

ensure_label() { # CID NAME COLOR -> ensures label exists on collection
  local cid="$1" name="$2" color="$3"
  api GET "/collections/$cid/labels" | jq -e --arg n "$name" '.[] | select(.name == $n)' >/dev/null 2>&1 || \
    api POST "/collections/$cid/labels" -H 'Content-Type: application/json' \
      -d "$(jq -nc --arg n "$name" --arg c "$color" '{name: $n, description: "", color: $c}')" >/dev/null
}

ensure_asset() { # CID NAME IP BENCH LABELS_CSV -> assetId
  local cid="$1" name="$2" ip="$3" bench="$4" labels="$5" id
  id=$(api GET "/assets?collectionId=$cid" | jq -r --arg n "$name" '.[] | select(.name == $n) | .assetId' | head -1)
  if [[ -z "$id" ]]; then
    id=$(api POST "/assets" -H 'Content-Type: application/json' -d "$(jq -nc \
      --arg cid "$cid" --arg n "$name" --arg ip "$ip" --arg b "$bench" --arg l "$labels" \
      '{name: $n, collectionId: $cid, description: "cyber lab asset", ip: $ip,
        noncomputing: false, metadata: {},
        labelNames: ($l | split(",") | map(select(length > 0))),
        stigs: [$b]}')" | jq -r .assetId)
  fi
  echo "$id"
}

# post_reviews CID AID BENCH assessedPct fH fM fL na  — full status mix:
# fails: 1 rejected, ~30% accepted, rest submitted; NA: submitted;
# passes: ~20% accepted, ~30% submitted, rest saved.
post_reviews() {
  local cid="$1" aid="$2" bench="$3" pct="$4" fh="$5" fm="$6" fl="$7" na="$8"
  local rules reviews
  rules=$(api GET "/stigs/$bench/revisions/latest/rules")
  reviews=$(echo "$rules" | jq -c --argjson pct "$pct" --argjson fh "$fh" \
      --argjson fm "$fm" --argjson fl "$fl" --argjson na "$na" '
    def take_sev(sev; n): [.[] | select(.severity == sev)][:n];
    . as $all
    | (take_sev("high"; $fh) + take_sev("medium"; $fm) + take_sev("low"; $fl)) as $fails
    | ($fails | map(.ruleId)) as $failIds
    | [$all[] | select(.ruleId as $r | $failIds | index($r) | not)] as $rest
    | ($all | length) as $total
    | (($total * $pct / 100 | floor)) as $target
    | ($rest[:$na]) as $nas
    | ($rest[$na:] | .[: ([$target - ($fails|length) - $na, 0] | max)]) as $passes
    | ($fails | to_entries | map(
        {ruleId: .value.ruleId, result: "fail",
         detail: "Cyber lab finding.", comment: "Seeded",
         status: (if .key == 0
                  then {label: "rejected", text: "Mitigation detail insufficient — resubmit."}
                  elif .key % 3 == 1 then "accepted"
                  else "submitted" end)}))
      + ($nas | map({ruleId: .ruleId, result: "notapplicable",
                     detail: "Not applicable to this asset.", comment: "Seeded",
                     status: "submitted"}))
      + ($passes | to_entries | map(
        {ruleId: .value.ruleId, result: "pass",
         detail: "Verified.", comment: "Seeded",
         status: (if .key % 5 == 0 then "accepted"
                  elif .key % 3 == 0 then "submitted"
                  else "saved" end)}))
  ')
  local n; n=$(echo "$reviews" | jq 'length')
  echo "  asset $aid: $n reviews (${pct}% assessed, statuses mixed)"
  if ! echo "$reviews" | api POST "/collections/$cid/reviews/$aid" \
      -H 'Content-Type: application/json' -d @- >/dev/null 2>&1; then
    echo "  (accepted/rejected batch refused — retrying with submitted only)"
    echo "$reviews" | jq -c 'map(.status = (if (.status|type) == "object" then "submitted" else .status end))' | \
      api POST "/collections/$cid/reviews/$aid" -H 'Content-Type: application/json' -d @- >/dev/null
  fi
}

echo "creating collections with labels ..."
NET_CID=$(ensure_collection "Network Infrastructure" \
  '[{"name":"os:appliance","description":"","color":"99ccff"},{"name":"site:hq","description":"","color":"cc99ff"},{"name":"site:dr","description":"","color":"99e6cc"}]')
WS_CID=$(ensure_collection "Corporate Workstations" \
  '[{"name":"os:windows","description":"","color":"99ccff"},{"name":"dept:hr","description":"","color":"ffcc99"},{"name":"dept:eng","description":"","color":"c2f0c2"}]')
echo "Network Infrastructure = $NET_CID ; Corporate Workstations = $WS_CID"

echo "labeling the original collections ..."
for cid_label in "1:os:linux" "2:os:windows"; do
  cid="${cid_label%%:*}"; label="${cid_label#*:}"
  ensure_label "$cid" "$label" "99ccff"
  LID=$(api GET "/collections/$cid/labels" | jq -r --arg n "$label" '.[] | select(.name == $n) | .labelId')
  AIDS=$(api GET "/assets?collectionId=$cid" | jq -c '[.[].assetId]')
  api PUT "/collections/$cid/labels/$LID/assets" -H 'Content-Type: application/json' -d "$AIDS" >/dev/null
done

echo "creating assets ..."
N1=$(ensure_asset "$NET_CID" vpn-gw-01   10.30.30.11 "$VPN_BENCH" "os:appliance,site:hq")
N2=$(ensure_asset "$NET_CID" vpn-gw-02   10.30.30.12 "$VPN_BENCH" "os:appliance,site:dr")
N3=$(ensure_asset "$NET_CID" fw-mgmt-01  10.30.30.13 "$VPN_BENCH" "os:appliance,site:hq")
W1=$(ensure_asset "$WS_CID" ws-hr-01   10.40.40.11 "$WIN_BENCH" "os:windows,dept:hr")
W2=$(ensure_asset "$WS_CID" ws-eng-01  10.40.40.12 "$WIN_BENCH" "os:windows,dept:eng")
W3=$(ensure_asset "$WS_CID" ws-eng-02  10.40.40.13 "$WIN_BENCH" "os:windows,dept:eng")

echo "posting reviews with mixed workflow statuses ..."
post_reviews "$NET_CID" "$N1" "$VPN_BENCH" 95 2 8 1 4
post_reviews "$NET_CID" "$N2" "$VPN_BENCH" 70 1 5 2 3
post_reviews "$NET_CID" "$N3" "$VPN_BENCH" 45 3 6 0 2
post_reviews "$WS_CID" "$W1" "$WIN_BENCH" 80 1 12 2 5
post_reviews "$WS_CID" "$W2" "$WIN_BENCH" 65 4 15 3 4
post_reviews "$WS_CID" "$W3" "$WIN_BENCH" 30 2 6 1 2

echo "granting nexus-reporter read-only access ..."
./scripts/grant-reporter-access.sh "$NET_CID"
./scripts/grant-reporter-access.sh "$WS_CID"

echo
echo "seeded: Network Infrastructure=$NET_CID ($VPN_BENCH), Corporate Workstations=$WS_CID ($WIN_BENCH)"
