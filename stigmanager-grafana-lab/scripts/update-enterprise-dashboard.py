#!/usr/bin/env python3
"""Regenerate grafana/dashboards/stig-posture-enterprise.json.

The enterprise dashboard is generated (not hand-edited) so the panel set stays
consistent and every panel references the provisioned datasource UID
`stigmanager-infinity` directly — no ${DS_*} placeholders.

Enterprise math rules implemented here:
  * counts are summed across collections FIRST, percentages computed AFTER
    (via Infinity `summarizeExpression`), never averaged from per-collection
    percentages;
  * enterprise CORA aggregates each severity's raw counts before applying the
    CORA formula;
  * all divisions are guarded against zero denominators with ternaries.

Usage:  python3 scripts/update-enterprise-dashboard.py
Grafana's file provisioner picks the change up within 30 seconds.
"""
import json
import pathlib

DS = {"type": "yesoreyeram-infinity-datasource", "uid": "stigmanager-infinity"}
API = "http://stigman:54000/api"
META_URL = f"{API}/collections/meta/metrics/summary/collection"
# NOTE: verified against the live 1.6.13 /api/op/definition. The multi-
# collection endpoint is /collections/meta/metrics/summary/collection (the
# /collections/metrics/... form does not exist in this version).
FILTER = "collectionId IN (${collections:singlequote})"

GREEN, BLUE, RED, ORANGE, YELLOW, DARKRED = (
    "green", "blue", "red", "orange", "#EAB839", "dark-red")

# STIG Manager native palette (client/src/css/stigman.css)
CAT1_COLOR = "#eba794"        # --color-severity-high   hsl(13,68%,75%)
CAT2_COLOR = "#ffd68f"        # --color-severity-medium hsl(38,100%,78%)
CAT3_COLOR = "#cdd2ea"        # --color-severity-low    hsl(230,41%,86%)
SAVED_COLOR = "#cdd2ea"       # periwinkle (assessed/saved in the native UI)
SUBMITTED_COLOR = "#c3deab"   # --metrics-status-chart-submitted-light
ACCEPTED_COLOR = "#81dfaa"    # --metrics-status-chart-accepted-light
REJECTED_COLOR = "#eba995"    # --metrics-status-chart-rejected-light
UNASSESSED_COLOR = "#ededed"  # --metrics-status-chart-unassessed-light

COVERAGE_THRESHOLDS = {"mode": "absolute", "steps": [
    {"color": "red", "value": None},
    {"color": "orange", "value": 70},
    {"color": "green", "value": 90}]}
CORA_THRESHOLDS = {"mode": "absolute", "steps": [
    {"color": "green", "value": None},   # 0%  -> Low / no identified risk
    {"color": YELLOW, "value": 0.001},   # >0% -> Moderate
    {"color": "orange", "value": 10},    # >=10% -> High
    {"color": "red", "value": 20}]}      # >=20% -> Very High
ALERT_THRESHOLDS = {"mode": "absolute", "steps": [
    {"color": "green", "value": None},
    {"color": "red", "value": 1}]}
NEUTRAL_THRESHOLDS = {"mode": "absolute", "steps": [{"color": "text", "value": None}]}

CORA_DESC = (
    "CORA-style weighted risk score computed from aggregated raw counts: "
    "p_sev = (open findings + unassessed) / assessments per severity, "
    "CORA = (p1*10 + p2*4 + p3*1) / 15. Zero-assessment severities contribute 0. "
    "Bands: 0% Low, >0% Moderate, >=10% High, >=20% Very High. "
    "Lab interpretation — verify against current DISA CORA guidance before "
    "treating as authoritative.")


def col(selector, text, ctype="number"):
    return {"selector": selector, "text": text, "type": ctype}


SEVERITY_COLS = [
    col("metrics.findings.high", "high"),
    col("metrics.findings.medium", "medium"),
    col("metrics.findings.low", "low"),
    col("metrics.assessmentsBySeverity.high", "assessmentsHigh"),
    col("metrics.assessmentsBySeverity.medium", "assessmentsMedium"),
    col("metrics.assessmentsBySeverity.low", "assessmentsLow"),
    col("metrics.assessedBySeverity.high", "assessedHigh"),
    col("metrics.assessedBySeverity.medium", "assessedMedium"),
    col("metrics.assessedBySeverity.low", "assessedLow"),
]

CORA_ROW_EXPR = (
    "((assessmentsHigh > 0 ? (high + assessmentsHigh - assessedHigh) / assessmentsHigh : 0) * 10"
    " + (assessmentsMedium > 0 ? (medium + assessmentsMedium - assessedMedium) / assessmentsMedium : 0) * 4"
    " + (assessmentsLow > 0 ? (low + assessmentsLow - assessedLow) / assessmentsLow : 0) * 1) / 15 * 100")

CORA_SUM_EXPR = (
    "((sum(assessmentsHigh) > 0 ? (sum(high) + sum(assessmentsHigh) - sum(assessedHigh)) / sum(assessmentsHigh) : 0) * 10"
    " + (sum(assessmentsMedium) > 0 ? (sum(medium) + sum(assessmentsMedium) - sum(assessedMedium)) / sum(assessmentsMedium) : 0) * 4"
    " + (sum(assessmentsLow) > 0 ? (sum(low) + sum(assessmentsLow) - sum(assessedLow)) / sum(assessmentsLow) : 0) * 1) / 15 * 100")


def query(refid, url, columns, computed=None, filter_expr=FILTER,
          summarize=None, alias=None):
    q = {
        "refId": refid,
        "datasource": DS,
        "queryType": "infinity",
        "type": "json",
        "source": "url",
        "format": "table",
        "parser": "backend",
        "url": url,
        "url_options": {"method": "GET", "data": ""},
        "root_selector": "",
        "columns": columns,
    }
    if computed:
        q["computed_columns"] = computed
    if filter_expr:
        q["filterExpression"] = filter_expr
    if summarize:
        q["summarizeExpression"] = summarize
        if alias:
            q["summarizeAlias"] = alias
    return q


def color_override(name, color):
    return {"matcher": {"id": "byName", "options": name},
            "properties": [{"id": "color",
                            "value": {"mode": "fixed", "fixedColor": color}}]}


def stat(grid, title, q, unit=None, thresholds=None, decimals=0, desc=""):
    return {
        "type": "stat", "title": title, "description": desc, "gridPos": grid,
        "datasource": DS, "targets": [q],
        "options": {"reduceOptions": {"values": False, "calcs": ["lastNotNull"]},
                    "colorMode": "value", "graphMode": "none",
                    "justifyMode": "auto", "orientation": "auto",
                    "textMode": "auto", "wideLayout": True},
        "fieldConfig": {"defaults": {
            "unit": unit or "none", "decimals": decimals,
            "thresholds": thresholds or NEUTRAL_THRESHOLDS,
            "color": {"mode": "thresholds"}}, "overrides": []},
    }


def tile_stat(grid, title, q, field_colors, desc="", calcs="lastNotNull",
              transformations=None):
    """Native-UI style colored boxes: one stat panel, one colored tile per
    field (like STIG Manager's Findings / status boxes)."""
    return {
        "type": "stat", "title": title, "description": desc, "gridPos": grid,
        "datasource": DS, "targets": [q],
        **({"transformations": transformations} if transformations else {}),
        "options": {"reduceOptions": {"values": False, "calcs": [calcs]},
                    "colorMode": "background", "graphMode": "none",
                    "justifyMode": "auto", "orientation": "auto",
                    "textMode": "value_and_name", "wideLayout": True},
        "fieldConfig": {"defaults": {"unit": "none", "decimals": 0,
                                     "thresholds": NEUTRAL_THRESHOLDS,
                                     "color": {"mode": "thresholds"}},
                        "overrides": [color_override(n, c)
                                      for n, c in field_colors]},
    }


SEVERITY_TILE_COLORS = [("CAT 1", CAT1_COLOR), ("CAT 2", CAT2_COLOR),
                        ("CAT 3", CAT3_COLOR)]
STATUS_TILE_COLORS = [("Unassessed", UNASSESSED_COLOR),
                      ("Saved", SAVED_COLOR),
                      ("Submitted", SUBMITTED_COLOR),
                      ("Accepted", ACCEPTED_COLOR),
                      ("Rejected", REJECTED_COLOR)]


def donut(grid, title, url, desc="", calcs="sum"):
    """Posture donut. calcs='sum' aggregates across collection rows."""
    q = query("A", url, [
        col("metrics.results.pass", "Compliant"),
        col("metrics.results.notapplicable", "Not Applicable"),
        col("metrics.results.fail", "Open Findings"),
        col("metrics.assessments", "assessments"),
        col("metrics.assessed", "assessed"),
        col("collectionId", "collectionId", "string"),
    ], computed=[{"selector": "assessments - assessed",
                  "text": "Not Assessed", "type": "number"}],
        filter_expr=FILTER if "meta" in url else None)
    return {
        "type": "piechart", "title": title, "description": desc,
        "gridPos": grid, "datasource": DS, "targets": [q],
        "transformations": [{"id": "filterFieldsByName", "options": {
            "include": {"names": ["Compliant", "Not Applicable",
                                  "Open Findings", "Not Assessed"]}}}],
        "options": {
            "pieType": "donut",
            "reduceOptions": {"values": False, "calcs": [calcs]},
            "legend": {"displayMode": "table", "placement": "right",
                       "showLegend": True, "values": ["value", "percent"]},
            "displayLabels": ["percent"],
            "tooltip": {"mode": "single", "sort": "none"}},
        "fieldConfig": {"defaults": {"unit": "none",
                                     "color": {"mode": "palette-classic"}},
                        "overrides": [
                            color_override("Compliant", GREEN),
                            color_override("Not Applicable", BLUE),
                            color_override("Open Findings", RED),
                            color_override("Not Assessed", ORANGE)]},
    }


def gauge(grid, title, q, thresholds, desc=""):
    return {
        "type": "gauge", "title": title, "description": desc, "gridPos": grid,
        "datasource": DS, "targets": [q],
        "options": {"reduceOptions": {"values": False, "calcs": ["lastNotNull"]},
                    "showThresholdLabels": False, "showThresholdMarkers": True,
                    "minVizHeight": 75, "minVizWidth": 75, "sizing": "auto"},
        "fieldConfig": {"defaults": {
            "unit": "percent", "min": 0, "max": 100, "decimals": 1,
            "thresholds": thresholds, "color": {"mode": "thresholds"}},
            "overrides": []},
    }


ABOUT_MD = """## Enterprise STIG Compliance Posture — Management Review

This dashboard aggregates **all STIG Manager collections** selected in the
*Collections* dropdown (default: **All** — currently Linux Production and
Windows Production). Data is pulled **live** from the STIG Manager API by a
read-only reporting service account (`nexus-reporter`); nothing here is
entered by hand.

**How to read it**
- **Compliant** = checks that passed &nbsp;·&nbsp; **Open Findings** = checks that failed &nbsp;·&nbsp; **Not Applicable** = N/A to the asset &nbsp;·&nbsp; **Not Assessed** = not yet reviewed.
- **Coverage** = assessed / required assessments. Target: **90%+** (green). Below 70% is red — posture numbers are not trustworthy until coverage is high.
- **CAT I / II / III** = DISA severity categories (CAT I = highest risk, mission-critical weaknesses). Any CAT I finding shows red.
- **CORA risk score** = weighted risk (CAT I x10, CAT II x4, CAT III x1) including unassessed checks. Bands: 0% Low · >0% Moderate · >=10% High · >=20% Very High. *Lab interpretation of the CORA method — see README.*
- All enterprise percentages are computed from **summed raw counts** across collections, never from averaged percentages.

Drill into a single collection with the **STIG Posture — Per Collection** dashboard.
"""


def build():
    panels = []

    panels.append({
        "type": "text", "title": "", "gridPos": {"h": 9, "w": 24, "x": 0, "y": 0},
        "transparent": False,
        "options": {"mode": "markdown", "code": {"language": "plaintext",
                    "showLineNumbers": False, "showMiniMap": False},
                    "content": ABOUT_MD},
        "fieldConfig": {"defaults": {}, "overrides": []},
    })

    # ---- stat row -------------------------------------------------------
    stats = [
        ("Collections", query("A", META_URL,
                              [col("collectionId", "collectionId", "string")],
                              summarize="count(collectionId)", alias="Collections"),
         "none", NEUTRAL_THRESHOLDS,
         "Number of collections selected and visible to the reporting account."),
        ("Assessed reviews", query("A", META_URL,
                                   [col("collectionId", "collectionId", "string"),
                                    col("metrics.assessed", "assessed")],
                                   summarize="sum(assessed)", alias="Assessed"),
         "none", NEUTRAL_THRESHOLDS, "Total reviews with an assessment result."),
        ("Unassessed reviews", query("A", META_URL,
                                     [col("collectionId", "collectionId", "string"),
                                      col("metrics.assessments", "assessments"),
                                      col("metrics.assessed", "assessed")],
                                     summarize="sum(assessments) - sum(assessed)",
                                     alias="Unassessed"),
         "none", NEUTRAL_THRESHOLDS,
         "sum(assessments) - sum(assessed) across selected collections."),
        ("Open findings", query("A", META_URL,
                                [col("collectionId", "collectionId", "string"),
                                 col("metrics.results.fail", "fail")],
                                summarize="sum(fail)", alias="Open findings"),
         "none", ALERT_THRESHOLDS, "Total reviews with result = fail."),
        ("CAT I findings", query("A", META_URL,
                                 [col("collectionId", "collectionId", "string"),
                                  col("metrics.findings.high", "high")],
                                 summarize="sum(high)", alias="CAT I"),
         "none", ALERT_THRESHOLDS,
         "Open findings of severity high. Red when one or more exist."),
        ("Overall coverage", query("A", META_URL,
                                   [col("collectionId", "collectionId", "string"),
                                    col("metrics.assessments", "assessments"),
                                    col("metrics.assessed", "assessed")],
                                   summarize="sum(assessments) > 0 ? sum(assessed) / sum(assessments) * 100 : 0",
                                   alias="Coverage"),
         "percent", COVERAGE_THRESHOLDS,
         "Enterprise coverage = sum(assessed) / sum(assessments). Counts are "
         "summed first; percentages are never averaged."),
    ]
    for i, (title, q, unit, thr, desc) in enumerate(stats):
        panels.append(stat({"h": 4, "w": 4, "x": 4 * i, "y": 0},
                           title, q, unit=unit, thresholds=thr,
                           decimals=1 if unit == "percent" else 0, desc=desc))

    # ---- posture donut / stacked findings / CORA ------------------------
    panels.append(donut({"h": 9, "w": 8, "x": 0, "y": 4},
                        "Enterprise security posture", META_URL,
                        desc="sum(pass) / sum(notapplicable) / sum(fail) / "
                             "(sum(assessments) - sum(assessed)) across the "
                             "selected collections."))

    findings_q = query("A", META_URL, [
        col("collectionId", "collectionId", "string"),
        col("name", "Collection", "string"),
        col("metrics.findings.high", "CAT I"),
        col("metrics.findings.medium", "CAT II"),
        col("metrics.findings.low", "CAT III")])
    panels.append({
        "type": "barchart", "title": "Open findings by collection (stacked by severity)",
        "gridPos": {"h": 9, "w": 10, "x": 8, "y": 4}, "datasource": DS,
        "targets": [findings_q],
        "transformations": [{"id": "organize", "options": {
            "excludeByName": {"collectionId": True},
            "indexByName": {}, "renameByName": {}}}],
        "options": {"orientation": "auto", "stacking": "normal",
                    "xTickLabelRotation": 0, "xTickLabelSpacing": 0,
                    "showValue": "auto", "groupWidth": 0.7, "barWidth": 0.85,
                    "fullHighlight": False,
                    "legend": {"displayMode": "list", "placement": "bottom",
                               "showLegend": True},
                    "tooltip": {"mode": "single", "sort": "none"}},
        "fieldConfig": {"defaults": {"unit": "none",
                                     "color": {"mode": "palette-classic"},
                                     "custom": {"axisCenteredZero": False,
                                                "axisPlacement": "auto",
                                                "fillOpacity": 55,
                                                "lineWidth": 2}},
                        "overrides": [color_override("CAT I", CAT1_COLOR),
                                      color_override("CAT II", CAT2_COLOR),
                                      color_override("CAT III", CAT3_COLOR)]},
    })

    # ---- review workflow status (native STIG Manager colors) -----------
    status_q = query("A", META_URL, [
        col("collectionId", "collectionId", "string"),
        col("metrics.assessments", "assessments"),
        col("metrics.assessed", "assessed"),
        col("metrics.statuses.saved", "Saved"),
        col("metrics.statuses.submitted", "Submitted"),
        col("metrics.statuses.accepted", "Accepted"),
        col("metrics.statuses.rejected", "Rejected")],
        computed=[{"selector": "assessments - assessed",
                   "text": "Unassessed", "type": "number"}])
    panels.append(tile_stat(
        {"h": 5, "w": 24, "x": 0, "y": 28},
        "Review workflow status (selected collections)", status_q,
        STATUS_TILE_COLORS, calcs="sum",
        desc="Where reviews sit in the workflow, summed across the "
             "selection. Colors match the STIG Manager UI. Workflow status "
             "is intentionally separate from the security-posture donut.",
        transformations=[{"id": "filterFieldsByName", "options": {
            "include": {"names": ["Unassessed", "Saved", "Submitted",
                                  "Accepted", "Rejected"]}}}]))

    panels.append(gauge(
        {"h": 9, "w": 6, "x": 18, "y": 4}, "Enterprise CORA risk score",
        query("A", META_URL,
              [col("collectionId", "collectionId", "string")] + SEVERITY_COLS,
              summarize=CORA_SUM_EXPR,
              alias="Enterprise CORA"),
        CORA_THRESHOLDS,
        desc="Aggregated-first enterprise score: raw severity counts are summed "
             "across selected collections, then the CORA formula is applied. " + CORA_DESC))

    # ---- posture & risk table ------------------------------------------
    table_q = query("A", META_URL, [
        col("collectionId", "collectionId", "string"),
        col("name", "Collection", "string"),
        col("assets", "Assets"),
        col("metrics.assessments", "assessments"),
        col("metrics.assessed", "assessed"),
        col("metrics.results.fail", "Open findings"),
    ] + SEVERITY_COLS,
        computed=[
            {"selector": "assessments > 0 ? assessed / assessments * 100 : 0",
             "text": "Coverage %", "type": "number"},
            {"selector": CORA_ROW_EXPR, "text": "CORA %", "type": "number"}])
    panels.append({
        "type": "table", "title": "Collection posture and risk",
        "description": "Per-collection posture. Coverage and CORA are computed "
                       "from each collection's own raw counts. " + CORA_DESC,
        "gridPos": {"h": 8, "w": 24, "x": 0, "y": 13}, "datasource": DS,
        "targets": [table_q],
        "transformations": [{"id": "organize", "options": {
            "excludeByName": {"assessmentsHigh": True, "assessmentsMedium": True,
                              "assessmentsLow": True, "assessedHigh": True,
                              "assessedMedium": True, "assessedLow": True},
            "indexByName": {"collectionId": 0, "Collection": 1, "Assets": 2,
                            "assessments": 3, "assessed": 4, "Coverage %": 5,
                            "Open findings": 6, "high": 7, "medium": 8,
                            "low": 9, "CORA %": 10},
            "renameByName": {"collectionId": "ID",
                             "assessments": "Assessments",
                             "assessed": "Assessed",
                             "high": "CAT I", "medium": "CAT II",
                             "low": "CAT III"}}}],
        "options": {"cellHeight": "sm", "showHeader": True,
                    "footer": {"show": False, "reducer": ["sum"],
                               "countRows": False, "fields": ""},
                    "sortBy": [{"displayName": "CORA %", "desc": True}]},
        "fieldConfig": {"defaults": {"custom": {"align": "auto",
                                                "cellOptions": {"type": "auto"},
                                                "inspect": False},
                        "thresholds": NEUTRAL_THRESHOLDS},
                        "overrides": [
            {"matcher": {"id": "byName", "options": "Coverage %"},
             "properties": [
                 {"id": "unit", "value": "percent"},
                 {"id": "decimals", "value": 1},
                 {"id": "thresholds", "value": COVERAGE_THRESHOLDS},
                 {"id": "custom.cellOptions",
                  "value": {"type": "color-background", "mode": "gradient"}}]},
            {"matcher": {"id": "byName", "options": "CORA %"},
             "properties": [
                 {"id": "unit", "value": "percent"},
                 {"id": "decimals", "value": 1},
                 {"id": "thresholds", "value": CORA_THRESHOLDS},
                 {"id": "custom.cellOptions",
                  "value": {"type": "color-background", "mode": "gradient"}}]},
            {"matcher": {"id": "byName", "options": "CAT I"},
             "properties": [{"id": "thresholds", "value": ALERT_THRESHOLDS},
                            {"id": "custom.cellOptions",
                             "value": {"type": "color-text"}}]}]},
    })

    # ---- repeated per-collection donuts --------------------------------
    repeated = donut({"h": 7, "w": 6, "x": 0, "y": 21},
                     "${collections:text} — posture",
                     f"{API}/collections/${{collections}}/metrics/summary/collection",
                     desc="Repeats per selected collection.",
                     calcs="lastNotNull")
    repeated["repeat"] = "collections"
    repeated["repeatDirection"] = "h"
    repeated["maxPerRow"] = 4
    panels.append(repeated)

    # make room for the About text panel at the top
    for p in panels[1:]:
        p["gridPos"]["y"] += 9

    for pid, p in enumerate(panels, start=1):
        p["id"] = pid

    return {
        "uid": "stig-posture-enterprise",
        "title": "STIG Posture — Enterprise Overview",
        "description": "Enterprise STIG posture across collections, fed by the "
                       "STIG Manager API via the Infinity datasource "
                       "(service account: nexus-reporter, read-only).",
        "tags": ["stig", "posture", "enterprise"],
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
            "name": "collections",
            "label": "Collections",
            "type": "query",
            "datasource": DS,
            "refresh": 1,
            "multi": True,
            "includeAll": True,
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
            "current": {"selected": True, "text": ["All"], "value": ["$__all"]},
            "options": []}]},
        "annotations": {"list": []},
        "links": [],
        "panels": panels,
    }


if __name__ == "__main__":
    out = pathlib.Path(__file__).resolve().parent.parent / \
        "grafana" / "dashboards" / "stig-posture-enterprise.json"
    out.write_text(json.dumps(build(), indent=2) + "\n")
    print(f"wrote {out}")
