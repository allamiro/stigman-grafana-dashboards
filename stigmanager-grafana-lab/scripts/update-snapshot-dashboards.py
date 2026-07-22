#!/usr/bin/env python3
"""Generate the history SNAPSHOT dashboards in grafana/dashboards-snapshots/
(provisioned into the "STIG Posture (History Snapshots)" folder).

Same look as the Infinity dashboards — pie charts, bar charts, stat tiles,
gauges — but backed by Prometheus history and built from INSTANT queries.
An instant query evaluates at the END of the dashboard's time range, so:

  * leave the range at "now"      -> latest recorded posture (review mode)
  * set the range to end at date X -> the whole dashboard shows posture as
                                      of date X. No trend-watching needed.

Dashboards:
  * stig-posture-snapshot-overview   — all collections (managers)
  * stig-posture-snapshot-collection — one collection, with dropdown

Math matches the live dashboards; the per-collection CORA score comes from
the exporter's stigman_collection_cora_percent (identical formula).

Usage:  python3 scripts/update-snapshot-dashboards.py
"""
import json
import pathlib

DS = {"type": "prometheus", "uid": "stigmanager-prometheus"}
OUT = (pathlib.Path(__file__).resolve().parent.parent
       / "grafana" / "dashboards-snapshots")

GREEN, BLUE, RED, ORANGE, YELLOW, DARKRED = (
    "green", "blue", "red", "orange", "#EAB839", "dark-red")
# STIG Manager native palette (client stigman.css)
CAT1_COLOR, CAT2_COLOR, CAT3_COLOR = "#eba794", "#ffd68f", "#cdd2ea"
SAVED_COLOR, SUBMITTED_COLOR = "#cdd2ea", "#c3deab"
ACCEPTED_COLOR, REJECTED_COLOR, UNASSESSED_COLOR = "#81dfaa", "#eba995", "#ededed"

COVERAGE_THRESHOLDS = {"mode": "absolute", "steps": [
    {"color": "red", "value": None}, {"color": "orange", "value": 70},
    {"color": "green", "value": 90}]}
CORA_THRESHOLDS = {"mode": "absolute", "steps": [
    {"color": "green", "value": None}, {"color": YELLOW, "value": 0.001},
    {"color": "orange", "value": 10}, {"color": "red", "value": 20}]}
ALERT_THRESHOLDS = {"mode": "absolute", "steps": [
    {"color": "green", "value": None}, {"color": "red", "value": 1}]}
NEUTRAL_THRESHOLDS = {"mode": "absolute",
                      "steps": [{"color": "text", "value": None}]}

RISK_MAPPINGS = [
    {"type": "range", "options": {"from": 20, "to": 1e9,
     "result": {"text": "VERY HIGH RISK", "color": "red", "index": 0}}},
    {"type": "range", "options": {"from": 10, "to": 20,
     "result": {"text": "HIGH RISK", "color": "orange", "index": 1}}},
    {"type": "range", "options": {"from": 0.000001, "to": 10,
     "result": {"text": "MODERATE RISK", "color": YELLOW, "index": 2}}},
    {"type": "range", "options": {"from": -1, "to": 0.000001,
     "result": {"text": "LOW RISK", "color": "green", "index": 3}}},
]

CORA_ENT = None  # built below


def sev_sum(metric, sev, coll=None):
    m = {"severity": f'"{sev}"'}
    if coll:
        m["collection_name"] = f'~"{coll}"'
    sel = ",".join(f"{k}={v}" for k, v in m.items())
    return f"sum({metric}{{{sel}}})"


def cora_expr(coll=None):
    parts = []
    for sev, w in (("high", 10), ("medium", 4), ("low", 1)):
        a = sev_sum("stigman_collection_assessments_by_severity", sev, coll)
        d = sev_sum("stigman_collection_assessed_by_severity", sev, coll)
        f = sev_sum("stigman_collection_findings", sev, coll)
        parts.append(f"(({f} + {a} - {d}) / clamp_min({a}, 1)) * {w}")
    return "(" + " + ".join(parts) + ") / 15 * 100"


def csel(coll):
    return f'{{collection_name=~"{coll}"}}' if coll else ""


def rsel(result, coll=None):
    m = [f'result="{result}"']
    if coll:
        m.append(f'collection_name=~"{coll}"')
    return "{" + ",".join(m) + "}"


def target(expr, legend, refid, fmt="time_series"):
    return {"refId": refid, "datasource": DS, "expr": expr,
            "editorMode": "code", "legendFormat": legend,
            "range": False, "instant": True, "format": fmt}


def name_override(name, color):
    return {"matcher": {"id": "byName", "options": name},
            "properties": [{"id": "color",
                            "value": {"mode": "fixed", "fixedColor": color}}]}


def stat(grid, title, expr, unit, thresholds, decimals=1, desc="",
         color_mode="value", mappings=None):
    p = {
        "type": "stat", "title": title, "description": desc, "gridPos": grid,
        "datasource": DS, "targets": [target(expr, "", "A")],
        "options": {"reduceOptions": {"values": False,
                                      "calcs": ["lastNotNull"]},
                    "colorMode": color_mode, "graphMode": "none",
                    "justifyMode": "auto", "orientation": "auto",
                    "textMode": "auto", "wideLayout": True},
        "fieldConfig": {"defaults": {"unit": unit, "decimals": decimals,
                                     "thresholds": thresholds,
                                     "color": {"mode": "thresholds"}},
                        "overrides": []},
    }
    if mappings is not None:
        p["fieldConfig"]["defaults"]["mappings"] = mappings
    return p


def gauge(grid, title, expr, thresholds, desc=""):
    return {
        "type": "gauge", "title": title, "description": desc, "gridPos": grid,
        "datasource": DS, "targets": [target(expr, "", "A")],
        "options": {"reduceOptions": {"values": False,
                                      "calcs": ["lastNotNull"]},
                    "showThresholdLabels": False,
                    "showThresholdMarkers": True,
                    "minVizHeight": 75, "minVizWidth": 75, "sizing": "auto"},
        "fieldConfig": {"defaults": {"unit": "percent", "min": 0, "max": 100,
                                     "decimals": 1, "thresholds": thresholds,
                                     "color": {"mode": "thresholds"}},
                        "overrides": []},
    }


def donut(grid, title, coll=None, desc=""):
    return {
        "type": "piechart", "title": title, "description": desc,
        "gridPos": grid, "datasource": DS,
        "targets": [
            target(f'sum(stigman_collection_results{rsel("pass", coll)})',
                   "Compliant", "A"),
            target('sum(stigman_collection_results'
                   f'{rsel("notapplicable", coll)})', "Not Applicable", "B"),
            target(f'sum(stigman_collection_results{rsel("fail", coll)})',
                   "Open Findings", "C"),
            target(f'sum(stigman_collection_assessments{csel(coll)}) - '
                   f'sum(stigman_collection_assessed{csel(coll)})',
                   "Not Assessed", "D"),
        ],
        "options": {"pieType": "donut",
                    "reduceOptions": {"values": False,
                                      "calcs": ["lastNotNull"]},
                    "legend": {"displayMode": "table", "placement": "right",
                               "showLegend": True,
                               "values": ["value", "percent"]},
                    "displayLabels": ["percent"],
                    "tooltip": {"mode": "single", "sort": "none"}},
        "fieldConfig": {"defaults": {"unit": "none",
                                     "color": {"mode": "palette-classic"}},
                        "overrides": [
                            name_override("Compliant", GREEN),
                            name_override("Not Applicable", BLUE),
                            name_override("Open Findings", RED),
                            name_override("Not Assessed", ORANGE)]},
    }


def severity_bars(grid, title, coll=None, desc=""):
    """Colored tiles matching the native STIG Manager severity boxes."""
    return {
        "type": "stat", "title": title, "description": desc,
        "gridPos": grid, "datasource": DS,
        "targets": [
            target(sev_sum("stigman_collection_findings", "high", coll),
                   "CAT 1", "A"),
            target(sev_sum("stigman_collection_findings", "medium", coll),
                   "CAT 2", "B"),
            target(sev_sum("stigman_collection_findings", "low", coll),
                   "CAT 3", "C")],
        "options": {"reduceOptions": {"values": False,
                                      "calcs": ["lastNotNull"]},
                    "colorMode": "background", "graphMode": "none",
                    "justifyMode": "auto", "orientation": "auto",
                    "textMode": "value_and_name", "wideLayout": True},
        "fieldConfig": {"defaults": {"unit": "none", "decimals": 0,
                                     "thresholds": NEUTRAL_THRESHOLDS,
                                     "color": {"mode": "thresholds"}},
                        "overrides": [name_override("CAT 1", CAT1_COLOR),
                                      name_override("CAT 2", CAT2_COLOR),
                                      name_override("CAT 3", CAT3_COLOR)]},
    }


def status_tiles(grid, title, coll=None, desc=""):
    """Workflow-status tiles with native STIG Manager colors."""
    def ssel(status):
        m = [f'status="{status}"']
        if coll:
            m.append(f'collection_name=~"{coll}"')
        return "{" + ",".join(m) + "}"
    return {
        "type": "stat", "title": title, "description": desc,
        "gridPos": grid, "datasource": DS,
        "targets": [
            target(f'sum(stigman_collection_assessments{csel(coll)}) - '
                   f'sum(stigman_collection_assessed{csel(coll)})',
                   "Unassessed", "A"),
            target(f'sum(stigman_collection_statuses{ssel("saved")})',
                   "Saved", "B"),
            target(f'sum(stigman_collection_statuses{ssel("submitted")})',
                   "Submitted", "C"),
            target(f'sum(stigman_collection_statuses{ssel("accepted")})',
                   "Accepted", "D"),
            target(f'sum(stigman_collection_statuses{ssel("rejected")})',
                   "Rejected", "E")],
        "options": {"reduceOptions": {"values": False,
                                      "calcs": ["lastNotNull"]},
                    "colorMode": "background", "graphMode": "none",
                    "justifyMode": "auto", "orientation": "auto",
                    "textMode": "value_and_name", "wideLayout": True},
        "fieldConfig": {"defaults": {"unit": "none", "decimals": 0,
                                     "thresholds": NEUTRAL_THRESHOLDS,
                                     "color": {"mode": "thresholds"}},
                        "overrides": [
                            name_override("Unassessed", UNASSESSED_COLOR),
                            name_override("Saved", SAVED_COLOR),
                            name_override("Submitted", SUBMITTED_COLOR),
                            name_override("Accepted", ACCEPTED_COLOR),
                            name_override("Rejected", REJECTED_COLOR)]},
    }


def base(uid, title, desc, tags, panels, templating=None):
    for pid, p in enumerate(panels, start=1):
        p["id"] = pid
    return {
        "uid": uid, "title": title, "description": desc, "tags": tags,
        "timezone": "browser", "editable": True, "fiscalYearStartMonth": 0,
        "graphTooltip": 0, "liveNow": False, "schemaVersion": 39,
        "version": 1, "refresh": "5m",
        "time": {"from": "now-6h", "to": "now"},
        "templating": {"list": templating or []},
        "annotations": {"list": []},
        "links": [
            {"title": "Live (API): Management Review", "type": "link",
             "icon": "dashboard", "url": "/d/stig-posture-management",
             "targetBlank": False, "asDropdown": False, "includeVars": False,
             "keepTime": False, "tags": []},
            {"title": "Trends over time", "type": "link", "icon": "dashboard",
             "url": "/d/stig-posture-management-trends", "targetBlank": False,
             "asDropdown": False, "includeVars": False, "keepTime": True,
             "tags": []}],
        "panels": panels,
    }


ENT_COVERAGE = ("sum(stigman_collection_assessed) / "
                "clamp_min(sum(stigman_collection_assessments), 1) * 100")
ENT_COMPLIANCE = ('sum(stigman_collection_results{result="pass"}) / '
                  "clamp_min(sum(stigman_collection_assessed), 1) * 100")

# ======================================================================
# Dashboard 1: Review Snapshot — all collections
# ======================================================================
ABOUT = """# Security Compliance — Review Snapshot (from recorded history)
Familiar review visuals (pies, bars, stats) backed by **recorded history** — no graphs to watch.
**How the time picker works here:** every panel shows posture **as of the END of the selected time range**. Leave it at *now* for the current review; set the range to end on a past date (e.g. *yesterday 17:00*) and the whole page shows posture as it was then. History exists from the first exporter run onward.
"""

panels = [{
    "type": "text", "title": "",
    "gridPos": {"h": 4, "w": 24, "x": 0, "y": 0},
    "options": {"mode": "markdown", "code": {"language": "plaintext",
                "showLineNumbers": False, "showMiniMap": False},
                "content": ABOUT},
    "fieldConfig": {"defaults": {}, "overrides": []},
}]

panels.append(stat({"h": 6, "w": 7, "x": 0, "y": 4}, "Bottom line",
                   cora_expr(), "none", CORA_THRESHOLDS,
                   color_mode="background", mappings=RISK_MAPPINGS,
                   desc="CORA-style weighted risk verdict as of the selected "
                        "point in time."))
panels.append(stat({"h": 6, "w": 5, "x": 7, "y": 4},
                   "Critical findings — target 0",
                   sev_sum("stigman_collection_findings", "high"), "none",
                   ALERT_THRESHOLDS, decimals=0, color_mode="background",
                   desc="Open CAT I findings."))
panels.append(stat({"h": 6, "w": 4, "x": 12, "y": 4},
                   "Compliance — target 90%+", ENT_COMPLIANCE, "percent",
                   COVERAGE_THRESHOLDS, desc="sum(pass)/sum(assessed)."))
panels.append(stat({"h": 6, "w": 4, "x": 16, "y": 4},
                   "Coverage — target 90%+", ENT_COVERAGE, "percent",
                   COVERAGE_THRESHOLDS,
                   desc="sum(assessed)/sum(assessments)."))
panels.append(stat({"h": 6, "w": 4, "x": 20, "y": 4}, "Open findings",
                   'sum(stigman_collection_results{result="fail"})', "none",
                   ALERT_THRESHOLDS, decimals=0,
                   desc="All open failed checks."))

panels.append(donut({"h": 9, "w": 8, "x": 0, "y": 10},
                    "Security posture — all collections",
                    desc="Compliant / Not Applicable / Open Findings / "
                         "Not Assessed as of the selected point in time."))

# stacked findings by collection
panels.append({
    "type": "barchart",
    "title": "Open findings by collection (stacked by severity)",
    "description": "As of the selected point in time.",
    "gridPos": {"h": 9, "w": 10, "x": 8, "y": 10}, "datasource": DS,
    "targets": [
        target('sum by (collection_name) '
               '(stigman_collection_findings{severity="high"})',
               "", "A", fmt="table"),
        target('sum by (collection_name) '
               '(stigman_collection_findings{severity="medium"})',
               "", "B", fmt="table"),
        target('sum by (collection_name) '
               '(stigman_collection_findings{severity="low"})',
               "", "C", fmt="table")],
    "transformations": [
        {"id": "joinByField",
         "options": {"byField": "collection_name", "mode": "outer"}},
        {"id": "organize", "options": {
            "excludeByName": {"Time": True, "Time 1": True, "Time 2": True,
                              "Time 3": True},
            "renameByName": {"collection_name": "Collection",
                             "Value #A": "CAT I", "Value #B": "CAT II",
                             "Value #C": "CAT III"},
            "indexByName": {}}}],
    "options": {"orientation": "auto", "stacking": "normal",
                "xTickLabelRotation": 0, "xTickLabelSpacing": 0,
                "showValue": "auto", "groupWidth": 0.7, "barWidth": 0.85,
                "fullHighlight": False,
                "legend": {"displayMode": "list", "placement": "bottom",
                           "showLegend": True},
                "tooltip": {"mode": "single", "sort": "none"}},
    "fieldConfig": {"defaults": {"unit": "none",
                                 "color": {"mode": "palette-classic"},
                                 "custom": {"fillOpacity": 55,
                                            "lineWidth": 2,
                                            "axisCenteredZero": False,
                                            "axisPlacement": "auto"}},
                    "overrides": [name_override("CAT I", CAT1_COLOR),
                                  name_override("CAT II", CAT2_COLOR),
                                  name_override("CAT III", CAT3_COLOR)]},
})

panels.append(gauge({"h": 9, "w": 6, "x": 18, "y": 10},
                    "Enterprise CORA risk score", cora_expr(),
                    CORA_THRESHOLDS,
                    desc="Aggregated-first CORA-style score as of the "
                         "selected point in time."))

# focus table (per-collection, ranked by risk)
panels.append({
    "type": "table", "title": "Where to focus first (highest risk on top)",
    "description": "Per-collection posture as of the selected point in time.",
    "gridPos": {"h": 7, "w": 24, "x": 0, "y": 19}, "datasource": DS,
    "targets": [
        target("stigman_collection_cora_percent", "", "A", fmt="table"),
        target('sum by (collection_name) '
               '(stigman_collection_findings{severity="high"})',
               "", "B", fmt="table"),
        target('sum by (collection_name) '
               '(stigman_collection_results{result="fail"})',
               "", "C", fmt="table"),
        target("sum by (collection_name) (stigman_collection_assessed) / "
               "clamp_min(sum by (collection_name) "
               "(stigman_collection_assessments), 1) * 100",
               "", "D", fmt="table"),
        target("sum by (collection_name) (stigman_collection_assets)",
               "", "E", fmt="table")],
    "transformations": [
        {"id": "joinByField",
         "options": {"byField": "collection_name", "mode": "outer"}},
        {"id": "organize", "options": {
            "excludeByName": {"Time": True, "Time 1": True, "Time 2": True,
                              "Time 3": True, "Time 4": True, "Time 5": True,
                              "collection_id": True, "instance": True,
                              "job": True, "__name__": True},
            "renameByName": {"collection_name": "Environment",
                             "Value #A": "Risk score %",
                             "Value #B": "Critical (CAT I)",
                             "Value #C": "Open findings",
                             "Value #D": "Coverage %",
                             "Value #E": "Assets"},
            "indexByName": {"Environment": 0, "Risk score %": 1,
                            "Critical (CAT I)": 2, "Open findings": 3,
                            "Coverage %": 4, "Assets": 5}}},
        {"id": "sortBy", "options": {
            "fields": {},
            "sort": [{"field": "Risk score %", "desc": True}]}}],
    "options": {"cellHeight": "md", "showHeader": True,
                "footer": {"show": False, "reducer": ["sum"],
                           "countRows": False, "fields": ""}},
    "fieldConfig": {"defaults": {"custom": {"align": "auto",
                                            "cellOptions": {"type": "auto"},
                                            "inspect": False},
                    "thresholds": NEUTRAL_THRESHOLDS},
                    "overrides": [
        {"matcher": {"id": "byName", "options": "Risk score %"},
         "properties": [{"id": "unit", "value": "percent"},
                        {"id": "decimals", "value": 1},
                        {"id": "thresholds", "value": CORA_THRESHOLDS},
                        {"id": "custom.cellOptions",
                         "value": {"type": "color-background",
                                   "mode": "basic"}}]},
        {"matcher": {"id": "byName", "options": "Coverage %"},
         "properties": [{"id": "unit", "value": "percent"},
                        {"id": "decimals", "value": 1},
                        {"id": "thresholds", "value": COVERAGE_THRESHOLDS},
                        {"id": "custom.cellOptions",
                         "value": {"type": "color-background",
                                   "mode": "basic"}}]},
        {"matcher": {"id": "byName", "options": "Critical (CAT I)"},
         "properties": [{"id": "thresholds", "value": ALERT_THRESHOLDS},
                        {"id": "custom.cellOptions",
                         "value": {"type": "color-text"}}]}]},
})

# repeated per-collection donuts
rep_var = {
    "name": "collection", "label": "Collections", "type": "query",
    "datasource": DS, "refresh": 2, "multi": True, "includeAll": True,
    "sort": 1,
    "query": {"qryType": 1, "query":
              "label_values(stigman_collection_assessments, collection_name)",
              "refId": "PrometheusVariableQueryEditor-VariableQuery"},
    "current": {"selected": True, "text": ["All"], "value": ["$__all"]},
    "options": [],
}
panels.append(status_tiles({"h": 4, "w": 24, "x": 0, "y": 26},
                            "Review workflow status (all collections)",
                            desc="Where reviews sit in the workflow. Colors "
                                 "match the STIG Manager UI."))

rep = donut({"h": 7, "w": 6, "x": 0, "y": 30}, "$collection — posture",
            coll="$collection", desc="Repeats per selected collection.")
rep["repeat"] = "collection"
rep["repeatDirection"] = "h"
rep["maxPerRow"] = 4
panels.append(rep)

overview = base(
    "stig-posture-snapshot-overview",
    "STIG Posture — Review Snapshot (All Collections)",
    "Point-in-time review visuals from recorded history. Panels evaluate at "
    "the end of the selected time range.",
    ["stig", "posture", "snapshot"], panels, [rep_var])

# ======================================================================
# Dashboard 2: Review Snapshot — per collection
# ======================================================================
C = "$collection"
cpanels = [
    donut({"h": 10, "w": 9, "x": 0, "y": 0}, "Security posture — $collection",
          coll=C, desc="As of the end of the selected time range."),
    gauge({"h": 5, "w": 5, "x": 9, "y": 0}, "Assessment coverage",
          f"sum(stigman_collection_assessed{csel(C)}) / "
          f"clamp_min(sum(stigman_collection_assessments{csel(C)}), 1) * 100",
          COVERAGE_THRESHOLDS, desc="assessed / assessments."),
    gauge({"h": 5, "w": 5, "x": 14, "y": 0}, "CORA risk score",
          f"stigman_collection_cora_percent{csel(C)}", CORA_THRESHOLDS,
          desc="Per-collection CORA-style score (exporter-computed, same "
               "formula as the live dashboards)."),
    stat({"h": 5, "w": 5, "x": 19, "y": 0}, "Open findings",
         f'sum(stigman_collection_results{rsel("fail", C)})', "none",
         ALERT_THRESHOLDS, decimals=0, desc="Failed checks."),
    stat({"h": 5, "w": 5, "x": 19, "y": 5}, "CAT I findings",
         sev_sum("stigman_collection_findings", "high", C), "none",
         ALERT_THRESHOLDS, decimals=0, color_mode="background",
         desc="Open critical findings. Red when >= 1."),
    severity_bars({"h": 5, "w": 10, "x": 9, "y": 5},
                  "Open findings by severity", coll=C,
                  desc="CAT 1 = high, CAT 2 = medium, CAT 3 = low."),
    status_tiles({"h": 4, "w": 24, "x": 0, "y": 10},
                 "Review workflow status", coll=C,
                 desc="Where this collection's reviews sit in the workflow."),
]
coll_var = {
    "name": "collection", "label": "Collection", "type": "query",
    "datasource": DS, "refresh": 2, "multi": False, "includeAll": False,
    "sort": 1,
    "query": {"qryType": 1, "query":
              "label_values(stigman_collection_assessments, collection_name)",
              "refId": "PrometheusVariableQueryEditor-VariableQuery"},
    "current": {}, "options": [],
}
collection = base(
    "stig-posture-snapshot-collection",
    "STIG Posture — Review Snapshot (Per Collection)",
    "Point-in-time review visuals for one collection, from recorded history.",
    ["stig", "posture", "snapshot", "collection"], cpanels, [coll_var])

OUT.mkdir(exist_ok=True)
for dash, name in ((overview, "stig-posture-snapshot-overview.json"),
                   (collection, "stig-posture-snapshot-collection.json")):
    (OUT / name).write_text(json.dumps(dash, indent=2) + "\n")
    print(f"wrote {OUT / name}")
