#!/usr/bin/env python3
"""Generate grafana/dashboards/stig-posture-management.json.

Management Review dashboard built on researched executive-dashboard best
practices:

  * Five-second rule — a single verdict tile top-left answers "how are we
    doing?" before anything else is read.
  * Inverted pyramid — Tier 1: outcome KPIs with explicit targets;
    Tier 2: driver metrics that explain WHY (posture mix, open findings,
    unassessed backlog); Tier 3: an actionable "where to focus first"
    ranking so the meeting ends with next steps, not just numbers.
  * Every KPI carries its target in the title; colors follow one semantic
    (green good / red bad); business language, no STIG jargon unexplained.
  * Drill-down is via dashboard links, not clutter on this page.

Reuses the enterprise generator's query builders/CORA math so figures can
never disagree between dashboards.

Usage:  python3 scripts/update-management-dashboard.py
"""
import importlib.util
import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "ent", HERE / "update-enterprise-dashboard.py")
ent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ent)

DS, META_URL, col = ent.DS, ent.META_URL, ent.col

ABOUT_MD = """# Security Compliance — Management Review
**Scope: all monitored environments** · live data from the assessment system (read-only reporting account) · targets: coverage **>=90%**, critical findings **0**.
Read top to bottom: **1) Bottom line** → **2) What's driving it** → **3) Where to focus first**. Detail dashboards are linked top-right.
"""

RISK_MAPPINGS = [
    {"type": "range", "options": {"from": 20, "to": 1e9,
     "result": {"text": "VERY HIGH RISK", "color": "red", "index": 0}}},
    {"type": "range", "options": {"from": 10, "to": 20,
     "result": {"text": "HIGH RISK", "color": "orange", "index": 1}}},
    {"type": "range", "options": {"from": 0.000001, "to": 10,
     "result": {"text": "MODERATE RISK", "color": "#EAB839", "index": 2}}},
    {"type": "range", "options": {"from": -1, "to": 0.000001,
     "result": {"text": "LOW RISK", "color": "green", "index": 3}}},
]


def q(columns, summarize=None, alias=None, computed=None):
    cols = list(columns)
    if not any(c["text"] == "collectionId" for c in cols):
        cols.append(col("collectionId", "collectionId", "string"))
    return ent.query("A", META_URL, cols, computed=computed,
                     filter_expr=ent.FILTER, summarize=summarize, alias=alias)


def stat(grid, title, query, unit, thresholds, decimals=0, desc="",
         color_mode="value", mappings=None):
    p = ent.stat(grid, title, query, unit=unit, thresholds=thresholds,
                 decimals=decimals, desc=desc)
    p["options"]["colorMode"] = color_mode
    if mappings is not None:
        p["fieldConfig"]["defaults"]["mappings"] = mappings
    return p


SEVERITY_COLORS = [("CAT I (critical)", ent.CAT1_COLOR),
                   ("CAT II (medium)", ent.CAT2_COLOR),
                   ("CAT III (low)", ent.CAT3_COLOR)]


def severity_bars(grid, title, target, desc):
    """Colored tiles matching the native STIG Manager UI severity boxes."""
    return {
        "type": "stat", "title": title, "description": desc,
        "gridPos": grid, "datasource": DS, "targets": [target],
        "transformations": [{"id": "filterFieldsByName", "options": {
            "include": {"names": [n for n, _ in SEVERITY_COLORS]}}}],
        "options": {"reduceOptions": {"values": False,
                                      "calcs": ["lastNotNull"]},
                    "colorMode": "background", "graphMode": "none",
                    "justifyMode": "auto", "orientation": "auto",
                    "textMode": "value_and_name", "wideLayout": True},
        "fieldConfig": {"defaults": {"unit": "none", "decimals": 0,
                                     "thresholds": ent.NEUTRAL_THRESHOLDS,
                                     "color": {"mode": "thresholds"}},
                        "overrides": [ent.color_override(n, c)
                                      for n, c in SEVERITY_COLORS]},
    }


panels = []

# ======== header ========================================================
panels.append({
    "type": "text", "title": "", "gridPos": {"h": 3, "w": 20, "x": 0, "y": 0},
    "options": {"mode": "markdown", "code": {"language": "plaintext",
                "showLineNumbers": False, "showMiniMap": False},
                "content": ABOUT_MD},
    "fieldConfig": {"defaults": {}, "overrides": []},
})

# data-freshness tile: latest assessment activity across all environments,
# so a green dashboard is never mistaken for a recently-verified one.
_fresh = ent.stat(
    {"h": 3, "w": 4, "x": 20, "y": 0}, "Assessments last updated",
    q([col("metrics.maxTouchTs", "Last activity", "timestamp")]),
    unit="dateTimeFromNow", thresholds=ent.NEUTRAL_THRESHOLDS,
    desc="Most recent review activity (maxTouchTs) across all environments. "
         "If this is old, the posture shown here is equally old.")
_fresh["transformations"] = [
    {"id": "filterFieldsByName",
     "options": {"include": {"names": ["Last activity"]}}},
    {"id": "sortBy", "options": {
        "fields": {}, "sort": [{"field": "Last activity", "desc": False}]}},
    # stat panels reduce numeric fields only: convert time -> epoch ms
    {"id": "convertFieldType", "options": {"conversions": [
        {"targetField": "Last activity", "destinationType": "number"}],
        "fields": {}}}]
panels.append(_fresh)

# ======== TIER 1 — bottom line (five-second rule, targets shown) ========
panels.append(stat(
    {"h": 7, "w": 7, "x": 0, "y": 3}, "Bottom line",
    q(list(ent.SEVERITY_COLS), summarize=ent.CORA_SUM_EXPR, alias="Risk"),
    "none", ent.CORA_THRESHOLDS, decimals=1, color_mode="background",
    mappings=RISK_MAPPINGS,
    desc="Single verdict for the whole estate, from the CORA-style weighted "
         "risk score (open findings + unassessed checks, weighted CAT I x10, "
         "CAT II x4, CAT III x1). Bands: 0% Low, >0% Moderate, >=10% High, "
         ">=20% Very High. " + ent.CORA_DESC))

panels.append(stat(
    {"h": 7, "w": 5, "x": 7, "y": 3}, "Critical findings — target 0",
    q([col("metrics.findings.high", "high")], summarize="sum(high)",
      alias="CAT I open"),
    "none", ent.ALERT_THRESHOLDS, color_mode="background",
    desc="Open CAT I (mission-critical) weaknesses. Every one of these "
         "needs an owner and a remediation date."))

panels.append(stat(
    {"h": 7, "w": 4, "x": 12, "y": 3}, "Compliance — target 90%+",
    q([col("metrics.results.pass", "pass"), col("metrics.assessed", "assessed")],
      summarize="sum(assessed) > 0 ? sum(pass) / sum(assessed) * 100 : 0",
      alias="Compliance"),
    "percent", ent.COVERAGE_THRESHOLDS, decimals=1,
    desc="Share of assessed checks that passed, across all environments "
         "(sum of passes / sum of assessed)."))

panels.append(stat(
    {"h": 7, "w": 4, "x": 16, "y": 3}, "Coverage — target 90%+",
    q([col("metrics.assessments", "assessments"),
       col("metrics.assessed", "assessed")],
      summarize="sum(assessments) > 0 ? sum(assessed) / sum(assessments) * 100 : 0",
      alias="Coverage"),
    "percent", ent.COVERAGE_THRESHOLDS, decimals=1,
    desc="How much of the estate has actually been checked. While coverage "
         "is below target, compliance and risk figures understate reality."))

panels.append(stat(
    {"h": 7, "w": 4, "x": 20, "y": 3}, "Open findings",
    q([col("metrics.results.fail", "fail")], summarize="sum(fail)",
      alias="Open findings"),
    "none", ent.ALERT_THRESHOLDS, color_mode="value",
    desc="All failed checks (any severity) currently open across all "
         "environments."))

# ======== TIER 2 — what's driving it ====================================
panels.append(ent.donut(
    {"h": 9, "w": 8, "x": 0, "y": 10},
    "Driver 1 — posture mix (selected collections)", META_URL,
    desc="Composition of every required check: passed (green), open "
         "findings (red), not applicable (blue), not yet assessed "
         "(orange). A large orange share means the picture is incomplete."))

panels.append(severity_bars(
    {"h": 9, "w": 8, "x": 8, "y": 10},
    "Driver 2 — open findings by severity",
    q([col("metrics.findings.high", "CAT I (critical)"),
       col("metrics.findings.medium", "CAT II (medium)"),
       col("metrics.findings.low", "CAT III (low)")]),
    "Confirmed failures awaiting remediation, worst first. CAT I drives "
    "the risk score 10x harder than CAT III."))

panels.append(severity_bars(
    {"h": 9, "w": 8, "x": 16, "y": 10},
    "Driver 3 — unassessed backlog by severity",
    q([col("metrics.assessmentsBySeverity.high", "aH"),
       col("metrics.assessedBySeverity.high", "sH"),
       col("metrics.assessmentsBySeverity.medium", "aM"),
       col("metrics.assessedBySeverity.medium", "sM"),
       col("metrics.assessmentsBySeverity.low", "aL"),
       col("metrics.assessedBySeverity.low", "sL")],
      computed=[
          {"selector": "aH - sH", "text": "CAT I (critical)", "type": "number"},
          {"selector": "aM - sM", "text": "CAT II (medium)", "type": "number"},
          {"selector": "aL - sL", "text": "CAT III (low)", "type": "number"}]),
    "Checks not yet performed, by severity. These count toward risk "
    "because an unchecked critical control is not a passed one."))
# keep only the computed severity fields (hide the raw helper columns)
panels[-1]["transformations"] = [{
    "id": "filterFieldsByName",
    "options": {"include": {"names": ["CAT I (critical)", "CAT II (medium)",
                                      "CAT III (low)"]}}}]

# ======== TIER 3 — where to focus first =================================
focus_q = q(
    [col("name", "Environment", "string"),
     col("metrics.results.fail", "Open findings"),
     col("metrics.assessments", "assessments"),
     col("metrics.assessed", "assessed"),
     col("assets", "Assets")] + ent.SEVERITY_COLS,
    computed=[
        {"selector": "assessments > 0 ? assessed / assessments * 100 : 0",
         "text": "Coverage %", "type": "number"},
        {"selector": ent.CORA_ROW_EXPR, "text": "Risk score %", "type": "number"}])
panels.append({
    "type": "table", "title": "Where to focus first (highest risk on top)",
    "description": "Environments ranked by risk score — the top row is the "
                   "first place to put remediation effort. " + ent.CORA_DESC,
    "gridPos": {"h": 7, "w": 24, "x": 0, "y": 19}, "datasource": DS,
    "targets": [focus_q],
    "transformations": [
        {"id": "organize", "options": {
            "excludeByName": {"assessments": True, "assessed": True,
                              "assessmentsHigh": True, "assessmentsMedium": True,
                              "assessmentsLow": True, "assessedHigh": True,
                              "assessedMedium": True, "assessedLow": True,
                              "medium": True, "low": True},
            "indexByName": {"Environment": 0, "Risk score %": 1, "high": 2,
                            "Open findings": 3, "Coverage %": 4, "Assets": 5},
            "renameByName": {"high": "Critical (CAT I)"}}},
        {"id": "sortBy", "options": {
            "fields": {}, "sort": [{"field": "Risk score %", "desc": True}]}}],
    "options": {"cellHeight": "md", "showHeader": True,
                "footer": {"show": False, "reducer": ["sum"],
                           "countRows": False, "fields": ""}},
    "fieldConfig": {"defaults": {"custom": {"align": "auto",
                                            "cellOptions": {"type": "auto"},
                                            "inspect": False},
                    "thresholds": ent.NEUTRAL_THRESHOLDS},
                    "overrides": [
        {"matcher": {"id": "byName", "options": "Risk score %"},
         "properties": [{"id": "unit", "value": "percent"},
                        {"id": "decimals", "value": 1},
                        {"id": "thresholds", "value": ent.CORA_THRESHOLDS},
                        {"id": "custom.cellOptions",
                         "value": {"type": "color-background", "mode": "basic"}}]},
        {"matcher": {"id": "byName", "options": "Coverage %"},
         "properties": [{"id": "unit", "value": "percent"},
                        {"id": "decimals", "value": 1},
                        {"id": "thresholds", "value": ent.COVERAGE_THRESHOLDS},
                        {"id": "custom.cellOptions",
                         "value": {"type": "color-background", "mode": "basic"}}]},
        {"matcher": {"id": "byName", "options": "Critical (CAT I)"},
         "properties": [{"id": "thresholds", "value": ent.ALERT_THRESHOLDS},
                        {"id": "custom.cellOptions",
                         "value": {"type": "color-text"}}]}]},
})

for pid, p in enumerate(panels, start=1):
    p["id"] = pid

dashboard = {
    "uid": "stig-posture-management",
    "title": "STIG Posture — Management Review",
    "description": "Best-practice management dashboard: 5-second verdict, "
                   "targets on every KPI, drivers, and a ranked focus list. "
                   "All collections aggregated; read-only data.",
    "tags": ["stig", "posture", "management"],
    "timezone": "browser",
    "editable": True,
    "fiscalYearStartMonth": 0,
    "graphTooltip": 0,
    "liveNow": False,
    "schemaVersion": 39,
    "version": 1,
    "refresh": "5m",
    "time": {"from": "now-6h", "to": "now"},
    "templating": {"list": [ent.collections_variable()]},
    "annotations": {"list": []},
    "links": [
        {"title": "Details: Enterprise Overview", "type": "link", "icon": "dashboard",
         "url": "/d/stig-posture-enterprise", "targetBlank": False,
         "asDropdown": False, "includeVars": False, "keepTime": False, "tags": []},
        {"title": "Drill-down: Per Collection", "type": "link", "icon": "dashboard",
         "url": "/d/stig-posture-collection", "targetBlank": False,
         "asDropdown": False, "includeVars": False, "keepTime": False, "tags": []},
    ],
    "panels": panels,
}

out = HERE.parent / "grafana" / "dashboards" / "stig-posture-management.json"
out.write_text(json.dumps(dashboard, indent=2) + "\n")
print(f"wrote {out}")
