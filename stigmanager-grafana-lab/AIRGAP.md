# Air-gapped deployment guide

How to deploy this reporting stack on a disconnected network. Replace the
example hostnames with yours throughout:

```
STIG Manager : https://stigman.example.internal        (API at /api)
Keycloak     : https://sso.example.internal            (realm: stigman)
Grafana      : https://grafana.example.internal
Prometheus   : runs on your monitoring server
```

Data flow (nothing pushes; Prometheus pulls):

```
Grafana ──(Infinity, OAuth2 client-credentials)──> STIG Manager API   [live dashboards]
Grafana ──(PromQL)──> Prometheus ──scrape :9633──> stigman_exporter ──> Keycloak + STIG Manager API   [trends/snapshots]
```

---

## 0. What to carry across the air gap

| Item | Source (connected side) |
|---|---|
| This repository | `git clone` / zip |
| Exporter Python deps | `pip download -r metrics-history/exporter/requirements.txt -d wheels/` (run on the same OS/arch/Python as the target, e.g. RHEL x86_64 / Python 3.9+) |
| *(alternative)* exporter container image | `docker build -t stigman-exporter:1.0.0 metrics-history/exporter && docker save stigman-exporter:1.0.0 -o stigman-exporter-1.0.0.tar` |
| Infinity plugin zip | `curl -Lo infinity-3.11.1.zip "https://grafana.com/api/plugins/yesoreyeram-infinity-datasource/versions/3.11.1/download"` |
| (if not already deployed) Grafana/Prometheus RPMs or images | vendor downloads |

---

## 1. Keycloak — create the reporting service account

In the Keycloak admin console, inside the realm STIG Manager uses
(shown here as `stigman`):

> **Order matters:** create the client scopes (steps 1–2) *before* the
> client (step 3). Client scopes are realm-level objects, and the client's
> *Add client scope* dialog can only attach scopes that already exist — if
> you create the client first you'll have to come back to it afterwards.

> **Existing STIG Manager realm? Steps 1–2 are usually just a check.**
> Client scopes are shared realm objects, and a realm that already serves
> the STIG Manager web client normally has the `stig-manager:*` scopes
> defined — so you only *attach* them to the new client (step 3), you don't
> re-create them. Verify three things instead of assuming:
>
> * The **`:read` variants** exist. Some setups defined only the write
>   scopes (`stig-manager:collection` etc.). Attach **only** `:read` scopes
>   to the reporter — never the write scopes. If the `:read` variants are
>   missing from the picker, create them per step 1.
> * The **audience scope is usually NOT pre-existing** — plain STIG Manager
>   setups often run without `STIGMAN_JWT_AUD_VALUE`. Check the STIG Manager
>   environment: if the variable is set, create/attach the audience scope
>   (step 2) or reporter tokens will fail audience validation; if it is not
>   set, skip step 2 entirely.
> * **The token is the proof** — after attaching scopes, run the smoke test
>   in step 4. `scope` must list the four `:read` scopes (if one is missing,
>   that scope has *Include in token scope = Off* — turn it on),
>   `preferred_username` must be `service-account-nexus-reporter`, and `aud`
>   must contain your `STIGMAN_JWT_AUD_VALUE` when one is configured.

1. **Client scopes** (skip any that already exist — the STIG Manager realm
   usually has them): *Client scopes → Create client scope*, protocol
   OpenID Connect, type *None*, name each of:
   `stig-manager:collection:read`, `stig-manager:stig:read`,
   `stig-manager:user:read`, `stig-manager:op:read`.
   In each scope's settings turn **Include in token scope = On**.
2. **Audience scope** (only if STIG Manager runs with
   `STIGMAN_JWT_AUD_VALUE`, recommended): create client scope
   `stig-manager-audience` → *Mappers → Add mapper → By configuration →
   Audience* → *Included Custom Audience* = the value of
   `STIGMAN_JWT_AUD_VALUE` (we use `stig-manager`), *Add to access token* = On.
3. **Client**: *Clients → Create client*
   - Client ID: `nexus-reporter`, type OpenID Connect
   - **Client authentication: On** (confidential)
   - Authentication flows: **Service accounts roles = On**; Standard flow
     **Off**; Direct access grants **Off**; Implicit **Off**
   - Save → *Credentials* tab → copy the **Client Secret**
   - *Client scopes* tab → *Add client scope* → add as **Default**:
     the four `...:read` scopes, `stig-manager-audience`, and `profile`
     (profile puts `preferred_username=service-account-nexus-reporter`
     in the token — STIG Manager uses it as the username).
   - Remove `roles`/`email` if you want the token minimal; set
     *Full scope allowed = Off* under *Client scopes → Setup* (or the
     client's *Capability config*) so realm roles never leak in.
4. **Smoke test** from any host that can reach Keycloak:

   ```bash
   curl -s https://sso.example.internal/realms/stigman/protocol/openid-connect/token \
     -d grant_type=client_credentials -d client_id=nexus-reporter \
     -d client_secret='<SECRET>' | jq -r .access_token | cut -d. -f2 \
     | base64 -d 2>/dev/null | jq '{iss, aud, preferred_username, scope}'
   ```

   Expect your issuer, `aud` containing `stig-manager`, username
   `service-account-nexus-reporter`, and only `...:read` scopes.

## 2. STIG Manager — trust and read-only grants

STIG Manager itself needs no new settings for the service account if it
already trusts this Keycloak realm. For reference, the relevant environment
variables (values must be URLs reachable *from the STIG Manager server* /
*from browsers* respectively):

```
STIGMAN_OIDC_PROVIDER=https://sso.example.internal/realms/stigman         # API-side metadata/JWKS
STIGMAN_CLIENT_OIDC_PROVIDER=https://sso.example.internal/realms/stigman  # browser-side (same URL when everyone resolves the FQDN)
STIGMAN_CLIENT_ID=stig-manager
STIGMAN_JWT_AUD_VALUE=stig-manager                                   # only with the audience mapper from step 1.2
```

Grant the service account **read-only** access to each collection you want
reported:

1. Make the user record exist: call the API once with a `nexus-reporter`
   token (`GET /api/collections` — a `200 []` response is expected and fine).

   ```bash
   # a. Fetch a service-account token (same client-credentials call as step 1.4)
   TOKEN=$(curl -s https://sso.example.internal/realms/stigman/protocol/openid-connect/token \
     -d grant_type=client_credentials -d client_id=nexus-reporter \
     -d client_secret='<SECRET>' | jq -r .access_token)

   # b. Hit the API once — a 200 with `[]` is correct and lazily creates the
   #    service-account-nexus-reporter user record (no grants yet)
   curl -s https://stigman.example.internal/api/collections \
     -H "Authorization: Bearer $TOKEN" | jq
   ```
2. In the STIG Manager UI as a collection Manage/Owner user:
   *Collection → Manage → Grants → New grant* → user
   `service-account-nexus-reporter` → role **Restricted** → then edit that
   grant's **Access Control List** and add one rule covering the whole
   collection with access **Read**.
3. Or scripted (the repo script honors env overrides):

   ```bash
   KEYCLOAK_URL=https://sso.example.internal STIGMAN_URL=https://stigman.example.internal \
     ./scripts/grant-reporter-access.sh <collectionId>
   ```

   (The script authenticates as an admin user via the `stig-manager` client's
   direct grant. If direct grants are disabled in production — they should
   be — use the UI path above instead.)

Verify with the same `$TOKEN` from step 1 — `GET /api/collections` now lists
the granted collections instead of `[]`, and the per-collection metrics
endpoint returns data:

```bash
# granted collections
curl -s https://stigman.example.internal/api/collections \
  -H "Authorization: Bearer $TOKEN" | jq

# metrics for one collection
curl -s "https://stigman.example.internal/api/collections/<id>/metrics/summary/collection" \
  -H "Authorization: Bearer $TOKEN" | jq
```

## 3. Exporter on the Prometheus server

The exporter is one file: `metrics-history/exporter/stigman_exporter.py`.
It listens on `:9633`; Prometheus scrapes it; on each scrape it fetches
current posture from STIG Manager. It never writes to Prometheus.

### 3a. Install (plain Python, no Docker)

```bash
sudo mkdir -p /opt/stigman-exporter
sudo cp stigman_exporter.py /opt/stigman-exporter/
sudo python3 -m venv /opt/stigman-exporter/venv
sudo /opt/stigman-exporter/venv/bin/pip install --no-index \
     --find-links /path/to/wheels prometheus-client requests
```

Config — `/etc/stigman-exporter.env` (root-owned, mode 0600):

```
STIGMAN_API_URL=https://stigman.example.internal/api
KEYCLOAK_TOKEN_URL=https://sso.example.internal/realms/stigman/protocol/openid-connect/token
OIDC_CLIENT_ID=nexus-reporter
OIDC_CLIENT_SECRET=<the secret from step 1.3>
EXPORTER_PORT=9633
# Private CA? Point requests at your bundle — do NOT disable verification:
# REQUESTS_CA_BUNDLE=/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem
```

Systemd unit — `/etc/systemd/system/stigman-exporter.service`:

```ini
[Unit]
Description=STIG Manager posture exporter for Prometheus
After=network-online.target
Wants=network-online.target

[Service]
EnvironmentFile=/etc/stigman-exporter.env
ExecStart=/opt/stigman-exporter/venv/bin/python /opt/stigman-exporter/stigman_exporter.py
DynamicUser=yes
NoNewPrivileges=yes
ProtectSystem=strict
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now stigman-exporter
curl -s http://localhost:9633/metrics | grep stigman_collection_cora_percent
```

### 3b. Or as a container

```bash
docker load -i stigman-exporter-1.0.0.tar
docker run -d --name stigman-exporter --restart unless-stopped -p 9633:9633 \
  --env-file /etc/stigman-exporter.env stigman-exporter:1.0.0
```

(Kubernetes: apply `metrics-history/kubernetes/` — namespace, secret,
deployment, service, optional ServiceMonitor.)

### 3c. Prometheus scrape config

Append to `/etc/prometheus/prometheus.yml` and reload:

```yaml
scrape_configs:
  - job_name: stigman-exporter
    scrape_interval: 60s
    scrape_timeout: 20s
    static_configs:
      - targets: ["localhost:9633"]   # wherever the exporter runs
```

Check *Status → Targets* shows the job **UP**, then query
`stigman_collection_cora_percent` in the Prometheus UI (the exporter also
records results, findings and per-severity assessment gauges, workflow
statuses via `stigman_collection_statuses{status=}`, and
`stigman_collection_last_touch_timestamp_seconds`). Set retention to
cover your reporting horizon (e.g. `--storage.tsdb.retention.time=395d`
for year-over-year).

## 4. Grafana — Infinity plugin, datasources, dashboards

### 4a. Install the Infinity plugin offline

```bash
# using the zip you carried over:
grafana-cli --pluginUrl /path/to/infinity-3.11.1.zip \
  plugins install yesoreyeram-infinity-datasource
# (or unzip it into /var/lib/grafana/plugins/ )
sudo systemctl restart grafana-server
```

### 4b. Infinity datasource (feeds the LIVE dashboards)

The Infinity datasource is what lets Grafana call the STIG Manager REST API
directly. Configure it by provisioning file (preferred — drop into
`/etc/grafana/provisioning/datasources/infinity.yml`) or the UI.

Provisioning file (this repo's `grafana/provisioning/datasources/infinity.yml`
adapted to real hostnames):

```yaml
apiVersion: 1
datasources:
  - name: STIG Manager
    uid: stigmanager-infinity          # dashboards reference this UID — keep it
    type: yesoreyeram-infinity-datasource
    access: proxy
    jsonData:
      auth_method: oauth2
      oauth2:
        oauth2_type: client_credentials
        client_id: nexus-reporter
        token_url: https://sso.example.internal/realms/stigman/protocol/openid-connect/token
      allowedHosts:
        - https://stigman.example.internal   # must match the URL the dashboards query
      oauthPassThru: false
      tlsSkipVerify: false              # never true in production
    secureJsonData:
      oauth2ClientSecret: $NEXUS_REPORTER_CLIENT_SECRET   # from Grafana's environment
```

UI equivalent: *Connections → Data sources → Add → Infinity* →
Authentication **OAuth2** → Grant type **Client Credentials** → Client ID
`nexus-reporter` → Client secret → Token URL as above → *Allowed hosts*:
`https://stigman.example.internal` → Save & test. Then set the UID: easiest is to
create it via provisioning so the UID is exactly `stigmanager-infinity`;
otherwise you must re-point the dashboards at your UID.

### 4c. Prometheus datasource (feeds trends + snapshots)

```yaml
apiVersion: 1
datasources:
  - name: STIG Posture History
    uid: stigmanager-prometheus        # trend/snapshot dashboards use this UID
    type: prometheus
    access: proxy
    url: https://prometheus.example.internal:9090
    jsonData:
      timeInterval: 60s
      httpMethod: POST
```

### 4d. Dashboards — fix the API URL, then import

There are **four dashboard folders**:

| Directory | Grafana folder | Datasource | Contains API URLs? |
|---|---|---|---|
| `grafana/dashboards/` | STIG Posture | Infinity | **yes** |
| `grafana/dashboards-cyber/` | STIG Posture (Cyber Analysis) | Infinity | **yes** |
| `grafana/dashboards-trends/` | STIG Posture (Trends) | Prometheus | no |
| `grafana/dashboards-snapshots/` | STIG Posture (History Snapshots) | Prometheus | no |

The Infinity dashboards embed the STIG Manager API base URL in every query
(in the lab it is `http://stigman:54000/api`). Point them at your API —
either regenerate (single source of truth; the other generators import the
enterprise one, so edit the URL constants in these files):

```bash
# edit API/META_URL "http://stigman:54000/api" -> your URL in
# scripts/update-enterprise-dashboard.py, then check the API constant in
# update-collection-dashboard.py and update-cyber-dashboards.py too:
python3 scripts/update-enterprise-dashboard.py
python3 scripts/update-executive-dashboard.py
python3 scripts/update-management-dashboard.py
python3 scripts/update-collection-dashboard.py
python3 scripts/update-cyber-dashboards.py
python3 scripts/update-trend-dashboards.py      # no URLs, safe to re-run
python3 scripts/update-snapshot-dashboards.py   # no URLs, safe to re-run
```

or just search-replace in the generated JSON:

```bash
sed -i 's|http://stigman:54000|https://stigman.example.internal|g' \
  grafana/dashboards/*.json grafana/dashboards-cyber/*.json
```

The replacement URL must match an entry in the Infinity datasource's
**allowed hosts** (4b). The Prometheus dashboards contain no URLs — only
the `stigmanager-prometheus` UID — and import unchanged.

Import: copy all four dashboard directories to the Grafana server and add
the four file providers exactly as in this repo's
`grafana/provisioning/dashboards/dashboards.yml` (one provider per folder),
or import each JSON by hand (*Dashboards → New → Import → paste JSON*).

**Collection selection needs no extra configuration**: the aggregate
dashboards' multi-select *Collections* picker and the per-collection /
cyber dropdowns populate themselves from `GET /api/collections` (Infinity)
or Prometheus label values — which means they only ever show collections
the `nexus-reporter` account has been **granted** (step 2). A collection
you never grant simply never appears on any dashboard.

## 5. End-to-end verification order

1. Token test (step 1.4) — issuer/aud/scopes correct.
2. `GET /api/collections` with the service token — collections listed.
3. `curl http://localhost:9633/metrics` on the Prometheus server — gauges
   present, `stigman_scrape_success 1`.
4. Prometheus *Status → Targets* — `stigman-exporter` UP; query
   `stigman_collection_findings`.
5. Grafana Infinity datasource *Save & test* → open the live Management
   Review dashboard — tiles populated.
6. Open a snapshot dashboard, set the time range to end an hour ago —
   values reflect that time.

## 6. Air-gap gotchas

* **TLS with a private CA**: set `REQUESTS_CA_BUNDLE` for the exporter and
  install the CA into the OS trust store on the Grafana host (Infinity uses
  Grafana's trust). Never ship `tlsSkipVerify: true`.
* **pip wheels are platform-specific** — run `pip download` on the same
  OS/arch/Python as the target, or use the container image instead.
* **History starts at first scrape** — deploy the exporter early; snapshots
  and trends cannot show dates before it started running.
* **Clock skew** breaks OAuth token validation — NTP everywhere.
* **Secret handling** — the client secret lives in `/etc/stigman-exporter.env`
  (0600) and Grafana's environment; rotate it in Keycloak *Credentials* and
  update both places.
