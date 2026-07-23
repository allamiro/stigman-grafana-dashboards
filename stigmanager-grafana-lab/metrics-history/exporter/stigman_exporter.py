#!/usr/bin/env python3
"""Prometheus exporter for STIG Manager posture metrics.

On every Prometheus scrape it authenticates to Keycloak with OAuth2
client-credentials (the read-only `nexus-reporter` service account), calls
STIG Manager's meta metrics endpoint, and exposes per-collection posture
gauges. Prometheus stores the history, which turns the point-in-time STIG
Manager API into trend data ("posture yesterday vs today").

Configuration (environment variables):
  STIGMAN_API_URL      e.g. http://localhost:54000/api   (required)
  KEYCLOAK_TOKEN_URL   e.g. http://localhost:8180/realms/stigman/protocol/openid-connect/token (required)
  OIDC_CLIENT_ID       default: nexus-reporter
  OIDC_CLIENT_SECRET   (required)
  EXPORTER_PORT        default: 9633
  REQUEST_TIMEOUT      seconds, default: 15
  STIGMAN_VERIFY_TLS   default: true. Set to a CA-bundle path (e.g.
                       /certs/internal-ca.pem) to trust an internal CA, or
                       "false" to disable TLS verification entirely (INSECURE
                       — lab/self-signed only, never production).

Run locally:   python3 stigman_exporter.py
Metrics at:    http://localhost:9633/metrics
"""
import logging
import os
import sys
import time

import requests
from prometheus_client import start_http_server
from prometheus_client.core import REGISTRY, GaugeMetricFamily

log = logging.getLogger("stigman-exporter")

API_URL = os.environ.get("STIGMAN_API_URL", "").rstrip("/")
TOKEN_URL = os.environ.get("KEYCLOAK_TOKEN_URL", "")
CLIENT_ID = os.environ.get("OIDC_CLIENT_ID", "nexus-reporter")
CLIENT_SECRET = os.environ.get("OIDC_CLIENT_SECRET", "")
PORT = int(os.environ.get("EXPORTER_PORT", "9633"))
TIMEOUT = float(os.environ.get("REQUEST_TIMEOUT", "15"))


def _verify_tls():
    """requests' `verify`: True/False, or a CA-bundle path string."""
    raw = os.environ.get("STIGMAN_VERIFY_TLS", "true").strip()
    if raw.lower() in ("false", "0", "no", "off"):
        return False
    if raw.lower() in ("true", "1", "yes", "on"):
        return True
    return raw  # treat as a path to a CA bundle


VERIFY = _verify_tls()
if VERIFY is False:
    # Silence the per-request InsecureRequestWarning spam once, up front.
    from urllib3.exceptions import InsecureRequestWarning
    requests.packages.urllib3.disable_warnings(InsecureRequestWarning)
    log.warning("TLS verification DISABLED (STIGMAN_VERIFY_TLS=false) — "
                "insecure, use only in a lab/self-signed environment")

CORA_WEIGHTS = {"high": 10.0, "medium": 4.0, "low": 1.0}


class TokenCache:
    def __init__(self):
        self._token = None
        self._expires_at = 0.0

    def get(self):
        if self._token and time.time() < self._expires_at - 60:
            return self._token
        resp = requests.post(TOKEN_URL, timeout=TIMEOUT, verify=VERIFY, data={
            "grant_type": "client_credentials",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET})
        resp.raise_for_status()
        body = resp.json()
        self._token = body["access_token"]
        self._expires_at = time.time() + float(body.get("expires_in", 300))
        return self._token


def cora_percent(findings, assessments, assessed):
    """User-specified CORA formula with zero-denominator guards, as a
    percentage. Same math as the Grafana dashboards."""
    score = 0.0
    for sev, weight in CORA_WEIGHTS.items():
        total = assessments.get(sev, 0)
        if total > 0:
            unassessed = total - assessed.get(sev, 0)
            score += (findings.get(sev, 0) + unassessed) / total * weight
    return score / sum(CORA_WEIGHTS.values()) * 100.0


class StigmanCollector:
    def __init__(self):
        self.tokens = TokenCache()

    def collect(self):
        up = GaugeMetricFamily(
            "stigman_scrape_success",
            "1 if the last scrape of the STIG Manager API succeeded")
        duration = GaugeMetricFamily(
            "stigman_scrape_duration_seconds",
            "Time spent scraping the STIG Manager API")
        started = time.time()
        try:
            rows = self._fetch()
        except Exception:  # noqa: BLE001
            log.exception("scrape failed")
            up.add_metric([], 0.0)
            duration.add_metric([], time.time() - started)
            yield up
            yield duration
            return

        labels = ["collection_id", "collection_name"]
        g_assess = GaugeMetricFamily(
            "stigman_collection_assessments",
            "Required rule assessments in the collection", labels=labels)
        g_assessed = GaugeMetricFamily(
            "stigman_collection_assessed",
            "Assessments that have a result", labels=labels)
        g_assets = GaugeMetricFamily(
            "stigman_collection_assets",
            "Assets in the collection", labels=labels)
        g_results = GaugeMetricFamily(
            "stigman_collection_results",
            "Assessed reviews by result (pass/fail/notapplicable/other)",
            labels=labels + ["result"])
        g_findings = GaugeMetricFamily(
            "stigman_collection_findings",
            "Open findings by severity (high=CAT I, medium=CAT II, low=CAT III)",
            labels=labels + ["severity"])
        g_assess_sev = GaugeMetricFamily(
            "stigman_collection_assessments_by_severity",
            "Required assessments by severity", labels=labels + ["severity"])
        g_assessed_sev = GaugeMetricFamily(
            "stigman_collection_assessed_by_severity",
            "Completed assessments by severity", labels=labels + ["severity"])
        g_statuses = GaugeMetricFamily(
            "stigman_collection_statuses",
            "Assessed reviews by workflow status "
            "(saved/submitted/accepted/rejected)",
            labels=labels + ["status"])
        g_cora = GaugeMetricFamily(
            "stigman_collection_cora_percent",
            "CORA-style weighted risk score (0-100), lab interpretation",
            labels=labels)
        g_touch = GaugeMetricFamily(
            "stigman_collection_last_touch_timestamp_seconds",
            "Unix time of the most recent review activity", labels=labels)

        for row in rows:
            lv = [str(row.get("collectionId", "")), row.get("name", "")]
            m = row.get("metrics", {})
            g_assess.add_metric(lv, m.get("assessments", 0))
            g_assessed.add_metric(lv, m.get("assessed", 0))
            g_assets.add_metric(lv, row.get("assets", 0))
            for result, value in (m.get("results") or {}).items():
                g_results.add_metric(lv + [result], value)
            for status, value in (m.get("statuses") or {}).items():
                g_statuses.add_metric(lv + [status], value)
            findings = m.get("findings") or {}
            assess_sev = m.get("assessmentsBySeverity") or {}
            assessed_sev = m.get("assessedBySeverity") or {}
            for sev in ("high", "medium", "low"):
                g_findings.add_metric(lv + [sev], findings.get(sev, 0))
                g_assess_sev.add_metric(lv + [sev], assess_sev.get(sev, 0))
                g_assessed_sev.add_metric(lv + [sev], assessed_sev.get(sev, 0))
            g_cora.add_metric(lv, cora_percent(findings, assess_sev, assessed_sev))
            touch = m.get("maxTouchTs")
            if touch:
                g_touch.add_metric(lv, _to_epoch(touch))

        up.add_metric([], 1.0)
        duration.add_metric([], time.time() - started)
        for metric in (g_assess, g_assessed, g_assets, g_results, g_findings,
                       g_assess_sev, g_assessed_sev, g_statuses, g_cora,
                       g_touch, up, duration):
            yield metric

    def _fetch(self):
        token = self.tokens.get()
        resp = requests.get(
            f"{API_URL}/collections/meta/metrics/summary/collection",
            headers={"Authorization": f"Bearer {token}"},
            timeout=TIMEOUT, verify=VERIFY)
        resp.raise_for_status()
        return resp.json()


def _to_epoch(ts):
    from datetime import datetime, timezone
    return datetime.fromisoformat(ts.replace("Z", "+00:00")) \
        .astimezone(timezone.utc).timestamp()


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    missing = [n for n, v in (("STIGMAN_API_URL", API_URL),
                              ("KEYCLOAK_TOKEN_URL", TOKEN_URL),
                              ("OIDC_CLIENT_SECRET", CLIENT_SECRET)) if not v]
    if missing:
        log.error("missing required environment variables: %s",
                  ", ".join(missing))
        sys.exit(2)
    REGISTRY.register(StigmanCollector())
    start_http_server(PORT)
    log.info("stigman exporter listening on :%d (api=%s)", PORT, API_URL)
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
