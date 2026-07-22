#!/usr/bin/env python3
"""Generate grafana/dashboards/stig-posture-collection.json (per-collection
live dashboard, Infinity datasource).

Reuses the enterprise generator's palette/helpers; severity and workflow
status use the native STIG Manager UI colors.

Usage:  python3 scripts/update-collection-dashboard.py
"""
import importlib.util
import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "ent", HERE / "update-enterprise-dashboard.py")
ent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ent)

DS, col = ent.DS, ent.col
API = "http://stigman:54000/api"
SUMMARY_URL = f"{API}/collections/$collection/metrics/summary/collection"


def q(columns, computed=None):
    return ent.query("A", SUMMARY_URL, columns, computed=computed,
                     filter_expr=None)


panels = []

panels.append({
    "id": 1, "type": "piechart", "title": "Security posture — $collection",
    "description": "Compliant = pass, Not Applicable = notapplicable, "
                   "Open Findings = fail, Not Assessed = assessments - "
                   "assessed. Workflow statuses are shown separately below.",
    "gridPos": {"h": 10, "w": 9, "x": 0, "y": 0}, "datasource": DS,
    "targets": [q([
        col("metrics.results.pass", "Compliant"),
        col("metrics.results.notapplicable", "Not Applicable"),
        col("metrics.results.fail", "Open Findings"),
        col("metrics.assessments", "assessments"),
        col("metrics.assessed", "assessed")],
        computed=[{"selector": "assessments - assessed",
                   "text": "Not Assessed", "type": "number"}])],
    "transformations": [{"id": "filterFieldsByName", "options": {
        "include": {"names": ["Compliant", "Not Applicable",
                              "Open Findings", "Not Assessed"]}}}],
    "options": {"pieType": "donut",
                "reduceOptions": {"values": False, "calcs": ["lastNotNull"]},
                "legend": {"displayMode": "table", "placement": "right",
                           "showLegend": True, "values": ["value", "percent"]},
                "displayLabels": ["percent"],
                "tooltip": {"mode": "single", "sort": "none"}},
    "fieldConfig": {"defaults": {"unit": "none",
                                 "color": {"mode": "palette-classic"}},
                    "overrides": [
                        ent.color_override("Compliant", ent.GREEN),
                        ent.color_override("Not Applicable", ent.BLUE),
                        ent.color_override("Open Findings", ent.RED),
                        ent.color_override("Not Assessed", ent.ORANGE)]},
})

panels.append(ent.gauge(
    {"h": 5, "w": 5, "x": 9, "y": 0}, "Assessment coverage",
    q([col("metrics.assessments", "assessments"),
       col("metrics.assessed", "assessed")],
      computed=[{"selector": "assessments > 0 ? assessed / assessments * 100 : 0",
                 "text": "Coverage", "type": "number"}]),
    ent.COVERAGE_THRESHOLDS,
    desc="Coverage = assessed / assessments (0% when the collection has "
         "no assessments)."))
panels[-1]["transformations"] = [{"id": "filterFieldsByName",
                                  "options": {"include": {"names": ["Coverage"]}}}]

panels.append(ent.gauge(
    {"h": 5, "w": 5, "x": 14, "y": 0}, "CORA risk score (this collection)",
    q(list(ent.SEVERITY_COLS),
      computed=[{"selector": ent.CORA_ROW_EXPR, "text": "CORA",
                 "type": "number"}]),
    ent.CORA_THRESHOLDS, desc=ent.CORA_DESC))
panels[-1]["transformations"] = [{"id": "filterFieldsByName",
                                  "options": {"include": {"names": ["CORA"]}}}]

panels.append(ent.stat(
    {"h": 5, "w": 5, "x": 19, "y": 0}, "Open findings",
    q([col("metrics.results.fail", "Open findings")]),
    thresholds=ent.ALERT_THRESHOLDS,
    desc="Reviews with result = fail."))

panels.append(ent.stat(
    {"h": 5, "w": 5, "x": 19, "y": 5}, "CAT 1 findings",
    q([col("metrics.findings.high", "CAT 1")]),
    thresholds=ent.ALERT_THRESHOLDS,
    desc="Open critical findings. Red when >= 1."))
panels[-1]["options"]["colorMode"] = "background"

panels.append(ent.tile_stat(
    {"h": 5, "w": 10, "x": 9, "y": 5}, "Open findings by severity",
    q([col("metrics.findings.high", "CAT 1"),
       col("metrics.findings.medium", "CAT 2"),
       col("metrics.findings.low", "CAT 3")]),
    ent.SEVERITY_TILE_COLORS,
    desc="CAT 1 = high, CAT 2 = medium, CAT 3 = low. Colors match the "
         "STIG Manager UI."))

panels.append(ent.tile_stat(
    {"h": 4, "w": 24, "x": 0, "y": 10}, "Review workflow status",
    q([col("metrics.assessments", "assessments"),
       col("metrics.assessed", "assessed"),
       col("metrics.statuses.saved", "Saved"),
       col("metrics.statuses.submitted", "Submitted"),
       col("metrics.statuses.accepted", "Accepted"),
       col("metrics.statuses.rejected", "Rejected")],
      computed=[{"selector": "assessments - assessed",
                 "text": "Unassessed", "type": "number"}]),
    ent.STATUS_TILE_COLORS,
    desc="Where this collection's reviews sit in the workflow. Colors match "
         "the STIG Manager UI. Kept separate from the security-posture donut.",
    transformations=[{"id": "filterFieldsByName", "options": {
        "include": {"names": ["Unassessed", "Saved", "Submitted",
                              "Accepted", "Rejected"]}}}]))

for pid, p in enumerate(panels, start=1):
    p["id"] = pid

dashboard = {
    "uid": "stig-posture-collection",
    "title": "STIG Posture — Per Collection",
    "description": "Security posture for a single STIG Manager collection, "
                   "fed by the STIG Manager API via the Infinity datasource "
                   "(service account: nexus-reporter, read-only).",
    "tags": ["stig", "posture", "collection"],
    "timezone": "browser",
    "editable": True,
    "fiscalYearStartMonth": 0,
    "graphTooltip": 0,
    "liveNow": False,
    "schemaVersion": 39,
    "version": 1,
    "refresh": "5m",
    "time": {"from": "now-6h", "to": "now"},
    "templating": {"list": [{
        "name": "collection",
        "label": "Collection",
        "type": "query",
        "datasource": DS,
        "refresh": 1,
        "multi": False,
        "includeAll": False,
        "sort": 1,
        "query": {
            "queryType": "infinity",
            "query": "",
            "infinityQuery": {
                "refId": "variable",
                "queryType": "infinity",
                "type": "json",
                "source": "url",
                "format": "table",
                "parser": "backend",
                "url": f"{API}/collections",
                "url_options": {"method": "GET", "data": ""},
                "root_selector": "",
                "columns": [
                    {"selector": "name", "text": "__text", "type": "string"},
                    {"selector": "collectionId", "text": "__value",
                     "type": "string"}]}},
        "current": {},
        "options": []}]},
    "annotations": {"list": []},
    "links": [],
    "panels": panels,
}

out = HERE.parent / "grafana" / "dashboards" / "stig-posture-collection.json"
out.write_text(json.dumps(dashboard, indent=2) + "\n")
print(f"wrote {out}")
