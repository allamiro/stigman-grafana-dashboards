#!/usr/bin/env bash
# Run the full validation suite in order and print a final summary.
# Exits nonzero if any required test fails.
set -uo pipefail
cd "$(dirname "$0")/.."

declare -a NAMES RESULTS
run() { # run "Summary name" command...
  local name="$1"; shift
  echo
  echo "===================================================================="
  echo ">> $name"
  echo "===================================================================="
  if "$@"; then
    NAMES+=("$name"); RESULTS+=("PASS")
  else
    NAMES+=("$name"); RESULTS+=("FAIL")
  fi
}

run "Stack health"                 ./scripts/wait-for-stack.sh
run "Keycloak authentication"      ./scripts/test-keycloak.sh
run "STIG Manager API"             bash -c 'curl -sf http://localhost:54000/api/op/configuration | jq -e .version >/dev/null && echo "API version: $(curl -sf http://localhost:54000/api/op/configuration | jq -r .version)"'
run "Service account token"        ./scripts/test-keycloak.sh
run "Collection visibility"        ./scripts/test-service-account.sh
run "Collection metrics"           ./scripts/test-collection-metrics.sh "${1:-1}"
run "Grafana datasource"           ./scripts/test-grafana.sh
run "Per-collection dashboard"     python3 ./scripts/validate-dashboard-queries.py stig-posture-collection
run "Enterprise dashboard"         python3 ./scripts/validate-dashboard-queries.py stig-posture-enterprise

echo
echo "======================= SUMMARY ======================="
fail=0
for i in "${!NAMES[@]}"; do
  echo "${RESULTS[$i]}: ${NAMES[$i]}"
  [[ "${RESULTS[$i]}" == "FAIL" ]] && fail=1
done
exit $fail
