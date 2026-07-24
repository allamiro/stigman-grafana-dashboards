# STIG Manager posture exporter

A tiny Prometheus exporter. On every scrape it authenticates to Keycloak with
the read-only `nexus-reporter` service account (OAuth2 client-credentials),
calls STIG Manager's metrics endpoint, and exposes per-collection posture
gauges. Prometheus stores the history so point-in-time posture becomes trends.

- Code: [`stigman_exporter.py`](stigman_exporter.py)
- Config template: [`.env.example`](.env.example)
- Metrics: `http://<host>:9633/metrics`

## Configuration

Everything is read from environment variables — **nothing is baked into the
image**. Required: `STIGMAN_API_URL`, `KEYCLOAK_TOKEN_URL`, `OIDC_CLIENT_SECRET`.

| Variable | Required | Default | Notes |
|---|---|---|---|
| `STIGMAN_API_URL` | yes | — | e.g. `http://stigman:54000/api` |
| `KEYCLOAK_TOKEN_URL` | yes | — | `…/realms/<realm>/protocol/openid-connect/token` |
| `OIDC_CLIENT_SECRET` | yes | — | the `nexus-reporter` client secret |
| `OIDC_CLIENT_ID` | no | `nexus-reporter` | override only if your client is named differently |
| `EXPORTER_PORT` | no | `9633` | |
| `REQUEST_TIMEOUT` | no | `15` | seconds |
| `STIGMAN_VERIFY_TLS` | no | `true` | `true` / `false` / path to a CA bundle. Global default for both calls |
| `KEYCLOAK_VERIFY_TLS` | no | *(falls back to `STIGMAN_VERIFY_TLS`)* | per-endpoint override for the Keycloak call |
| `STIGMAN_API_VERIFY_TLS` | no | *(falls back to `STIGMAN_VERIFY_TLS`)* | per-endpoint override for the STIG Manager call |

See [`.env.example`](.env.example) for a copy-paste starting point.

## Running it

**In the lab compose stack** — nothing to do; the `stigman-exporter` service
gets its variables from the top-level `.env`.

**Standalone with an env file:**

```bash
cp .env.example .env      # then edit .env and set OIDC_CLIENT_SECRET
docker run -d --name stigman-exporter -p 9633:9633 --env-file .env \
  stigman-exporter:1.2.0
```

**Standalone with Docker Compose** (this directory has its own
[`docker-compose.yml`](docker-compose.yml) for running just the exporter):

```bash
cp .env.example .env      # then edit .env and set OIDC_CLIENT_SECRET
docker compose up -d      # builds the image and starts it on :9633
docker compose logs -f    # watch it authenticate + scrape
curl http://localhost:9633/metrics
```

The compose file reads `.env` via `env_file:` — same **no-quotes** format as
below. To use the published image instead of building, uncomment the
`image: ghcr.io/allamiro/stigman-exporter:1.2.0` line in the compose file.

**Standalone with `-e` flags** (let the shell expand the secret):

```bash
docker run -d --name stigman-exporter -p 9633:9633 \
  -e STIGMAN_API_URL=http://host.docker.internal:54000/api \
  -e KEYCLOAK_TOKEN_URL=http://host.docker.internal:8180/realms/stigman/protocol/openid-connect/token \
  -e OIDC_CLIENT_ID=nexus-reporter \
  -e OIDC_CLIENT_SECRET="$NEXUS_REPORTER_CLIENT_SECRET" \
  stigman-exporter:1.2.0
```

**Bare-metal with systemd** (an env file read by systemd, not Docker):

```ini
# /etc/systemd/system/stigman-exporter.service
[Service]
EnvironmentFile=/etc/stigman-exporter.env    # same KEY=value format, no quotes
ExecStart=/usr/bin/python3 /opt/stigman-exporter/stigman_exporter.py
Restart=always
User=stigman-exporter
```

```bash
cp .env.example /etc/stigman-exporter.env    # edit it, set OIDC_CLIENT_SECRET
sudo systemctl daemon-reload && sudo systemctl enable --now stigman-exporter
```

> `EnvironmentFile=` follows the **same no-quotes rule** as Docker's
> `--env-file` — systemd does not run a shell over it either.

## Applying changes to the env file (recreate — don't just restart)

Environment variables are injected when the container is **created**, not when
it starts. So after editing `.env`, `docker restart` will **not** pick up the
new values — it reuses the env the container was created with. You have to
**recreate** the container:

```bash
# Compose (recommended): recreates from the updated .env
docker compose up -d --force-recreate

# Plain docker run: remove and run again
docker rm -f stigman-exporter
docker run -d --name stigman-exporter -p 9633:9633 --env-file .env stigman-exporter:1.2.0
```

(`docker compose up -d` on recent Compose usually detects the `.env` change and
recreates on its own; `--force-recreate` guarantees it across versions.)

## ⚠️ Env-file rules (this is the #1 cause of `invalid_client`)

A Docker `--env-file` (and compose `env_file:`) is **not** parsed by a shell.
Docker takes the text **after the first `=` literally** — it does not strip
quotes, spaces, or inline comments. So:

```
OIDC_CLIENT_SECRET=675524...afe6a0        ✅ correct
OIDC_CLIENT_SECRET="675524...afe6a0"      ❌ the quotes become part of the secret
OIDC_CLIENT_SECRET=675524...afe6a0        # ok on its own line
OIDC_CLIENT_SECRET=675524...afe6a0 # note ❌ " # note" becomes part of the secret
OIDC_CLIENT_SECRET = 675524...            ❌ leading space becomes part of the secret
```

Rules for an env file:
- **No quotes.** The secret is plain hex — it never needs them.
- **No spaces** around `=`, and none trailing the value.
- **No inline comments.** Comments must be on their own line (`# like this`).

(Quotes are only harmless when a *shell* reads the file — `source .env`,
`export`, or the `-e VAR="$X"` form above — because bash strips them. Docker's
`--env-file` does not.)

## Troubleshooting

### `401 invalid_client` from Keycloak

The token call the exporter makes is identical to this curl — so if the curl
works but the exporter doesn't, the **values** differ, not the code:

```bash
curl -s "$KEYCLOAK_TOKEN_URL" \
  -d grant_type=client_credentials \
  -d client_id=nexus-reporter \
  -d client_secret='<secret>' | jq
```

Check, in order:

1. **`OIDC_CLIENT_ID` is exactly `nexus-reporter`.** The curl above hardcodes
   it; the exporter reads it from the env. A wrong/typo'd client id gives the
   same `invalid_client`.
2. **The secret byte-matches** the one Keycloak stores — no quotes, spaces, or
   inline comment picked up from the env file (see the rules above). Reveal
   hidden characters with `od -c`:
   ```bash
   docker exec stigman-exporter printenv OIDC_CLIENT_SECRET | od -c | tail -2
   ```
3. **`KEYCLOAK_TOKEN_URL`** points at the right realm/host.

Definitive test — make the container run the exporter's exact token call with
its own env, and read what Keycloak says:

```bash
docker exec stigman-exporter python -c '
import os, requests
r = requests.post(os.environ["KEYCLOAK_TOKEN_URL"], data={
    "grant_type":"client_credentials",
    "client_id":os.environ.get("OIDC_CLIENT_ID","nexus-reporter"),
    "client_secret":os.environ["OIDC_CLIENT_SECRET"]})
print(r.status_code, r.text[:200])'
```

### Token OK but `403` on the API

The `nexus-reporter` account has no read grant on the collection. Add a
read-only grant (lab: `./scripts/grant-reporter-access.sh <collectionId>`).

### `SSLError` / certificate errors

Only when your URLs are `https`. Set `STIGMAN_VERIFY_TLS` (or the per-endpoint
`KEYCLOAK_VERIFY_TLS` / `STIGMAN_API_VERIFY_TLS`) to a CA-bundle path, or to
`false` for a lab with self-signed certs.

### Missing required environment variables (exit 2)

`OIDC_CLIENT_SECRET`, `STIGMAN_API_URL`, or `KEYCLOAK_TOKEN_URL` isn't set.
Remember the exporter reads **`OIDC_CLIENT_SECRET`** — not
`NEXUS_REPORTER_CLIENT_SECRET` (that lab variable is mapped to it by compose).
