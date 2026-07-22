#!/usr/bin/env python3
"""Generate grafana/dashboards/stig-posture-executive.json.

A one-page management view: every number aggregates ALL collections (no
collection picker, no per-collection breakdown). Detail lives in the
Enterprise Overview and Per Collection dashboards, which are linked from the
top of this one.

Reuses query builders and CORA expressions from update-enterprise-dashboard.py
so the math can never drift between dashboards.

Usage:  python3 scripts/update-executive-dashboard.py
"""
import importlib.util
import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "ent", HERE / "update-enterprise-dashboard.py")
ent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ent)

DS = ent.DS
META_URL = ent.META_URL
col = ent.col
color_override = ent.color_override

ABOUT_MD = """# STIG Compliance — Executive Summary

Security compliance status across **all monitored environments** (every STIG
Manager collection — currently *Linux Production* and *Windows Production*).
Figures are pulled live from the assessment system by a read-only reporting
account and are computed from totals across all environments.

**The four numbers that matter:** overall **Risk level**, **Critical (CAT I)
findings** (any number above zero needs a remediation plan), **Compliance
rate** (checks passing, of those assessed), and **Assessment coverage** (how
much of the estate has actually been checked — low coverage means the other
numbers understate risk).

Need detail? Use the links in the top-right: *Enterprise Overview* for the
per-environment breakdown, *Per Collection* for a single environment.
"""

RISK_MAPPINGS = [
    {"type": "range", "options": {"from": 20, "to": 1e9,
     "result": {"text": "VERY HIGH", "color": "red", "index": 0}}},
    {"type": "range", "options": {"from": 10, "to": 20,
     "result": {"text": "HIGH", "color": "orange", "index": 1}}},
    {"type": "range", "options": {"from": 0.000001, "to": 10,
     "result": {"text": "MODERATE", "color": "#EAB839", "index": 2}}},
    {"type": "range", "options": {"from": -1, "to": 0.000001,
     "result": {"text": "LOW", "color": "green", "index": 3}}},
]


def q(columns, summarize=None, alias=None, computed=None):
    # No collection variable on this dashboard: filter_expr must be None.
    return ent.query("A", META_URL, columns, computed=computed,
                     filter_expr=None, summarize=summarize, alias=alias)


def big_stat(grid, title, query, unit, thresholds, decimals=0, desc="",
             color_mode="value", mappings=None):
    p = ent.stat(grid, title, query, unit=unit, thresholds=thresholds,
                 decimals=decimals, desc=desc)
    p["options"]["colorMode"] = color_mode
    p["options"]["textMode"] = "auto"
    if mappings is not None:
        p["fieldConfig"]["defaults"]["mappings"] = mappings
    return p


panels = []

# ---- framing text ------------------------------------------------------
panels.append({
    "type": "text", "title": "", "gridPos": {"h": 6, "w": 24, "x": 0, "y": 0},
    "options": {"mode": "markdown", "code": {"language": "plaintext",
                "showLineNumbers": False, "showMiniMap": False},
                "content": ABOUT_MD},
    "fieldConfig": {"defaults": {}, "overrides": []},
})

# ---- headline row ------------------------------------------------------
panels.append(big_stat(
    {"h": 6, "w": 5, "x": 0, "y": 6}, "Overall risk level",
    q(list(ent.SEVERITY_COLS), summarize=ent.CORA_SUM_EXPR, alias="Risk"),
    "none", ent.CORA_THRESHOLDS, decimals=1, color_mode="background",
    mappings=RISK_MAPPINGS,
    desc="CORA-style weighted risk across all environments. "
         "LOW / MODERATE / HIGH / VERY HIGH. " + ent.CORA_DESC))

panels.append(ent.gauge(
    {"h": 6, "w": 5, "x": 5, "y": 6}, "Risk score",
    q([c for c in ent.SEVERITY_COLS], summarize=ent.CORA_SUM_EXPR,
      alias="CORA"),
    ent.CORA_THRESHOLDS,
    desc="Same risk figure as the verdict tile, shown against the "
         "0-100% band scale. " + ent.CORA_DESC))

panels.append(big_stat(
    {"h": 6, "w": 5, "x": 10, "y": 6}, "Critical (CAT I) findings",
    q([col("metrics.findings.high", "high")], summarize="sum(high)",
      alias="CAT I"),
    "none", ent.ALERT_THRESHOLDS, color_mode="background",
    desc="Mission-critical weaknesses currently open across all "
         "environments. Target: 0."))

panels.append(big_stat(
    {"h": 6, "w": 5, "x": 15, "y": 6}, "Compliance rate",
    q([col("metrics.results.pass", "pass"), col("metrics.assessed", "assessed")],
      summarize="sum(assessed) > 0 ? sum(pass) / sum(assessed) * 100 : 0",
      alias="Compliance"),
    "percent", ent.COVERAGE_THRESHOLDS, decimals=1,
    desc="Of everything assessed so far, the share that passed. "
         "sum(pass) / sum(assessed) across all environments."))

panels.append(big_stat(
    {"h": 6, "w": 4, "x": 20, "y": 6}, "Assessment coverage",
    q([col("metrics.assessments", "assessments"),
       col("metrics.assessed", "assessed")],
      summarize="sum(assessments) > 0 ? sum(assessed) / sum(assessments) * 100 : 0",
      alias="Coverage"),
    "percent", ent.COVERAGE_THRESHOLDS, decimals=1,
    desc="Share of required checks that have been performed. Low coverage "
         "means the risk picture is incomplete."))

# ---- second row: posture donut, findings by severity, scope ------------
panels.append(ent.donut({"h": 9, "w": 9, "x": 0, "y": 12},
                        "Where we stand — all environments", META_URL,
                        desc="All required checks across every environment: "
                             "passed (green), open findings (red), not "
                             "applicable (blue), not yet assessed (orange)."))
# donut() adds the collections filter only for meta URLs — strip it here
# because this dashboard has no collection variable.
panels[-1]["targets"][0].pop("filterExpression", None)

panels.append({
    "type": "stat", "title": "Open findings by severity — all environments",
    "description": "CAT I = critical, CAT II = medium, CAT III = low. "
                   "Summed across every environment. Colors match the "
                   "STIG Manager UI.",
    "gridPos": {"h": 9, "w": 9, "x": 9, "y": 12}, "datasource": DS,
    "targets": [q([col("metrics.findings.high", "CAT I (critical)"),
                   col("metrics.findings.medium", "CAT II (medium)"),
                   col("metrics.findings.low", "CAT III (low)")],
                  summarize=None)],
    "options": {"reduceOptions": {"values": False, "calcs": ["sum"]},
                "colorMode": "background", "graphMode": "none",
                "justifyMode": "auto", "orientation": "auto",
                "textMode": "value_and_name", "wideLayout": True},
    "fieldConfig": {"defaults": {"unit": "none", "decimals": 0,
                                 "thresholds": ent.NEUTRAL_THRESHOLDS,
                                 "color": {"mode": "thresholds"}},
                    "overrides": [
                        ent.color_override("CAT I (critical)", ent.CAT1_COLOR),
                        ent.color_override("CAT II (medium)", ent.CAT2_COLOR),
                        ent.color_override("CAT III (low)", ent.CAT3_COLOR)]},
})

scope_stats = [
    ("Environments (collections)",
     q([col("collectionId", "collectionId", "string")],
       summarize="count(collectionId)", alias="Environments"),
     "Monitored STIG Manager collections."),
    ("Assets under assessment",
     q([col("assets", "assets")], summarize="sum(assets)", alias="Assets"),
     "Servers/devices with assigned STIG checklists."),
    ("Total required checks",
     q([col("metrics.assessments", "assessments")],
       summarize="sum(assessments)", alias="Checks"),
     "Individual STIG rules to be assessed across all assets."),
]
for i, (title, query, desc) in enumerate(scope_stats):
    panels.append(big_stat({"h": 3, "w": 6, "x": 18, "y": 12 + 3 * i},
                           title, query, "none", ent.NEUTRAL_THRESHOLDS,
                           desc=desc))

for pid, p in enumerate(panels, start=1):
    p["id"] = pid

dashboard = {
    "uid": "stig-posture-executive",
    "title": "STIG Posture — Executive Summary",
    "description": "One-page management view of STIG compliance across all "
                   "collections. Read-only data via nexus-reporter.",
    "tags": ["stig", "posture", "executive"],
    "timezone": "browser",
    "editable": True,
    "fiscalYearStartMonth": 0,
    "graphTooltip": 0,
    "liveNow": False,
    "schemaVersion": 39,
    "version": 1,
    "refresh": "5m",
    "time": {"from": "now-6h", "to": "now"},
    "templating": {"list": []},
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

out = HERE.parent / "grafana" / "dashboards" / "stig-posture-executive.json"
out.write_text(json.dumps(dashboard, indent=2) + "\n")
print(f"wrote {out}")
