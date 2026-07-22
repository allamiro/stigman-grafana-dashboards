#!/usr/bin/env python3
"""Generate the cyber-analyst drill-down dashboards in
grafana/dashboards-cyber/ (folder "STIG Posture (Cyber Analysis)").

Built for the analyst workflow, not the manager one: pick a collection and
pivot by STIG benchmark, asset, and label; then rank the actual failing
rules; then deep-dive a single asset. All live data via the read-only
Infinity datasource; severity/status colors match the STIG Manager UI.

Dashboards:
  * stig-cyber-drilldown — collection drill-down (by STIG / asset / label /
    failing rule)
  * stig-cyber-asset     — single-asset deep dive

Usage:  python3 scripts/update-cyber-dashboards.py
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
OUT = HERE.parent / "grafana" / "dashboards-cyber"

SEVERITY_MAPPINGS = [{"type": "value", "options": {
    "high": {"text": "CAT 1", "color": ent.CAT1_COLOR, "index": 0},
    "medium": {"text": "CAT 2", "color": ent.CAT2_COLOR, "index": 1},
    "low": {"text": "CAT 3", "color": ent.CAT3_COLOR, "index": 2}}}]


def q(url, columns, computed=None, filter_expr=None):
    return ent.query("A", url, columns, computed=computed,
                     filter_expr=filter_expr)


def bg_override(name, color):
    return {"matcher": {"id": "byName", "options": name},
            "properties": [
                {"id": "color", "value": {"mode": "fixed",
                                          "fixedColor": color}},
                {"id": "custom.cellOptions",
                 "value": {"type": "color-background", "mode": "basic"}}]}


def pct_override(name, thresholds):
    return {"matcher": {"id": "byName", "options": name},
            "properties": [
                {"id": "unit", "value": "percent"},
                {"id": "decimals", "value": 1},
                {"id": "thresholds", "value": thresholds},
                {"id": "custom.cellOptions",
                 "value": {"type": "color-background", "mode": "basic"}}]}


AGE_COLS = [col("metrics.minTs", "Oldest", "timestamp"),
            col("metrics.maxTs", "Newest", "timestamp"),
            col("metrics.maxTouchTs", "Updated", "timestamp")]

POSTURE_COLS = AGE_COLS + [
    col("metrics.assessments", "assessments"),
    col("metrics.assessed", "assessed"),
    col("metrics.results.fail", "Open findings"),
    col("metrics.statuses.saved", "Saved"),
    col("metrics.statuses.submitted", "Submitted"),
    col("metrics.statuses.accepted", "Accepted"),
    col("metrics.statuses.rejected", "Rejected"),
] + ent.SEVERITY_COLS

POSTURE_COMPUTED = [
    {"selector": "assessments > 0 ? assessed / assessments * 100 : 0",
     "text": "Coverage %", "type": "number"},
    {"selector": ent.CORA_ROW_EXPR, "text": "CORA %", "type": "number"},
]

HIDE_HELPERS = {"assessments": False, "assessed": False,
                "assessmentsHigh": True, "assessmentsMedium": True,
                "assessmentsLow": True, "assessedHigh": True,
                "assessedMedium": True, "assessedLow": True}

AGE_OVERRIDES = [
    {"matcher": {"id": "byName", "options": n},
     "properties": [{"id": "unit", "value": "dateTimeFromNow"}]}
    for n in ("Oldest", "Newest", "Updated")]

POSTURE_OVERRIDES = AGE_OVERRIDES + [
    pct_override("Coverage %", ent.COVERAGE_THRESHOLDS),
    pct_override("CORA %", ent.CORA_THRESHOLDS),
    bg_override("CAT 1", ent.CAT1_COLOR),
    bg_override("CAT 2", ent.CAT2_COLOR),
    bg_override("CAT 3", ent.CAT3_COLOR),
    bg_override("Saved", ent.SAVED_COLOR),
    bg_override("Submitted", ent.SUBMITTED_COLOR),
    bg_override("Accepted", ent.ACCEPTED_COLOR),
    bg_override("Rejected", ent.REJECTED_COLOR),
]


def posture_table(grid, title, url, first_cols, first_renames, desc="",
                  filter_expr=None, sort_field="CORA %"):
    """Standard cyber pivot table: <dimension cols> + posture + statuses."""
    columns = first_cols + POSTURE_COLS
    renames = dict(first_renames)
    renames.update({"high": "CAT 1", "medium": "CAT 2", "low": "CAT 3",
                    "assessments": "Checks", "assessed": "Assessed"})
    # explicit column order: dimensions, volume, coverage, severity,
    # workflow, risk. indexByName keys must be the ORIGINAL (pre-rename)
    # field names or Grafana appends the column at the end.
    ordered = [c["text"] for c in first_cols]
    ordered += ["assessments", "assessed", "Coverage %", "Open findings",
                "high", "medium", "low", "Saved", "Submitted",
                "Accepted", "Rejected", "Oldest", "Newest", "Updated",
                "CORA %"]
    index_by_name = {name: i for i, name in enumerate(dict.fromkeys(ordered))}
    return {
        "type": "table", "title": title, "description": desc,
        "gridPos": grid, "datasource": DS,
        "targets": [q(url, columns, computed=POSTURE_COMPUTED,
                      filter_expr=filter_expr)],
        "transformations": [
            {"id": "organize", "options": {
                "excludeByName": {k: v for k, v in HIDE_HELPERS.items() if v},
                "renameByName": renames, "indexByName": index_by_name}},
            {"id": "sortBy", "options": {
                "fields": {},
                "sort": [{"field": sort_field, "desc": True}]}}],
        "options": {"cellHeight": "sm", "showHeader": True,
                    "footer": {"show": False, "reducer": ["sum"],
                               "countRows": False, "fields": ""}},
        "fieldConfig": {"defaults": {
            "custom": {"align": "auto", "cellOptions": {"type": "auto"},
                       "inspect": False, "filterable": True},
            "thresholds": ent.NEUTRAL_THRESHOLDS},
            "overrides": POSTURE_OVERRIDES},
    }


def findings_table(grid, title, url, desc="", extra_cols=None,
                   sort_field="Failing assets"):
    columns = [col("severity", "Severity", "string"),
               col("ruleId", "Rule", "string"),
               col("title", "Title", "string")] + (extra_cols or [])
    return {
        "type": "table", "title": title, "description": desc,
        "gridPos": grid, "datasource": DS, "targets": [q(url, columns)],
        "transformations": [{"id": "sortBy", "options": {
            "fields": {}, "sort": [{"field": sort_field, "desc": True}]}}],
        "options": {"cellHeight": "sm", "showHeader": True,
                    "footer": {"show": False, "reducer": ["sum"],
                               "countRows": False, "fields": ""}},
        "fieldConfig": {"defaults": {
            "custom": {"align": "auto", "cellOptions": {"type": "auto"},
                       "inspect": False, "filterable": True},
            "thresholds": ent.NEUTRAL_THRESHOLDS},
            "overrides": [
                {"matcher": {"id": "byName", "options": "Severity"},
                 "properties": [
                     {"id": "mappings", "value": SEVERITY_MAPPINGS},
                     {"id": "custom.cellOptions",
                      "value": {"type": "color-background", "mode": "basic"}},
                     {"id": "custom.width", "value": 90}]},
                {"matcher": {"id": "byName", "options": "Rule"},
                 "properties": [{"id": "custom.width", "value": 210}]}]},
    }


def sev_tiles(grid, title, url, filter_expr=None, desc=""):
    return ent.tile_stat(grid, title,
                         q(url, [col("metrics.findings.high", "CAT 1"),
                                 col("metrics.findings.medium", "CAT 2"),
                                 col("metrics.findings.low", "CAT 3"),
                                 col("assetId", "assetId", "string")
                                 if filter_expr else
                                 col("collectionId", "collectionId", "string")],
                           filter_expr=filter_expr),
                         ent.SEVERITY_TILE_COLORS, desc=desc,
                         transformations=[{"id": "filterFieldsByName",
                                           "options": {"include": {"names": [
                                               "CAT 1", "CAT 2", "CAT 3"]}}}])


def status_tiles(grid, title, url, filter_expr=None, desc=""):
    cols = [col("metrics.assessments", "assessments"),
            col("metrics.assessed", "assessed"),
            col("metrics.statuses.saved", "Saved"),
            col("metrics.statuses.submitted", "Submitted"),
            col("metrics.statuses.accepted", "Accepted"),
            col("metrics.statuses.rejected", "Rejected")]
    if filter_expr:
        cols.append(col("assetId", "assetId", "string"))
    return ent.tile_stat(grid, title,
                         q(url, cols,
                           computed=[{"selector": "assessments - assessed",
                                      "text": "Unassessed", "type": "number"}],
                           filter_expr=filter_expr),
                         ent.STATUS_TILE_COLORS, desc=desc,
                         transformations=[{"id": "filterFieldsByName",
                                           "options": {"include": {"names": [
                                               "Unassessed", "Saved",
                                               "Submitted", "Accepted",
                                               "Rejected"]}}}])


def collection_variable():
    return {
        "name": "collection", "label": "Collection", "type": "query",
        "datasource": DS, "refresh": 1, "multi": False, "includeAll": False,
        "sort": 1,
        "query": {"queryType": "infinity", "query": "", "infinityQuery": {
            "refId": "variable", "queryType": "infinity", "type": "json",
            "source": "url", "format": "table", "parser": "backend",
            "url": f"{API}/collections",
            "url_options": {"method": "GET", "data": ""},
            "root_selector": "",
            "columns": [
                {"selector": "name", "text": "__text", "type": "string"},
                {"selector": "collectionId", "text": "__value",
                 "type": "string"}]}},
        "current": {}, "options": [],
    }


def base(uid, title, desc, panels, templating):
    for pid, p in enumerate(panels, start=1):
        p["id"] = pid
    return {
        "uid": uid, "title": title, "description": desc,
        "tags": ["stig", "posture", "cyber"],
        "timezone": "browser", "editable": True, "fiscalYearStartMonth": 0,
        "graphTooltip": 0, "liveNow": False, "schemaVersion": 39,
        "version": 1, "refresh": "5m",
        "time": {"from": "now-6h", "to": "now"},
        "templating": {"list": templating},
        "annotations": {"list": []},
        "links": [
            {"title": "Asset deep dive", "type": "link", "icon": "dashboard",
             "url": "/d/stig-cyber-asset", "targetBlank": False,
             "asDropdown": False, "includeVars": True, "keepTime": False,
             "tags": []},
            {"title": "Collection drilldown", "type": "link",
             "icon": "dashboard", "url": "/d/stig-cyber-drilldown",
             "targetBlank": False, "asDropdown": False, "includeVars": True,
             "keepTime": False, "tags": []}],
        "panels": panels,
    }


# ======================================================================
# Dashboard A: collection drill-down
# ======================================================================
SUMMARY = f"{API}/collections/$collection/metrics/summary"

panels = [
    sev_tiles({"h": 4, "w": 8, "x": 0, "y": 0},
              "Open findings by severity — $collection",
              f"{SUMMARY}/collection",
              desc="Colors match the STIG Manager UI."),
    status_tiles({"h": 4, "w": 10, "x": 8, "y": 0},
                 "Review workflow status — $collection",
                 f"{SUMMARY}/collection",
                 desc="Where reviews sit in the workflow."),
    ent.review_age_tiles({"h": 4, "w": 6, "x": 18, "y": 0},
                         "Review ages", f"{SUMMARY}/collection"),
    posture_table(
        {"h": 8, "w": 24, "x": 0, "y": 4}, "Posture by STIG benchmark",
        f"{SUMMARY}/stig",
        [col("benchmarkId", "STIG", "string"),
         col("revisionStr", "Rev", "string"),
         col("assets", "Assets")],
        {"benchmarkId": "STIG"},
        desc="Each STIG applied in this collection: coverage, open findings "
             "by severity, workflow statuses and per-STIG risk score. Sorted "
             "worst-risk first. Columns are filterable."),
    posture_table(
        {"h": 8, "w": 24, "x": 0, "y": 12}, "Posture by asset",
        f"{SUMMARY}/asset",
        [col("name", "Asset", "string"), col("ip", "IP", "string")],
        {"name": "Asset"},
        desc="Every asset in this collection. Use the Asset deep-dive "
             "dashboard (top-right link) for a single asset."),
    posture_table(
        {"h": 7, "w": 24, "x": 0, "y": 20}, "Posture by label",
        f"{SUMMARY}/label",
        [col("name", "Label", "string"), col("assets", "Assets")],
        {"name": "Label"},
        desc="Posture pivoted by collection label (site, OS, department...). "
             "An empty label row means assets without labels."),
    findings_table(
        {"h": 10, "w": 24, "x": 0, "y": 27},
        "Top failing rules (most widespread first)",
        f"{API}/collections/$collection/findings?aggregator=ruleId&acceptedOnly=false",
        desc="Open findings aggregated by rule across the collection — the "
             "remediation hit list. 'Failing assets' = how many assets fail "
             "this rule.",
        extra_cols=[col("assetCount", "Failing assets")]),
]
drilldown = base(
    "stig-cyber-drilldown", "STIG Posture — Cyber Drilldown",
    "Analyst drill-down for one collection: pivot posture by STIG, asset "
    "and label, then rank failing rules. Live read-only API data.",
    panels, [collection_variable()])

# ======================================================================
# Dashboard B: asset deep dive
# ======================================================================
ASSET_FILTER = "assetId IN ('$asset')"
ASSET_URL = f"{SUMMARY}/asset"

asset_var = {
    "name": "asset", "label": "Asset", "type": "query",
    "datasource": DS, "refresh": 1, "multi": False, "includeAll": False,
    "sort": 1,
    "query": {"queryType": "infinity", "query": "", "infinityQuery": {
        "refId": "variable", "queryType": "infinity", "type": "json",
        "source": "url", "format": "table", "parser": "backend",
        "url": ASSET_URL,
        "url_options": {"method": "GET", "data": ""},
        "root_selector": "",
        "columns": [
            {"selector": "name", "text": "__text", "type": "string"},
            {"selector": "assetId", "text": "__value", "type": "string"}]}},
    "current": {}, "options": [],
}

apanels = [
    {
        "type": "piechart", "title": "Security posture — ${asset:text}",
        "description": "This asset's checks: Compliant / Not Applicable / "
                       "Open Findings / Not Assessed.",
        "gridPos": {"h": 9, "w": 9, "x": 0, "y": 0}, "datasource": DS,
        "targets": [q(ASSET_URL, [
            col("assetId", "assetId", "string"),
            col("metrics.results.pass", "Compliant"),
            col("metrics.results.notapplicable", "Not Applicable"),
            col("metrics.results.fail", "Open Findings"),
            col("metrics.assessments", "assessments"),
            col("metrics.assessed", "assessed")],
            computed=[{"selector": "assessments - assessed",
                       "text": "Not Assessed", "type": "number"}],
            filter_expr=ASSET_FILTER)],
        "transformations": [{"id": "filterFieldsByName", "options": {
            "include": {"names": ["Compliant", "Not Applicable",
                                  "Open Findings", "Not Assessed"]}}}],
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
                            ent.color_override("Compliant", ent.GREEN),
                            ent.color_override("Not Applicable", ent.BLUE),
                            ent.color_override("Open Findings", ent.RED),
                            ent.color_override("Not Assessed", ent.ORANGE)]},
    },
]

cov_gauge = ent.gauge(
    {"h": 5, "w": 5, "x": 9, "y": 0}, "Coverage",
    q(ASSET_URL, [col("assetId", "assetId", "string"),
                  col("metrics.assessments", "assessments"),
                  col("metrics.assessed", "assessed")],
      computed=[{"selector": "assessments > 0 ? assessed / assessments * 100 : 0",
                 "text": "Coverage", "type": "number"}],
      filter_expr=ASSET_FILTER),
    ent.COVERAGE_THRESHOLDS, desc="assessed / assessments for this asset.")
cov_gauge["transformations"] = [{"id": "filterFieldsByName",
                                 "options": {"include": {"names": ["Coverage"]}}}]
apanels.append(cov_gauge)

cora_gauge = ent.gauge(
    {"h": 5, "w": 5, "x": 14, "y": 0}, "CORA risk (this asset)",
    q(ASSET_URL, [col("assetId", "assetId", "string")] + ent.SEVERITY_COLS,
      computed=[{"selector": ent.CORA_ROW_EXPR, "text": "CORA",
                 "type": "number"}],
      filter_expr=ASSET_FILTER),
    ent.CORA_THRESHOLDS, desc=ent.CORA_DESC)
cora_gauge["transformations"] = [{"id": "filterFieldsByName",
                                  "options": {"include": {"names": ["CORA"]}}}]
apanels.append(cora_gauge)

apanels.append(sev_tiles({"h": 4, "w": 5, "x": 19, "y": 0},
                         "Findings", ASSET_URL, filter_expr=ASSET_FILTER))
apanels.append(status_tiles({"h": 4, "w": 10, "x": 9, "y": 5},
                            "Review workflow status", ASSET_URL,
                            filter_expr=ASSET_FILTER))
apanels.append(ent.review_age_tiles(
    {"h": 4, "w": 5, "x": 19, "y": 5}, "Review ages", ASSET_URL,
    filter_expr=ASSET_FILTER,
    id_col=ent.col("assetId", "assetId", "string")))

apanels.append(posture_table(
    {"h": 7, "w": 24, "x": 0, "y": 9}, "STIGs on this asset",
    f"{SUMMARY}?assetId=$asset",
    [col("benchmarkId", "STIG", "string"),
     col("revisionStr", "Rev", "string")],
    {"benchmarkId": "STIG"},
    desc="Per-benchmark posture for the selected asset."))

apanels.append(findings_table(
    {"h": 10, "w": 24, "x": 0, "y": 16}, "Failing rules on this asset",
    f"{API}/collections/$collection/findings?aggregator=ruleId&acceptedOnly=false&assetId=$asset",
    desc="Every open finding on the selected asset — the asset's punch "
         "list.",
    sort_field="Severity"))

asset_dash = base(
    "stig-cyber-asset", "STIG Posture — Cyber Asset Deep Dive",
    "Single-asset analysis: posture, risk, workflow status, per-STIG "
    "breakdown and the asset's failing rules.",
    apanels, [collection_variable(), asset_var])

OUT.mkdir(exist_ok=True)
for dash, name in ((drilldown, "stig-cyber-drilldown.json"),
                   (asset_dash, "stig-cyber-asset.json")):
    (OUT / name).write_text(json.dumps(dash, indent=2) + "\n")
    print(f"wrote {OUT / name}")
