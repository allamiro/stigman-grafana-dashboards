#!/usr/bin/env bash
# Verify Grafana: health, Infinity plugin, datasource provisioning + UID,
# dashboard provisioning + UIDs, and that Grafana can query STIG Manager
# through the Infinity datasource (server-side, via /api/ds/query).
set -euo pipefail
cd "$(dirname "$0")/.."
source .env

GRAFANA_URL="${GRAFANA_URL:-http://localhost:3200}"
CRED="$GRAFANA_ADMIN_USER:$GRAFANA_ADMIN_PASSWORD"
DS_UID="stigmanager-infinity"
fail=0
check() { if [[ "$2" == "0" ]]; then echo "  ok: $1"; else echo "  FAIL: $1"; fail=1; fi; }

echo "[1/5] /api/health"
H=$(curl -sf "$GRAFANA_URL/api/health")
[[ $(echo "$H" | jq -r .database) == "ok" ]]; check "database ok (version $(echo "$H" | jq -r .version))" $?

echo "[2/5] Infinity plugin"
P=$(curl -sf -u "$CRED" "$GRAFANA_URL/api/plugins/yesoreyeram-infinity-datasource/settings")
[[ -n "$P" ]]; check "plugin installed (version $(echo "$P" | jq -r .info.version))" $?

echo "[3/5] datasource provisioning"
DS=$(curl -sf -u "$CRED" "$GRAFANA_URL/api/datasources/uid/$DS_UID")
[[ $(echo "$DS" | jq -r .uid) == "$DS_UID" ]]; check "datasource uid = $DS_UID" $?
[[ $(echo "$DS" | jq -r .jsonData.auth_method) == "oauth2" ]]; check "auth_method = oauth2" $?
[[ $(echo "$DS" | jq -r .jsonData.oauth2.oauth2_type) == "client_credentials" ]]; check "grant = client_credentials" $?
[[ $(echo "$DS" | jq -r '.jsonData.allowedHosts[0]') == "http://stigman:54000" ]]; check "allowed host = http://stigman:54000" $?
[[ $(echo "$DS" | jq -r .secureJsonFields.oauth2ClientSecret) == "true" ]]; check "client secret stored in secure JSON" $?

echo "[4/5] dashboards"
for uid in stig-posture-collection stig-posture-enterprise; do
  D=$(curl -sf -u "$CRED" "$GRAFANA_URL/api/dashboards/uid/$uid") || { check "dashboard $uid" 1; continue; }
  title=$(echo "$D" | jq -r .dashboard.title)
  check "dashboard $uid (\"$title\")" 0
  n=$(echo "$D" | jq '[.dashboard.panels[].targets[]?.datasource.uid] | map(select(. != "'$DS_UID'")) | length')
  [[ "$n" == "0" ]]; check "  all panel queries use uid $DS_UID" $?
done

echo "[5/5] end-to-end query through Infinity (server-side)"
R=$(curl -sf -u "$CRED" -H 'Content-Type: application/json' "$GRAFANA_URL/api/ds/query" -d '{
  "queries": [{"refId":"A","datasource":{"type":"yesoreyeram-infinity-datasource","uid":"'$DS_UID'"},
  "type":"json","source":"url","format":"table","parser":"backend",
  "url":"http://stigman:54000/api/collections","url_options":{"method":"GET","data":""},
  "root_selector":"","columns":[{"selector":"collectionId","text":"collectionId","type":"string"},
  {"selector":"name","text":"name","type":"string"}]}],"from":"now-1h","to":"now"}')
STATUS=$(echo "$R" | jq -r '.results.A.status')
ROWS=$(echo "$R" | jq -r '.results.A.frames[0].data.values[0] | length')
[[ "$STATUS" == "200" ]]; check "query status 200" $?
[[ "$ROWS" -ge 1 ]]; check "collections returned through Infinity: $ROWS" $?
echo "  collections: $(echo "$R" | jq -c '.results.A.frames[0].data.values[1]')"

[[ $fail -eq 0 ]] && echo "PASS: Grafana datasource and dashboards" || { echo "FAIL: Grafana checks failed"; exit 1; }
