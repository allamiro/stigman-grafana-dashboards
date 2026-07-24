# Dashboard design system

The written-down style guide for the STIG posture dashboards, so every board
reads as one product. Grafana's own best-practice advice is to *"use a scripting
library to generate dashboards"* and to *"write down your design guidelines to
maintain consistency"* — this file is that, and the generator is that library.

> Applies to the newer dashboards (Platform Health, Risk Forecast, CAT I War
> Room, Review Backlog, Asset Inventory, Compliance Scorecard, Access &
> Governance). The original set predates this guide.

## 1. Context first — every dashboard has a goal

Grafana: *"A dashboard should tell a story or answer a question."* Each board
opens with a **markdown context panel** stating the goal, the question it
answers, and how to read it. If a panel doesn't serve that goal, it's cut.

## 2. Story structure — general → specific, top → bottom

Panels are grouped with **row panels** in a fixed narrative order:

1. **Context** — what this is and how to read it.
2. **Bottom line** — 3–4 KPI stat tiles with sparklines: the state *now*.
3. **Trends** — timeseries: how it got here, week-over-week.
4. **Detail** — tables: the specific assets / rules / grants to act on.

Reading gravity is top-left → bottom-right, biggest picture first.

## 3. Colour — semantic, consistent, CVD-safe

Blue/neutral = fine, warm = attention (Grafana: *"blue means good, red means
bad"*). Values are the validated data-viz **status palette** (never re-themed):

| Role | Hex | Used for |
|---|---|---|
| good | `#0ca30c` | at/above target |
| warning | `#fab219` | drifting |
| serious | `#ec835a` | elevated (e.g. Manage grant) |
| critical | `#d03b3b` | breach / CAT I / Owner grant |

**Severity** is a fixed ordered ramp, *always shown with a text label* so meaning
is never colour-alone: CAT I `#d03b3b` · CAT II `#eb6834` · CAT III `#eda100`.

## 4. Fixed thresholds — the same number means the same thing everywhere

| Measure | good | warning | critical |
|---|---|---|---|
| Coverage % (higher better) | ≥ 95 | 80–95 | < 80 |
| CORA risk % (lower better) | < 20 | 20–40 | ≥ 40 |
| Findings / CAT I counts | 0 | ≥ 1 | — |
| Review age | < 30 d | 30–90 d | > 90 d |

Trend panels with a target draw it as a **threshold line** (e.g. CORA ≤ 20).

## 5. Accessibility

- **Labels, not colour alone** — status/role/scrape values carry a text mapping
  (UP/DOWN, Restricted…Owner, Pinned) alongside colour.
- **Legends** for every panel with ≥ 2 series; single-series panels use the title.
- **One axis per panel** — never a dual-y-axis (it invents correlations).
- Tables double as the WCAG-clean view of any coloured cell.

## 6. Consistency mechanisms

- **Generator** (`scripts`/scratch `ds.py`) — one helper library emits every
  panel, so spacing, colours, and thresholds can't drift.
- **Template variable** `collection` on every scoped board (one board, not one
  per collection).
- **Dashboard links** — a "STIG dashboards" dropdown on each board carries the
  time range and variables across, so drill-down is one click.
- Consistent units, decimals, refresh, and question-shaped panel titles.

## 7. Evaluation checklist — use this to review any dashboard

- [ ] Does it state a goal, and does every panel serve it?
- [ ] General → specific top-to-bottom? Grouped into rows?
- [ ] Are colours semantic and from the palette above (no ad-hoc hexes)?
- [ ] Same thresholds as the table in §4?
- [ ] Does any status rely on colour alone (no text label)?
- [ ] Any dual-axis panel? (remove it)
- [ ] Legend on multi-series panels; description on every panel?
- [ ] Consistent units/decimals; sensible default time range?
- [ ] Linked to the other dashboards for navigation?

## References

- [Grafana — dashboard best practices](https://grafana.com/docs/grafana/latest/visualizations/dashboards/build-dashboards/best-practices/)
- [Grafana — getting started with dashboard design](https://grafana.com/blog/getting-started-with-grafana-best-practices-to-design-your-first-dashboard/)
