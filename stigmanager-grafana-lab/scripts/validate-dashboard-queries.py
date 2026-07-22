#!/usr/bin/env python3
"""Execute every panel query of a provisioned dashboard through Grafana's
/api/ds/query, substituting template variables with real values, and fail if
any panel returns an error or an empty result.

Usage: python3 scripts/validate-dashboard-queries.py <dashboard-uid> [...]
"""
import base64
import copy
import json
import pathlib
import sys
import urllib.request

GRAFANA = "http://localhost:3200"
ROOT = pathlib.Path(__file__).resolve().parent.parent

env = {}
for line in (ROOT / ".env").read_text().splitlines():
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
AUTH = "Basic " + base64.b64encode(
    f"{env['GRAFANA_ADMIN_USER']}:{env['GRAFANA_ADMIN_PASSWORD']}".encode()).decode()


def http_json(path, body=None):
    req = urllib.request.Request(
        GRAFANA + path,
        data=json.dumps(body).encode() if body else None,
        headers={"Content-Type": "application/json", "Authorization": AUTH})
    with urllib.request.urlopen(req) as r:
        return json.load(r)


def substitute(target, cids):
    s = json.dumps(target)
    quoted = ",".join(f"'{c}'" for c in cids)
    s = s.replace("${collections:singlequote}", quoted)
    s = s.replace("${collections:text}", "substituted")
    s = s.replace("${collections}", cids[0])
    s = s.replace("$collection", cids[0])
    return json.loads(s)


def main():
    # discover collection ids the way the dashboard variable does
    var_q = {
        "refId": "v", "datasource": {"type": "yesoreyeram-infinity-datasource",
                                     "uid": "stigmanager-infinity"},
        "type": "json", "source": "url", "format": "table", "parser": "backend",
        "url": "http://stigman:54000/api/collections",
        "url_options": {"method": "GET", "data": ""}, "root_selector": "",
        "columns": [{"selector": "collectionId", "text": "id", "type": "string"}]}
    resp = http_json("/api/ds/query", {"queries": [var_q], "from": "now-1h", "to": "now"})
    cids = resp["results"]["v"]["frames"][0]["data"]["values"][0]
    if not cids:
        print("FAIL: variable query returned no collections")
        return 1
    print(f"collection variable resolves to: {cids}")

    failures = 0
    for uid in sys.argv[1:]:
        dash = http_json(f"/api/dashboards/uid/{uid}")["dashboard"]
        print(f"== {dash['title']} ({uid})")
        for panel in dash.get("panels", []):
            for target in panel.get("targets", []):
                q = substitute(copy.deepcopy(target), cids)
                try:
                    r = http_json("/api/ds/query",
                                  {"queries": [q], "from": "now-1h", "to": "now"})
                    res = r["results"][q["refId"]]
                    frames = res.get("frames", [])
                    values = frames[0]["data"]["values"] if frames else []
                    ok = (res.get("status") == 200 and not res.get("error")
                          and any(v for v in values))
                except Exception as exc:  # noqa: BLE001
                    ok, res = False, {"error": str(exc)}
                mark = "ok" if ok else "FAIL"
                print(f"  [{mark}] {panel['title']}")
                if not ok:
                    print(f"        {res.get('error')}")
                    failures += 1
    if failures:
        print(f"FAIL: {failures} panel queries failed")
        return 1
    print("PASS: all panel queries returned data")
    return 0


if __name__ == "__main__":
    sys.exit(main())
