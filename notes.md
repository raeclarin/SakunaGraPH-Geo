# SakunaGraPH Onboarding & Audit Notes

> Working notes for the SakunaGraPH-Geo thesis project. Last updated: 2026-11-16.
> Focus: (1) repo onboarding, (2) first-pass audit, (3) plan for adding **geographic coordinates** to locations.

---

## 1. Project in one paragraph

SakunaGraPH integrates Philippine disaster data from 5 heterogeneous sources (NDRRMC sitreps, GDA, EM-DAT, PSGC, DROMIC) into a single RDF knowledge graph. Raw files are fetched/parsed (PDF/XLSX → text), transformed into typed Python dataclasses, then mapped to RDF via rdflib, aligned across sources (entity resolution → `owl:sameAs`), validated with SHACL, and loaded into GraphDB. A FastAPI `api/` queries GraphDB via SPARQL; a SvelteKit `frontend/` renders an ontology graph, event tables, and a map view.

## 2. Repo map

```
ontology/       sakunagraph.ttl (OWL 2), disaster_type_scheme.ttl (SKOS), shapes/shapes.ttl (SHACL)
etl/            the pipeline (see layers below)
api/            FastAPI; routers: map, analysis, disasters, ask (LLM), sparql, ontology
frontend/       SvelteKit UI; routes: /map, /ontology, analysis pages; static geo data
logs/           execution logs, scrape state, manifests
data/           (not in git) raw/, parsed/, rdf/{events,psgc,resolution,orgs,prov}
```

### ETL layers (data flows top → bottom)

| Layer | Role | Key files |
|---|---|---|
| `etl/fetch/` | Selenium scraper for DROMIC (stateful, resumable) | `dromic.py` |
| `etl/parse/` | pdfplumber PDF → CSV for NDRRMC | `ndrrmc.py` |
| `etl/transform/` | raw/CSV/XLSX → typed dataclasses (the internal exchange format) | `ndrrmc.py`, `emdat.py`, `gda.py`, `dromic.py`, `psgc_datafile.py` |
| `etl/semantic_processing/` | NLP resolution singletons: `LOCATION_MATCHER`, `ORG_RESOLVER`, `DISASTER_CLASSIFIER` | `location_matcher_v2.py`, `org_resolver.py`, `disaster_classifier.py` |
| `etl/mappings/` | dataclass → rdflib triples; IRI minting via UUID5 | `iris.py`, per-source `mappings/{source}.py` |
| `etl/pipeline/` | orchestration per source + cross-source alignment | `run_*.py`, `build_alignment.py`, `load_graphdb.py` |
| `etl/validate/` | pySHACL validation of output TTL against `shapes.ttl` | `validate.py` |

### Critical facts about data flow

- IRIs: `https://sakuna.ph/{source}/{uuid5}`; sub-resources `{event_iri}/{segment}/{id?}` — all deterministic (`mappings/iris.py`), so re-runs are idempotent.
- All pipeline scripts run **from `etl/`** with relative paths `../data/...`.
- `build_alignment.py` extracts event features from all source TTLs, blocks by date/type, scores pair similarity → `alignments.ttl` + `dedup_registry.json`.
- `load_graphdb.py` does atomic PUT-based loading (recent commit replaced DROP+POST).

### Ontology essentials (from `sakunagraph.ttl`)

- Core: `:DisasterEvent` (→ `:MajorEvent`, beAWARE `NaturalDisaster`), `:Incident`, `:Impact` (affected pop, casualties, housing/agri/infra damage…), `:Response` (relief, assistance…), prov provenance.
- Geography: `:Location rdfs:subClassOf geo:Feature` ← **important for the coordinates plan**. Hierarchy via `:isPartOf` (transitive): `:Barangay → :Municipality → :Province → :Region`, plus `:IslandGroup`, `:Country`. `:hasLocation` range = union of (Barangay|Municipality|Province|Region).
- External vocabularies already imported: GeoSPARQL 1.1 (`geo:`), SKOS, PROV, QUDT, beAWARE.

## 3. Running it (quickstart)

```bash
cd etl/
pip install -r ../requirements.txt

# PSGC first (location backbone)
python -m pipeline.run_psgc                       # or transform/psgc_datafile.py → psgc.ttl

# Per-source ETL → Turtle
python ./parse/ndrrmc.py                          # NDRRMC PDF → CSV
python -m pipeline.run_ndrrmc [--start 0] [--count 10]
python -m pipeline.run_gda
python -m pipeline.run_emdat
python -m pipeline.run_dromic                     # needs fetch/dromic.py scrape first

# Cross-source alignment, then load
python pipeline/build_alignment.py
python pipeline/load_graphdb.py

# SHACL validation
python -m validate.validate
```

- Backend: `api/` — FastAPI, pytest tests exist under `api/tests/` (the **only** tests in the repo; `etl/` has none).
- Frontend: `frontend/` — SvelteKit; map page loads GeoJSON from `frontend/static/data/`.
- GraphDB must be running locally (see `pipeline/load_graphdb.py` for endpoint config).

## 4. First-pass audit findings

### Strengths worth keeping
- Deterministic UUID5 IRIs → idempotent re-runs.
- SHACL validation layer (`etl/validate/`) + shapes file — good hygiene; extend rather than bypass.
- Typed dataclass boundary between transform and mappings keeps sources comparable.
- Recent commits show active hygiene work (atomic loading, output dir fixes).

### Issues / risks spotted
1. **`:latitude` / `:longitude` have no `rdfs:domain`** (`ontology/sakunagraph.ttl` ~L1034–1045). Only `xsd:decimal` range. Nothing stops them being attached to anything (e.g. an event).
2. **GDA silently drops coordinates**: `transform/gda.py` parses `Latitude`/`Longitude` columns into the dataclass, but `mappings/gda.py` never emits them (EM-DAT's mapping does, at ~L191). Either map them or delete the dead columns.
3. **GeoSPARQL is imported but unused**: only `geo:Feature` is declared; no `geo:hasGeometry`, `geo:asWKT`, `geo:Geometry` anywhere. The "spatial" claim of the graph is currently text-only — exactly the gap you want to fill.
4. **Duplicate matcher**: `location_matcher_single.py` vs `location_matcher_v2.py` — v2 appears to be the live one; candidate for deletion (verify no importers first).
5. **Hardcoded region map** in `location_matcher_v2.py` (region name → PSGC code dictionary) — brittle when PSA renames/splits regions (e.g., Negros Island Region 1800000000). Prefer deriving from `psgc.ttl` labels/altLabels.
6. **No tests in `etl/`**; behavior is verified only via SHACL + eyeballing. At minimum, add unit tests for `mappings/iris.py` determinism and matcher edge cases.
7. `mappings/emdat.py` generic `else` branch emits untyped `Literal(value)` for unknown fields — check no numeric fields fall through there.
8. DROMIC/NDRRMC parsing is PDF/table fragile by nature; `parse/check_fails.py` exists to triage failures — keep using it after any parser change.

*(This is a first pass — deeper review of `build_alignment.py` scoring and `mappings/ndrrmc.py` pending.)*

---

## 5. ⭐ Geographic coordinates: what exists and how to build it

### 5.1 Answer: is there already an X/Y field?

**Yes, three partial ones:**

| What | Where | Status |
|---|---|---|
| `:latitude`, `:longitude` (owl:DatatypeProperty, xsd:decimal) | `ontology/sakunagraph.ttl` L1034–1045 | Defined but no domain; **only EM-DAT mapping emits them** (on the event, ~`mappings/emdat.py` L191) |
| `Latitude`/`Longitude` columns in EM-DAT **and** GDA | `transform/emdat.py` L136, `transform/gda.py` L28 | Parsed into dataclasses; GDA's are **dropped at mapping time** |
| Full polygon geometry | `frontend/static/data/regions.geojson` (provinces, keyed by `adm2_psgc`/`adm1_psgc` PSGC codes!) and `frontend/ph-regions.json` (regions) | Used only for drawing the map; **never joined back into the graph** |

So the plumbing for point coordinates exists at the event level, but **locations themselves are text-only** — the map currently can't plot an event unless the API geocodes on the fly (it doesn't; the map page just filters by PSGC region).

### 5.2 Recommended approach (thesis-shaped, incremental)

**Attach geometry to PSGC individuals, not to events.** Since every event links to PSGC IRIs via `:hasLocation`, coordinates inherited from locations cover *all five sources for free* — no per-event geocoding needed.

#### Option A — Centroids from geometry you already have (fastest, offline, reproducible)
1. `frontend/static/data/regions.geojson` already carries **PSGC codes as feature properties** → join directly with `psgc.ttl` individuals (which carry `:psgcCode`). No fuzzy matching needed at province level.
2. Compute centroids with **Shapely**: `polygon.centroid` (true area-weighted centroid — *not* the bbox midpoint, which lands in water for concave/island regions like MIMAROPA or Eastern Visayas). For MultiPolygons, centroid of the **largest-area part** is usually the visually correct choice for a pin.
3. Sanity-check each centroid against the admin unit's known extent; fall back to `representative_point()` (a point guaranteed inside the polygon) if a centroid falls in the sea.
4. Emit into `psgc.ttl`:
   ```turtle
   <https://sakuna.ph/psgc/...>  a :Province ;
       :psgcCode "0129..." ;
       geo:hasGeometry [ a geo:Geometry ;
           geo:asWKT "POINT (121.0 14.6)"^^geo:wktLiteral ] ;
       :latitude 14.6 ;     # optional convenience duplicate
       :longitude 121.0 .
   ```
   ⚠️ **WKT order is lon lat** (X Y), and CRS is WGS84 / EPSG:4326 — always state it (`geo:hasSerialization` or a documented convention).

#### Option B — External boundary datasets (full coverage down to barangay)
For levels the frontend GeoJSON lacks (municipality/city, barangay), pull polygons whose attributes map to PSGC codes:
- **HDX / OCHA COD-AB (Common Operational Datasets – Admin Boundaries)** for the Philippines — admin levels to barangay, maintainer-curated, open license, usually carries pcode attributes designed to match PSGC.
- **GADM** (levels 0–3 for PH: country → province → city/municipality) — joins by name, not PSGC code, so reuse `LOCATION_MATCHER` fuzzy logic for the join.
- **geoBoundaries** — similar coverage, permissive licenses.
- Government sources (NAMRIA, PSA, PHIVOLCS) exist but are less accessible programmatically; cite them for provenance if used.

Then join → centroid (or keep full polygons) → same Turtle emission as Option A.

#### Option C — Geocoding APIs (fill gaps, not bulk)
- **Nominatim** (OSM) — free, needs proper User-Agent, 1 req/s policy; fine for a few hundred unmatched names, not for 42k barangays.
- Commercial (Google, Mapbox) — accurate but cost/licensing make bulk geocoding of the whole PSGC a poor fit, and reproducibility suffers for a thesis.

**Recommendation: A + B** (deterministic, offline, citable), with C only for stragglers. Record provenance per coordinate (which dataset, which version) using the PROV vocabulary already in the ontology — good thesis material.

#### Modeling refinement (fixes audit issue #1)
- Prefer **GeoSPARQL style** (`geo:hasGeometry` → `geo:Geometry` → `geo:asWKT`) as the canonical form; it keeps a clean spot for future polygon geometries and unlocks GraphDB's geospatial functions (`geof:distance`, `geof:within`, `sfIntersects`) in SPARQL.
- Keep `:latitude`/`:longitude` as convenience duplicates **but add `rdfs:domain`** (e.g. `:Location`) — or deprecate them; decide and document one canonical form.
- Extend `ontology/shapes/shapes.ttl` with constraints: lat ∈ [-90, 90], lon ∈ [117, 127] (Philippines bounding box) as a cheap QA net.

#### New pipeline piece
- Suggested home: `etl/transform/psgc_geodata.py` + `etl/pipeline/run_psgc_geo.py` (or fold into `psgc_datafile.py` as an optional `--geo` step) writing to `data/rdf/psgc/psgc_geo.ttl` so it loads alongside `psgc.ttl`.
- Validation path already exists: add the geo shapes, rerun `etl/validate/validate.py`.
- API: `api/src/services/map/events.py` can then return coordinates in `MapEvent` with one extra SPARQL pattern; the frontend pin-drop becomes trivial.

### 5.3 Thesis evaluation ideas (measure the improvement)
- Location resolution accuracy before/after (since GeoJSON join is PSGC-code-based, it's ~100% at province level — the interesting metric is municipality/barangay coverage via COD-AB join).
- Spatial query capability demo: nearest-events, events-within-radius, chluster analysis of incident density per province — impossible today, trivial after geometry.
- Consistency check: coordinates of *resolved* location IRIs vs. source-stated coordinates (EM-DAT/GDA lat-lng) → disagreement rate is a nice data-quality metric that also surfaces alignment errors.

## 6. Open questions / TODO

- [ ] Verify no importers of `location_matcher_single.py`, then remove.
- [ ] Decide canonical coordinate form (`geo:asWKT` vs `:latitude/:longitude`).
- [ ] Check which GraphDB edition is in use and its GeoSPARQL function support.
- [ ] Find municipality/barangay boundary dataset with PSGC-compatible attributes (try HDX COD-AB first).
- [ ] Deeper audit: `build_alignment.py` scoring thresholds; NDRRMC mapping coverage.
- [ ] `data/` is not in git — document how a fresh contributor regenerates everything from raw.

---

# 7. DROMIC deep dive (thesis core: automation + coordinates)

Scope narrowed with advisors to: **(1) automate DROMIC collection → KG updates, (2) attach GPS coordinates.**
Accuracy-vs-manual evaluation comes later. EM-DAT is demoted to a secondary/backfill source
(infrequent updates, international coverage).

## 7.1 How DROMIC flows through the repo today

```
WordPress site dromic.dswd.gov.ph
  └─ Selenium scraper        etl/fetch/dromic.py            → data/raw/dromic-new/{year}/  (+ manifest.json)
  └─ DOCX→PDF converter      etl/parse/dromic-docx-to-pdf.py → data/raw/dromic-new/{year}-pdf/
  └─ Docling parser          etl/parse/dromic.py            → data/parsed/dromic/{year}/{event}/
        metadata.json (DromicEvent), source.json (provenance), CSVs:
        *affected*, *displaced|served*, *house*, *assistance*
  └─ Transform               etl/transform/dromic.py        (loads metadata + CSVs, extracts barangay,
                                                              resolves locations via LOCATION_MATCHER v2,
                                                              fixes Docling numeric artifacts)
  └─ Map + orchestrate       etl/pipeline/run_dromic.py     → data/rdf/events/dromic/dromic-{year}.ttl
        (batched, per-batch SHACL validation, `_needs_rerun.txt` triage)
  └─ Load                    etl/pipeline/load_graphdb.py   (atomic PUT — incremental-friendly)
```

Notable strengths already in place for the thesis:
- The scraper is **stateful + idempotent** (manifest with `last_scrape_date`, `post_url`s) — resumable.
- The parser already reconstructs a **region/province/municipality hierarchy** per table row
  (font-based level classification + forward-fill, `parse/dromic.py`) and extracts **barangays** in
  `transform/dromic.py`. These 3-level columns are exactly what you need to join against PSGC
  for coordinates — the pipeline is closer to geo-ready than expected.
- `run_dromic.py` has batch SHACL validation and a rerun-queue file — good failure hygiene.
- Loading is atomic PUT — the graph can be updated incrementally without DROP+reload.

## 7.2 Root cause of "data couldn't be updated" (verified)

`fetch/dromic.py` treats each DROMIC post as scrape-once:
1. `extract_first_download_link()` downloads only the **first** PDF in a post.
2. `scraped_urls` (all previously seen `post_url`s) makes the scraper **skip any post it has
   already visited — forever** (`handle_page`, L284–290).

But DROMIC posts are **living documents**: e.g. the Mayon post now holds **110+ report PDFs**
(Report #1 … #110, one per 6AM/6PM cycle), the El Niño post 41+. WordPress sorts the listing by
published date, so posts edited with new reports sink down the list and never get revisited once
`last_scrape_date` passes them. Net effect: each event freezes at the snapshot captured on
first scrape — which is why the KG/frontend data "couldn't be updated yet".

**Fix the key, not just the schedule:** track downloaded files per *file URL*, not per *post URL*.

## 7.3 ⭐ Automation: RSS replaces Selenium entirely (verified live)

The site is plain WordPress and the **category RSS feed is open** (tested 2026-11-15):

```
https://dromic.dswd.gov.ph/category/situation-reports/{year}/feed/
```

Each feed item carries *everything the Selenium scraper clicks through pages to find*:

| Field | RSS content | Use |
|---|---|---|
| `title` | e.g. "Fire Incident in Brgy. Matampay, Kolambugan, Lanao del Norte [09 Sep 2026][Terminated]" | event name + incident date + **lifecycle status** |
| `pubDate` | publication/last-update datetime (UTC; 22:00Z = 6AM PHT next day) | freshness gate — replaces `last_scrape_date` paging |
| `category` | "Fire / Urban Fire / Conflagration", "Seismic Activity", "Volcanic Eruption", "El Niño", year | **free disaster-type classification** — could bypass the LLM classifier for DROMIC |
| `content:encoded` | full post HTML incl. **every** `wp-block-file` PDF link (direct `wp-content/uploads/...` URLs, newest first) | all report versions, not just the first |
| `link` / `guid` | permalink / post ID | provenance (`prov:wasDerivedFrom`) |

Consequences:
- A `requests` + `feedparser` fetcher (~100 lines, no Chrome/Chromedriver) can poll the feed
  hourly — the feed itself declares `sy:updatePeriod: hourly`.
- Per-file idempotency: manifest keyed by the PDF's `download_url` (which contains the report
  number and as-of date: `...Report-110-on-the-Mayon...-as-of-15-November-2026-6AM.pdf`), so new
  report snapshots in old posts get picked up — the exact gap in §7.2.
- Google-Viewer URLs also appear (the `ead-preview` iframe); keep the existing
  `make_direct_download_link()` fallback for those, but prefer the direct `wp-block-file` hrefs.
- Risk: `wp-json` REST API returns 401, so rely on RSS, not the REST API. If RSS ever breaks,
  fall back to the current Selenium scraper — keep it, don't delete.

Suggested new module: `etl/fetch/dromic_rss.py` with the same manifest format (extend entries with
`report_number`, `report_as_of`, `categories[]` so the parser can use the free classification).

## 7.4 Scheduling the daily run — options, in order of fit

1. **GitHub Actions cron on this repo** (recommended start):
   - `schedule: cron: "0 22 * * *"` (22:00 UTC = 6AM PHT, just after DROMIC's morning cycle; add a
     second trigger at 10:00 UTC for the 6PM cycle).
   - Caches `data/raw`, `data/parsed`, `data/rdf` between runs via `actions/cache` (data isn't in
     git), or commits outputs to a private data branch / uploads artifacts.
   - Then push `dromic-{year}.ttl` + `psgc.ttl` to GraphDB — but GraphDB must be reachable from
     Actions (a cloud GraphDB/freehost or self-hosted runner). If GraphDB is on a lab machine,
     use a **self-hosted runner** on that machine.
2. **Windows Task Scheduler** on the existing dev box (simplest, zero infra):
   - `python -m fetch.dromic_rss` daily → parse only new files → `run_dromic` →
     `build_alignment --incremental` → `load_graphdb`. Wrap as one script
     (`etl/pipeline/update_dromic.py`) with a single log; alert on non-zero exit.
3. **Airflow** — template already exists (`etl/fetch/airflow_fetch_sources_template.py`,
   currently `@weekly` and only wired to `fetch.dromic`). Good later if the university runs an
   Airflow instance; overkill for one daily job today. If used, extend the DAG beyond fetch:
   fetch → parse → transform/map → align → load, each a task, so retries are per-stage.

Whichever scheduler: make the **whole chain incremental** (only new files parsed; alignment has
`--incremental`; GraphDB load is PUT) so the daily job takes minutes, not hours (Docling+torch is
the heavy stage).

## 7.5 GPS coordinates for the DROMIC path (concrete plan)

DROMIC is actually the *best* source to geolocate first, because its tables already carry the
three-level hierarchy. Per pipeline stage:

1. **Join, don't geocode:** every parsed DROMIC row has `region | province | municipality`
   (parser output) — resolve each to PSGC IRIs with `LOCATION_MATCHER` (existing). Barangays are
   extracted in transform. So coordinates are attached to **PSGC location IRIs**, and every
   DROMIC impact row inherits them through `:hasLocation`.
2. **Centroid sources by level:**
   - Province: already solved — `frontend/static/data/regions.geojson` carries PSGC codes
     (`adm2_psgc`); Shapely centroid (largest part for MultiPolygon; `representative_point()`
     fallback if it lands in water).
   - Municipality/City & Barangay: need an external boundary set. Best bets: OCHA/HDX **COD-AB**
     Philippines (pcodes designed to match PSGC; levels to barangay), geoBoundaries, GADM
     (name-join via the fuzzy matcher). Cache the derived centroid table (`psgc_geo.ttl`) so the
     daily pipeline never recomputes it.
3. **Modeling:** emit `geo:hasGeometry → geo:Geometry → geo:asWKT "POINT(lon lat)"^^geo:wktLiteral`
   (+ `:latitude`/`:longitude` duplicates with a proper `rdfs:domain`) into
   `data/rdf/psgc/psgc_geo.ttl`, loaded with `psgc.ttl`. Add SHACL range checks
   (lat −90…90, lon 117…127) to `ontology/shapes/shapes.ttl`.
4. **Future accuracy work (not now):** population-weighted centroids from the PSGC 2024
   population already in `psgc.ttl`; event-level point from affected-barangay set.

Milestone ordering for the thesis: (a) RSS fetcher + daily chain → (b) province centroids (quick
win, ~100 units) → (c) municipality/barangay centroids via COD-AB → (d) evaluation vs manual parsing.

---

# 8. Prerequisite audit before implementation (2026-11-16)

Checks run against the actual environment before drafting the centroid work.

## 8.1 "Do GPS fields exist / are they enabled?" — resolved

- **DROMIC reports do not publish coordinates** — lat/long are *derived* by us, never fetched.
  So there is nothing to "enable" at the source; the work is entirely on our side.
- **Ontology is ready**: `:latitude`/`:longitude` (xsd:decimal, no domain — to fix) exist;
  `:Location rdfs:subClassOf geo:Feature`; GeoSPARQL imported. PSGC individuals may carry
  geometry with zero restructuring.
- **SHACL will not block us**: both `ontology/shapes/shapes.ttl` and
  `ontology/shapes/psgc/shapes.ttl` are open (no `sh:closed`; property shapes only). Adding
  `geo:hasGeometry`/`geo:asWKT`/lat/long to PSGC individuals passes validation as-is. We still
  *add* shapes (lat/lon range) as a QA net, not because validation would fail without them.
- **GraphDB**: standard GeoSPARQL function support assumed; runtime check pending once data loads.

## 8.2 Environment check

| Item | Status |
|---|---|
| Python 3.14 | ✅ |
| shapely, geopandas, pyproj | ✅ installed |
| rdflib, pyshacl, polars, thefuzz/rapidfuzz, sentence_transformers, requests | ✅ |
| feedparser | ❌ missing — only needed for the RSS automation step (step 4), not centroids |
| `etl/requirements.txt` | exists; consider adding `shapely`, `feedparser` when used |

## 8.3 Data check — the one real blocker

- **`data/` is entirely absent on this machine** (not in git). Everything must be regenerated.
- For centroids we need `psgc.ttl`, which requires the **PSGC Excel publication datafile** in
  `data/raw/psgc/`. The pipeline picks the latest `*.xlsx|xlsm|xls` there (`run_psgc.py`).
- PSA website blocks bots (403 tested). ⇒ **Action: obtain the workbook from the previous
  maintainers or a manual browser download** (PSA → Classification → PSGC → publication datafile).
- Then: `cd etl && python -m pipeline.run_psgc --barangay` (barangay rows included via flag;
  default excludes them).

## 8.4 Province polygons — verified in-repo

`frontend/static/data/regions.geojson`: **88 features**, all 18 region codes present
(`0100000000`… `1900000000`, incl. NIR `1800000000` and BARMM `1900000000`), mixed
Polygon (40) / MultiPolygon (48). NCR shows 4 features (city/district-level, not provinces) —
expect a few join special cases. ⚠️ Open item: verify `adm2_psgc` ↔ `:psgcCode` join coverage
after `psgc.ttl` is built (risks: NIR province re-codes, city/province conflation).

## 8.5 Agreed implementation sequence

1. Obtain PSGC workbook → build `psgc.ttl` (user action + one command).
2. `etl/transform/psgc_geodata.py`: geojson → Shapely centroids (largest part for MultiPolygon,
   `representative_point()` fallback) → join by PSGC code → emit `data/rdf/psgc/psgc_geo.ttl`
   with `geo:hasGeometry`/`geo:asWKT "POINT(lon lat)"` + `:latitude`/`:longitude` + provenance.
3. Ontology/SHACL amendments: add domain to `:latitude`/`:longitude` (or adopt geo-only),
   add coordinate range shapes (lat −90…90, lon 117…127).
4. Sample validation: eyeball ~10 provinces on a map; count unmatched joins.
5. Automation step (RSS fetcher) — needs `pip install feedparser`.
6. Frontend point rendering on the map page.

## 8.6 Results — province centroids implemented (2026-11-16)

PSGC workbook obtained (3Q-2026), `psgc.ttl` built (1,756 individuals; SHACL passed), and the
centroid pipeline is implemented and verified:

- `etl/transform/psgc_geodata.py` — centroid computation, code/name join, RDF emission
- `etl/pipeline/run_psgc_geo.py` — staged → SHACL → promote orchestration (mirrors `run_psgc`)
- Output: `data/rdf/psgc/psgc_geo.ttl` — 84 individuals with `geo:hasGeometry` /
  `geo:asWKT "POINT(lon lat)"` + `:latitude`/`:longitude`, loaded *alongside* `psgc.ttl`

Join coverage: **84 of 88 GeoJSON features joined** (code → alias → name fallbacks). The 4
skipped are NCR "district (Not a Province)" features — judicial districts, not PSGC admin
units; correctly unmatched.

Bugs caught and fixed along the way (good evaluation material):
1. **9-digit codes** in the boundary GeoJSON (missing leading zero) — fixed by zero-padding.
2. **Isabela mis-join** (silent!): "City of Isabela" (Basilan) name-matched the Province of
   Isabela (Cagayan Valley) — caught by sanity-checking latitudes; fixed with region-prefix
   disambiguation (feature code prefix must match candidate's code prefix).
3. **Siquijor ambiguity**: province vs its same-named capital municipality — fixed with
   administrative-level priority.
4. **Code drift**: Cotabato City numbered 1909900000 in the boundary file vs 1908703000 in
   PSGC 3Q-2026 — handled via a documented `ALIAS_CODES` map.

Sample verification (manual spot check): Benguet 16.54/120.70 ✓, Cebu 10.31/123.72 ✓,
Palawan 9.73/118.56 ✓, Southern Leyte 10.30/125.16 ✓, Isabela province 16.99 ✓ vs Isabela
city 6.64 ✓ (the previously mis-joined pair).

Still pending before load: add coordinate-range SHACL shapes; give `:latitude`/`:longitude`
an `rdfs:domain` (or adopt geo-only as canonical); GraphDB GeoSPARQL runtime check.

## 8.7 Step 2.5 — structure amendments implemented and proven (2026-11-17)

Ontology (`ontology/sakunagraph.ttl`):
- Declared GeoSPARQL terms following the file's existing copy-import pattern:
  `geo:hasGeometry` (object property, domain `geo:Feature`, range `geo:Geometry`),
  `geo:asWKT` (datatype property, range `geo:wktLiteral`), `geo:Geometry` class.
- `:latitude`/`:longitude` now carry `rdfs:domain` as a union
  `( :Location :DisasterEvent )` — covering both PSGC individuals and EM-DAT's event-level
  coordinates; safe because `:Incident`/`:MajorEvent` subclass `:DisasterEvent` and the
  `:Impact` disjointness doesn't involve either class. Comment marks `geo:asWKT` as canonical.

PSGC SHACL shapes (`ontology/shapes/psgc/shapes.ttl`):
- `LatitudeShape` / `LongitudeShape`: `xsd:decimal`, maxCount 1, Philippine bounding box
  lat 4.0–21.5 / lon 111.0–128.0 (deliberately loose to tolerate claimed island territories
  such as Kalayaan; tight enough that a lon/lat swap always violates).
- `HasGeometryShape`: ≤1 `geo:hasGeometry`, `sh:class geo:Geometry`.
- `GeometryNodeShape` (`sh:targetObjectsOf geo:hasGeometry`): `geo:asWKT` exactly 1,
  datatype `geo:wktLiteral`, `POINT (lon lat)` regex pattern.

Main shapes (`ontology/shapes/shapes.ttl`):
- `DisasterEventLatitudePropertyShape` / `...LongitudePropertyShape` (global bounds),
  hooked into `sgsh:DisasterEventShape` — guards EM-DAT's existing event-level coordinates.

Verification — `etl/validate/check_psgc_geo.py` (kept as a regression check):
1. ✅ Combined graph (psgc.ttl + psgc_geo.ttl + ontology) conforms to PSGC shapes.
2. ✅ Deliberate lat/lon swap is **detected** as a violation.
3. ✅ Malformed WKT (`POLYGON` where `POINT` required) is **detected** as a violation.

Remaining before Step 3: load `psgc.ttl` + `psgc_geo.ttl` into GraphDB (runtime GeoSPARQL
function check), then DROMIC sampling.
