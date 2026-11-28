# List of figures and tables (proposal)

Status key: **now** = data exists, can be generated today; **after Goal 3** = needs the DROMIC
sample run; **after Goal 4/6** = needs automation / the new map page.
Suggested generator scripts live next to the figures (e.g. `paper/figures/make_f3.py`), so
figures are regenerated whenever the data changes. ACM two-column: single-column figures are
~3.3 in wide, so keep maps and charts simple; use `figure*` for full-width ones.

## From the original list

1. **HDX COD-AB PHL licence.** A licence is better as text than as an image: it belongs in the
   data-sources table (Table T1) and in Acknowledgments, with the CC BY-IGO 3.0 credit line
   (see `paper_writing.md` §5.2). Keep a screenshot of the HDX dataset page (version, date,
   licence) as private evidence, not in the paper.

n. **New Sakuna.PH page with centroid points** (first sample of results). Kept as **F9** below.

## Figures

| # | Figure | What it shows | Status | Source |
|---|---|---|---|---|
| F1 | Method overview diagram | PSGC backbone → COD-AB polygons → join rules → unit points → event centroid → GraphDB → map | now | drawn (draw.io/TikZ) |
| F2 | Pipeline architecture | fetch → parse → transform → map → validate → load, with the new geodata stage and centroid step highlighted | now | drawn |
| F3 | Map of all 1,756 unit points | municipality/city points coloured by join rule (code, re-code, name, parent, dissolve) on a light PH basemap | now | `logs/psgc_geo_coverage.csv` + COD-AB ADM2 outline |
| F4 | Centroid method illustration | one multipart province (e.g. Sarangani or Romblon): whole-shape centre vs largest-part centroid vs representative point | now | COD-AB ADM2 for one province |
| F5 | Maguindanao code collision | small map of the renumbered towns: where a code-only join puts each point vs the name-checked join | now | coverage CSV + ADM3 polygons for Maguindanao |
| F6 | Agreement with COD-AB centres | histogram / CDF of distance between our point and COD-AB's published centre (median 1.0 km, p95 5.5 km), outliers labelled | now | coverage CSV `km_to_cod_ab_center` |
| F7 | Event centroid example | affected municipalities of one real event (e.g. Mayon Report 111) as points sized by affected persons, the weighted and unweighted centroids, and the spread circle | after Goal 3 | `logs/dromic_event_centroids.csv` + DROMIC TTL |
| F8 | Weighting shift distribution | distance between weighted and unweighted event points across all events | after Goal 4 (needs many events) | centroid report |
| F9 | Sakuna.PH map page screenshot | new point-based page vs the existing choropleth, same events | ready: page + filters work, 58 events loaded (notes §8.19–8.20) | screenshot of `/disasters/coordinate-map` |
| F10 | Freshness timeline | DROMIC publish time vs time the event appeared in GraphDB, per report | after Goal 4 | automation logs |
| F11 | Example SPARQL + result | GeoSPARQL query (e.g. events within 50 km of a point) and its output table | after GraphDB load | GraphDB |
| F12 | Automated update chain | RSS poll → fetch → parse → supersede → map + SHACL (quarantine) → atomic graph replace → run log; show the hourly trigger and the 304 short-circuit | now | drawn |
| F13 | Accuracy by stage | bar chart per stage (metadata, rows, PSGC codes, figures) for the 10-report pilot, plus centroid distance per report | after the Geography pilot | `etl/evaluation` output |

## Tables

| # | Table | Status |
|---|---|---|
| T1 | Data sources (source, version, role, licence) | now (drafted in §5.2) |
| T2 | Join rules and units joined per rule (1,562 / 85 / 69 / 18 / 14 / 8) | now (drafted in §5.3.2) |
| T3 | Documented exceptions (region re-codes, PSA label error, rename) | now |
| T4 | Event centroid results for the 3 sample reports (locations, method, precision, spread, shift) | drafted in §5.4.5 |
| T5 | Test checks and outcomes (7 PSGC + 14 centroid + pytest suite) | now |
| T6 | Defects found and fixed (§6.6) | now |
| T7 | Tools and libraries (§6.2) | now |
| T8 | Automation run log summary: polls, updates, events quarantined, median publication-to-graph delay | after a week of scheduled runs |
| T9 | Accuracy pilot: per-stage measures for 10 reports (pipeline vs Geography reference) | after the pilot |

## Suggested priority
F1 and F2 first (they frame the paper), then F3 and F5 (the strongest results so far), then F7
once the sample run works. F4 and F6 are quick to generate and make the method defensible.
