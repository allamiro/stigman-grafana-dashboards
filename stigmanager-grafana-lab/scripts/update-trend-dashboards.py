#!/usr/bin/env python3
"""Generate the Prometheus-backed trend dashboards in
grafana/dashboards-trends/ (provisioned into the "STIG Posture (Trends)"
folder).

These mirror the live-API dashboards but read from Prometheus
(uid: stigmanager-prometheus), where the stigman-exporter records posture
history — so the Grafana time picker works here: pick "Last 24 hours" or
"Yesterday" and see posture as it was.

Dashboards:
  * stig-posture-management-trends — Management Review with trend lines
  * stig-posture-collection-trends — per-collection trends with a variable

Math matches the live dashboards; denominators are guarded with clamp_min.

Usage:  python3 scripts/update-trend-dashboards.py
"""
import json
import pathlib

DS = {"type": "prometheus", "uid": "stigmanager-prometheus"}
OUT = pathlib.Path(__file__).resolve().parent.parent / "grafana" / "dashboards-trends"

GREEN, BLUE, RED, ORANGE, YELLOW, DARKRED = (
    "green", "blue", "red", "orange", "#EAB839", "dark-red")
CAT1_COLOR, CAT2_COLOR, CAT3_COLOR = "#eba794", "#ffd68f", "#cdd2ea"

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


MC = 'collection_name=~"$collection"'   # management-trends multi-select


def sev_expr(scope):  # scope: "" (filtered enterprise sums) or per-collection
    def s(metric, sev):
        if scope == "collection":
            return (f'sum by (collection_name) ({metric}'
                    f'{{severity="{sev}"}})')
        return f'sum({metric}{{severity="{sev}",{MC}}})'
    parts = []
    for sev, w in (("high", 10), ("medium", 4), ("low", 1)):
        a = s("stigman_collection_assessments_by_severity", sev)
        d = s("stigman_collection_assessed_by_severity", sev)
        f = s("stigman_collection_findings", sev)
        parts.append(f"(({f} + {a} - {d}) / clamp_min({a}, 1)) * {w}")
    return "(" + " + ".join(parts) + ") / 15 * 100"


ENT = {
    "cora": sev_expr(""),
    "coverage": (f"sum(stigman_collection_assessed{{{MC}}}) / "
                 f"clamp_min(sum(stigman_collection_assessments{{{MC}}}), 1) * 100"),
    "compliance": (f'sum(stigman_collection_results{{result="pass",{MC}}}) / '
                   f"clamp_min(sum(stigman_collection_assessed{{{MC}}}), 1) * 100"),
    "open": f'sum(stigman_collection_results{{result="fail",{MC}}})',
    "cat1": f'sum(stigman_collection_findings{{severity="high",{MC}}})',
}


def target(expr, legend="", refid="A", instant=False):
    t = {"refId": refid, "datasource": DS, "expr": expr, "editorMode": "code",
         "legendFormat": legend or "__auto", "range": not instant,
         "instant": instant}
    return t


def stat(grid, title, expr, unit, thresholds, decimals=1, desc="",
         color_mode="value", mappings=None):
    p = {
        "type": "stat", "title": title, "description": desc, "gridPos": grid,
        "datasource": DS, "targets": [target(expr)],
        "options": {"reduceOptions": {"values": False, "calcs": ["lastNotNull"]},
                    "colorMode": color_mode, "graphMode": "area",
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


def timeseries(grid, title, targets, unit, desc="", max_y=None,
               overrides=None, thresholds=None):
    return {
        "type": "timeseries", "title": title, "description": desc,
        "gridPos": grid, "datasource": DS, "targets": targets,
        "options": {"legend": {"displayMode": "list", "placement": "bottom",
                               "showLegend": True, "calcs": []},
                    "tooltip": {"mode": "multi", "sort": "desc"}},
        "fieldConfig": {"defaults": {
            "unit": unit, "min": 0, **({"max": max_y} if max_y else {}),
            "color": {"mode": "palette-classic"},
            "thresholds": thresholds or NEUTRAL_THRESHOLDS,
            "custom": {"drawStyle": "line", "lineWidth": 2, "fillOpacity": 12,
                       "spanNulls": True, "showPoints": "never",
                       "lineInterpolation": "smooth",
                       "axisCenteredZero": False, "axisPlacement": "auto",
                       "stacking": {"mode": "none", "group": "A"}}},
            "overrides": overrides or []},
    }


def name_override(name, color):
    return {"matcher": {"id": "byName", "options": name},
            "properties": [{"id": "color",
                            "value": {"mode": "fixed", "fixedColor": color}}]}


def write(dash, filename):
    (OUT / filename).write_text(json.dumps(dash, indent=2) + "\n")
    print(f"wrote {OUT / filename}")


def base(uid, title, desc, tags, panels, templating=None):
    for pid, p in enumerate(panels, start=1):
        p["id"] = pid
    return {
        "uid": uid, "title": title, "description": desc, "tags": tags,
        "timezone": "browser", "editable": True, "fiscalYearStartMonth": 0,
        "graphTooltip": 1, "liveNow": False, "schemaVersion": 39,
        "version": 1, "refresh": "1m",
        "time": {"from": "now-24h", "to": "now"},
        "templating": {"list": templating or []},
        "annotations": {"list": []},
        "links": [
            {"title": "Live: Management Review", "type": "link",
             "icon": "dashboard", "url": "/d/stig-posture-management",
             "targetBlank": False, "asDropdown": False, "includeVars": False,
             "keepTime": False, "tags": []},
            {"title": "Live: Enterprise Overview", "type": "link",
             "icon": "dashboard", "url": "/d/stig-posture-enterprise",
             "targetBlank": False, "asDropdown": False, "includeVars": False,
             "keepTime": False, "tags": []}],
        "panels": panels,
    }


# ======================================================================
# Dashboard 1: Management Review (Trends)
# ======================================================================
ABOUT = """# Security Compliance — Management Review (Trends)
Same figures as the live Management Review, but backed by **recorded history**
(Prometheus, 60s samples, 90d retention) — so the **time picker works here**:
choose *Last 24 hours*, *Last 7 days*, or an absolute range to see posture as
it was and whether it is improving. Current-state tiles show the latest sample.
"""

panels = [{
    "type": "text", "title": "", "gridPos": {"h": 3, "w": 24, "x": 0, "y": 0},
    "options": {"mode": "markdown", "code": {"language": "plaintext",
                "showLineNumbers": False, "showMiniMap": False},
                "content": ABOUT},
    "fieldConfig": {"defaults": {}, "overrides": []},
}]

# Tier 1 — current values (latest sample), same targets as the live board
panels.append(stat({"h": 6, "w": 7, "x": 0, "y": 3}, "Bottom line",
                   ENT["cora"], "none", CORA_THRESHOLDS,
                   color_mode="background", mappings=RISK_MAPPINGS,
                   desc="Latest recorded CORA-style risk verdict."))
panels.append(stat({"h": 6, "w": 5, "x": 7, "y": 3},
                   "Critical findings — target 0", ENT["cat1"], "none",
                   ALERT_THRESHOLDS, decimals=0, color_mode="background",
                   desc="Open CAT I findings, latest sample."))
panels.append(stat({"h": 6, "w": 4, "x": 12, "y": 3},
                   "Compliance — target 90%+", ENT["compliance"], "percent",
                   COVERAGE_THRESHOLDS,
                   desc="sum(pass)/sum(assessed), latest sample."))
panels.append(stat({"h": 6, "w": 4, "x": 16, "y": 3},
                   "Coverage — target 90%+", ENT["coverage"], "percent",
                   COVERAGE_THRESHOLDS,
                   desc="sum(assessed)/sum(assessments), latest sample."))
panels.append(stat({"h": 6, "w": 4, "x": 20, "y": 3}, "Open findings",
                   ENT["open"], "none", ALERT_THRESHOLDS, decimals=0,
                   desc="All open failed checks, latest sample."))

# Tier 2 — the trends themselves
panels.append(timeseries(
    {"h": 9, "w": 12, "x": 0, "y": 9}, "Coverage and compliance over time",
    [target(ENT["coverage"], "Coverage %", "A"),
     target(ENT["compliance"], "Compliance %", "B")],
    "percent", desc="Both should climb toward the 90% target line.",
    max_y=100,
    overrides=[name_override("Coverage %", BLUE),
               name_override("Compliance %", GREEN)]))

panels.append(timeseries(
    {"h": 9, "w": 12, "x": 12, "y": 9}, "Risk score over time",
    [target(ENT["cora"], "Enterprise risk %", "A"),
     target('stigman_collection_cora_percent{collection_name=~"$collection"}',
            "{{collection_name}}", "B")],
    "percent", desc="Enterprise CORA-style score plus each environment. "
                    "Down is good.",
    overrides=[name_override("Enterprise risk %", RED)]))

panels.append(timeseries(
    {"h": 9, "w": 12, "x": 0, "y": 18}, "Open findings by severity over time",
    [target('sum(stigman_collection_findings{severity="high"})',
            "CAT I (critical)", "A"),
     target('sum(stigman_collection_findings{severity="medium"})',
            "CAT II (medium)", "B"),
     target('sum(stigman_collection_findings{severity="low"})',
            "CAT III (low)", "C")],
    "none", desc="Remediation progress: each line should trend down.",
    overrides=[name_override("CAT I (critical)", CAT1_COLOR),
               name_override("CAT II (medium)", CAT2_COLOR),
               name_override("CAT III (low)", CAT3_COLOR)]))

panels.append(timeseries(
    {"h": 9, "w": 12, "x": 12, "y": 18}, "Unassessed backlog over time",
    [target('sum(stigman_collection_assessments{collection_name=~"$collection"}) - '
            'sum(stigman_collection_assessed{collection_name=~"$collection"})',
            "Unassessed checks", "A")],
    "none", desc="Checks not yet performed. Should trend toward zero as "
                 "coverage improves.",
    overrides=[name_override("Unassessed checks", ORANGE)]))

mgmt_var = {
    "name": "collection", "label": "Collections", "type": "query",
    "datasource": DS, "refresh": 2, "multi": True, "includeAll": True,
    "sort": 1,
    "query": {"qryType": 1, "query":
              "label_values(stigman_collection_assessments, collection_name)",
              "refId": "PrometheusVariableQueryEditor-VariableQuery"},
    "current": {"selected": True, "text": ["All"], "value": ["$__all"]},
    "options": [],
}
mgmt = base(
    "stig-posture-management-trends",
    "STIG Posture — Management Review (Trends)",
    "Historical posture trends recorded by stigman-exporter into Prometheus.",
    ["stig", "posture", "management", "trends"], panels, [mgmt_var])

# ======================================================================
# Dashboard 2: Per Collection (Trends)
# ======================================================================
BYC = 'sum by (collection_name)'
sel = '{collection_name=~"$collection"}'
coll = {
    "coverage": (f'{BYC}(stigman_collection_assessed{sel}) / '
                 f'clamp_min({BYC}(stigman_collection_assessments{sel}), 1) * 100'),
    "compliance": (f'{BYC}(stigman_collection_results{{result="pass",collection_name=~"$collection"}}) / '
                   f'clamp_min({BYC}(stigman_collection_assessed{sel}), 1) * 100'),
    "cora": f'stigman_collection_cora_percent{sel}',
    "open": f'{BYC}(stigman_collection_results{{result="fail",collection_name=~"$collection"}})',
    "cat1": f'{BYC}(stigman_collection_findings{{severity="high",collection_name=~"$collection"}})',
}

cpanels = [
    stat({"h": 5, "w": 6, "x": 0, "y": 0}, "Risk score (latest)",
         coll["cora"], "percent", CORA_THRESHOLDS, color_mode="background",
         desc="Latest recorded CORA-style score for $collection."),
    stat({"h": 5, "w": 6, "x": 6, "y": 0}, "Critical findings (latest)",
         coll["cat1"], "none", ALERT_THRESHOLDS, decimals=0,
         color_mode="background", desc="Open CAT I findings."),
    stat({"h": 5, "w": 6, "x": 12, "y": 0}, "Compliance (latest)",
         coll["compliance"], "percent", COVERAGE_THRESHOLDS,
         desc="pass/assessed."),
    stat({"h": 5, "w": 6, "x": 18, "y": 0}, "Coverage (latest)",
         coll["coverage"], "percent", COVERAGE_THRESHOLDS,
         desc="assessed/assessments."),
    timeseries({"h": 9, "w": 12, "x": 0, "y": 5},
               "Coverage and compliance over time",
               [target(coll["coverage"], "Coverage %", "A"),
                target(coll["compliance"], "Compliance %", "B")],
               "percent", max_y=100,
               overrides=[name_override("Coverage %", BLUE),
                          name_override("Compliance %", GREEN)]),
    timeseries({"h": 9, "w": 12, "x": 12, "y": 5}, "Risk score over time",
               [target(coll["cora"], "{{collection_name}}", "A")],
               "percent", desc="Down is good."),
    timeseries({"h": 9, "w": 12, "x": 0, "y": 14},
               "Open findings by severity over time",
               [target(f'{BYC}(stigman_collection_findings{{severity="high",collection_name=~"$collection"}})',
                       "CAT I (critical)", "A"),
                target(f'{BYC}(stigman_collection_findings{{severity="medium",collection_name=~"$collection"}})',
                       "CAT II (medium)", "B"),
                target(f'{BYC}(stigman_collection_findings{{severity="low",collection_name=~"$collection"}})',
                       "CAT III (low)", "C")],
               "none",
               overrides=[name_override("CAT I (critical)", CAT1_COLOR),
                          name_override("CAT II (medium)", CAT2_COLOR),
                          name_override("CAT III (low)", CAT3_COLOR)]),
    timeseries({"h": 9, "w": 12, "x": 12, "y": 14},
               "Results composition over time",
               [target(f'{BYC}(stigman_collection_results{{result="pass",collection_name=~"$collection"}})',
                       "Compliant", "A"),
                target(f'{BYC}(stigman_collection_results{{result="fail",collection_name=~"$collection"}})',
                       "Open Findings", "B"),
                target(f'{BYC}(stigman_collection_results{{result="notapplicable",collection_name=~"$collection"}})',
                       "Not Applicable", "C"),
                target(f'{BYC}(stigman_collection_assessments{sel}) - '
                       f'{BYC}(stigman_collection_assessed{sel})',
                       "Not Assessed", "D")],
               "none",
               overrides=[name_override("Compliant", GREEN),
                          name_override("Open Findings", RED),
                          name_override("Not Applicable", BLUE),
                          name_override("Not Assessed", ORANGE)]),
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
    "stig-posture-collection-trends",
    "STIG Posture — Per Collection (Trends)",
    "Historical posture trends for a single collection.",
    ["stig", "posture", "collection", "trends"], cpanels, [coll_var])

OUT.mkdir(exist_ok=True)
write(mgmt, "stig-posture-management-trends.json")
write(collection, "stig-posture-collection-trends.json")
