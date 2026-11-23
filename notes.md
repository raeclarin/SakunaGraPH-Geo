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

## 8.8 Goal 3 Step 1 — municipality centroids from HDX COD-AB (2026-11-19)

Decisions: boundary source **HDX COD-AB Philippines**; precision **municipality/city points +
event centroid** (barangay precision = future work / 2nd semester).

**Source:** COD-AB `v03`, boundaries `valid_on` 2025-02-13, HDX release May 2026, EPSG:4326.
Stored in `data/raw/boundaries/cod-ab/{shp,geojson,gdb,xlsx}` (not in git; GeoJSONs are
0.5–0.7 GB each, so the pipeline reads the shapefiles). Licence: **CC BY-IGO 3.0** (see §8.9).
Pcodes are `PH` + PSGC digits without trailing zeros (`PH0102801` → `0102801000`).

**Run:** `cd etl && python -m pipeline.run_psgc_geo` (default `--source cod-ab`;
`--source geojson` reproduces the old province-only output). Reports go to
`logs/psgc_geo_coverage.csv` and `logs/psgc_geo_unjoined_source.csv`.

**Result: 1,738 / 1,738 PSGC Prov/City/Mun/SubMun units have a point; SHACL passes.**
*(Updated 2026-11-21: regions added, now 1,756 / 1,756; see §8.9.)*

| Join rule | Units | What it covers |
|---|---|---|
| `code` (code + name agree) | 1,562 | the normal case |
| `recode` | 85 | Negros Island Region (`0604/0704/0706 → 1804/1806`), Sulu (`1906 → 0906`) |
| `name` (unique within region) | 69 | HUCs that COD-AB files under their province (Angeles, Cebu, Davao, NCR cities…), renumbered Maguindanao towns, Lucena |
| `parent` | 14 | Manila sub-municipalities (Tondo…) inherit City of Manila (precision = City) |
| `bgy-dissolve` | 8 | new BARMM SGA towns (Kapalawan, Ligawasan…): union of their barangay polygons (2 towns at 6/7 and 11/12 barangays) |

Findings worth writing up:
1. **Silent mis-join prevented:** Maguindanao del Norte/Sur were renumbered after the COD-AB
   vintage, so **16 COD-AB codes now belong to a different PSGC town** (COD-AB `1908822` =
   Pagagawan, PSGC `1908822000` = South Upi). A code-only join would misplace them with no
   error. Fix: a code join also requires name agreement (rapidfuzz ≥ 85); disagreements fall
   through to name matching. Regression test 7 guards it.
2. **PSA data error:** PSGC 3Q-2026 labels Lucena as `"City of Lucenaa/"` (typo); handled by
   a documented `NAME_ALIASES` entry.
3. **Rename:** COD-AB "San Isidro" = PSGC "Sawata" (Davao del Norte, same code, no other San
   Isidro in the province) — accepted via `CONFIRMED_RENAMES`.
4. **Centroid method:** largest-part centroid in EPSG:32651 (UTM 51N), `representative_point()`
   fallback. Against COD-AB's own `center_lat/lon`: median 1.0 km, 95th pct 5.5 km. Large gaps
   are multipart units where COD-AB centres the *whole* shape: Palawan 313 km (pulled toward
   the Kalayaan Islands), Sarangani 76 km (the bay between its two halves), Aparri 66 km (Fuga
   islands), Romblon 60 km. Largest-part is the right choice for a map pin; note the trade-off
   for split provinces like Sarangani, where the pin represents one half.
5. Each geometry node now carries `:geoPrecision` (new ontology property, SHACL `sh:in` list)
   and `prov:wasDerivedFrom <https://sakuna.ph/dataset/cod-ab-phl-v03>` (a `prov:Entity`).
   Gotcha: emit `:geoPrecision` as a *plain* literal — pySHACL treats `"x"^^xsd:string` as not
   equal to the plain `"x"` in `sh:in`.

Unjoined COD-AB features (14, all expected): 4 NCR districts + "City of Isabela (not a province)"
+ the SGA unit in ADM2, and the 8 superseded SGA units in ADM3 (their barangays feed the dissolve).


## 8.9 Licence, region points, and a latent decimal bug (2026-11-21)

### Licence: COD-AB is CC BY-IGO 3.0 — what it requires
The HDX COD-AB Philippines dataset is licensed **Creative Commons Attribution for
Intergovernmental Organisations (CC BY-IGO 3.0)**,
<https://creativecommons.org/licenses/by/3.0/igo/>. There is nothing further to download:
the licence is the legal text at that URL. Obligations for us:
1. **Attribute** in the paper, in any published dataset (`psgc_geo.ttl`), and on the map page
   (Goal 6). Suggested credit line: *"Administrative boundaries: OCHA / HDX Common Operational
   Dataset – Administrative Boundaries, Philippines (v03, valid 2025-02-13; sources PSA and
   NAMRIA), CC BY-IGO 3.0. Centroids derived by the authors."*
2. **Indicate changes**: we do not redistribute the polygons; we derive points from them. Say so
   (the "Centroids derived by the authors" clause covers it).
3. **No implied endorsement** by OCHA/the UN.

Good practice: save a PDF or screenshot of the HDX dataset page (version, date, licence) in a
private thesis folder as evidence of what was used and when.

In the graph, the dataset node `<https://sakuna.ph/dataset/cod-ab-phl-v03>` now carries
`dcterms:license <https://creativecommons.org/licenses/by/3.0/igo/>`, and every geometry node
links to it via `prov:wasDerivedFrom`.

Other data we depend on, for the paper's data-sources table:
- **PSGC 3Q-2026** (PSA publication datafile): cite PSA. It's a public government statistical
  release; no separate licence document was found.
- **`frontend/static/data/regions.geojson`** (inherited province polygons): origin undocumented.
  It is no longer used for coordinates (`--source geojson` is legacy), but the frontend still
  draws the choropleth with it. Find its source before publishing the map, or switch the
  choropleth to COD-AB ADM2 as well.

### Region points: dissolved from current members, not COD-AB ADM1
COD-AB v03's ADM1 has 17 regions. It **predates the Negros Island Region** (NIR), and still
has Negros in Regions VI/VII and Sulu in BARMM. Using ADM1 directly put Region VII's point on
southern Negros (9.61, 123.03), which is now NIR. Instead, `dissolve_regions()` unions the
already-matched polygons of every province, city and municipality whose PSGC parent is the
region (82 provinces + 34 independent cities + 9 municipalities directly under a region),
simplified to ~100 m first. The result: Region VII → Cebu (10.31, 123.72), NIR → Negros
(10.03, 123.00), and every unchanged region within metres of its ADM1 point (a cross-check of
the method). **Coverage now 1,756 / 1,756** (Reg/Prov/City/Mun/SubMun); join rule
`member-dissolve` = 18.

### Latent bug: float values in `xsd:decimal` literals
`Literal(18.669, datatype=XSD.decimal)` keeps a Python `float`, which pySHACL rejects as "not
xsd:decimal" when it validates **in memory**. PSGC geo output passed only because the pipeline
re-parses the staged Turtle file before validating; DROMIC batches are validated in memory, so
they would have failed. The event mapping uses `Decimal(str(x))`. **`mappings/emdat.py:192` has
the same pattern** (event lat/lon); it is harmless until EM-DAT batches are validated in memory.

## 8.10 Goal 3 Step 2 — event centroid (2026-11-21)

**Code:** `etl/semantic_processing/event_geo.py` (calculation), `mappings/dromic.py`
`event_geometry_mapping()` (RDF), `pipeline/run_dromic.py` `_map_centroid()` (wiring + report).
**Tests:** `python -m validate.check_event_centroid` (13 checks on synthetic Albay/Mayon events;
13/13 pass). **Report:** each `run_dromic` run writes `logs/dromic_event_centroids.csv`.

### Inputs (best source first)
1. **Affected-population rows**: municipality-level rows of the DROMIC "number of affected"
   and displaced/served tables (province and region subtotal rows are already dropped by
   `transform/helpers.py`), each with `hasLocation` and counts.
2. **Event metadata location** (e.g. "Fire Incident in Brgy. X, Town, Province"); no weights.
3. **Housing-damage rows**; no weights.

### Calculation
1. **Resolve** each location IRI to a point from `psgc_geo.ttl`. A unit without its own point
   (a barangay) walks up `:isPartOf` to the nearest ancestor with one; the precision is the
   level the point actually came from.
2. **Merge** repeated IRIs (weights summed).
3. **Choose one weight field for the whole event**: the first of `affectedPersons`,
   `affectedFamilies`, `displacedPersons`, `displacedFamilies` that is > 0 for *every* location.
   Mixing fields across towns would make the weights incomparable, so if none covers all
   locations, the mean is unweighted.
4. **Spherical (geographic) mean**: convert each point to a 3-D unit vector
   (x = cos φ cos λ, y = cos φ sin λ, z = sin φ), take the weighted sum, and convert back
   (φ = atan2(z, √(x²+y²)), λ = atan2(y, x)). This is exact on the sphere and independent of
   map projection; at Philippine scales it differs from a plain lat/lon average by well under a
   metre.
5. **Precision** = the coarsest level among the inputs (one province-only row → "Province").
6. **Spread** = the largest haversine distance from the centroid to any input point (0 for one
   town); a dispersion measure the map can draw as a radius.
7. The **unweighted** mean is always computed too, and the report stores the distance between
   the two (`weighting_shift_km`) for the thesis comparison.

### RDF emitted per event
```turtle
<event> geo:hasGeometry <event/geometry> ; :latitude 13.155547 ; :longitude 123.670364 .
<event/geometry> a geo:Geometry ;
    geo:asWKT "POINT (123.670364 13.155547)"^^geo:wktLiteral ;
    :geoPrecision "Municipality" ; :centroidMethod "weighted-mean:affectedPersons" ;
    :locationCount 4 ; :spreadKm 18.669 .
```
New ontology properties `:centroidMethod`, `:locationCount`, `:spreadKm` (domain
`geo:Geometry`). New SHACL `sgsh:EventGeometryNodeShape` in `ontology/shapes/shapes.ttl`: one
WKT `POINT` inside the PH bounding box (lon 111–128, lat 4–21, so a lat/lon swap fails), the
`:geoPrecision` list, a method pattern, `locationCount ≥ 1`, `spreadKm ≥ 0`. Note:
`geo:hasGeometry` has domain `geo:Feature`, so a reasoner will infer that events are
`geo:Feature`s. That is consistent with GeoSPARQL (a feature is anything with a spatial
representation).

### Synthetic results (Albay, around Mayon Volcano)
| Case | Result |
|---|---|
| One town (Daraga) | point = Daraga centroid, `single-location`, spread 0 |
| Barangay (Alobo, Daraga) | Daraga's point, precision Municipality |
| Daraga 3,000 : Tabaco 1,000 persons | point exactly 1/4 of the way from Daraga (ratio 0.2500); 5.8 km from the unweighted midpoint |
| Daraga/Legazpi/Guinobatan/Tabaco (4,000/2,500/3,000/1,500) | 13.1555, 123.6704; spread 18.7 km; weighting moved the point 2.5 km |

### Bug fixed along the way
`transform/dromic.py` joins several matched IRIs with `"|"` into **one** `URIRef` for the
event's metadata location (e.g. `https://sakuna.ph/A|https://sakuna.ph/B`), an invalid IRI in
the graph. `event_mapping` now emits one `:hasLocation` per IRI (test 12). Also removed a stray
`from turtle import st` in `mappings/dromic.py` (it imported Python's turtle-graphics module).

### Open questions for later
- DROMIC event IRIs are `uuid5(name + startDate)`, so **every report of an event maps to the
  same IRI**. With one report per event (the current scraper) that is fine. Goal 4 fetches
  *all* reports, so it must process only the latest report per event, or each report needs its
  own snapshot node. Otherwise an event would get several centroids, which SHACL `maxCount 1`
  will catch.
- Weighting by *affected* vs *displaced* persons answers different questions (where the impact
  is vs where people moved). This is worth one sentence in the paper's method section.

## 8.11 Writing workflow (proposal)

Requirement: a ≥ 10-page paper in ACM two-column format (`acmart`, `sigconf`).
1. **notes.md stays the lab notebook**: every step, number, command, and bug lands here first.
2. **Draft into the repo, sync to Overleaf.** Add the ACM template to `paper/` (e.g.
   `paper/main.tex` + `paper/sections/{intro,related,method,results,discussion}.tex` +
   `paper/refs.bib`). After each finished step, Claude drafts the matching section from these
   notes (method and results first, since they are freshest), and you paste/sync into Overleaf.
   Overleaf's GitHub sync needs a paid plan; without it, copy the section files manually.
3. **Figures from data, as scripts**: e.g. `paper/figures/*.py` generates a coverage-by-join-rule
   table, a map of municipality centroids, and a weighted-vs-unweighted shift histogram.
   Re-running them after the data changes keeps the paper honest.
4. **References**: keep `refs.bib` growing as sources come up (GeoSPARQL 1.1, COD-AB, PSGC,
   SHACL, PROV-O, DROMIC, EM-DAT, prior SakunaGraPH work).

## 8.12 First real DROMIC sample run (2026-11-21)

Three reports parsed with Docling (`parse.dromic --single`) and mapped with
`run_dromic --validate`: **all 3 events mapped, all got a centroid, SHACL passed** (175 triples).
Copied the PDFs and manifest by hand instead of running `dromic-docx-to-pdf.py`, because that
script opens Microsoft Word through COM even when there are no DOCX files.

| Event | Locations (resolved) | Event point | Method | Spread | Weighting shift | Verdict |
|---|---|---|---|---|---|---|
| El Niño, Western Visayas (Report 1) | Dumarao, Tapaz (Capiz); Dueñas, Lambunao (Iloilo) | 11.1748, 122.4542 | weighted by affected persons | 33.0 km | 8.8 km | plausible: central Panay, pulled toward Tapaz (22,795 persons) |
| Mayon Volcano (Report 111) | 13 Albay towns, all in the table, none unresolved | 13.1699, 123.5522 | weighted by affected persons | 38.1 km | 7.4 km | plausible: ~16 km SW of the crater, between Guinobatan (102,147) and Ligao (80,926) |
| Fire, Brgy. E. Rodriguez, Quezon City (Report 4) | **City of Manila** (wrong) | 14.6006, 120.9855 | single location | 0 | 0 | **wrong town**: should be Quezon City |

### Bug A (location matcher): every "X, Metro Manila, NCR" row resolves to the City of Manila
The parsed row is correct (`NCR | Metro Manila | Quezon City`), and the event title resolves
correctly to Quezon City. In `LocationMatcher.match()` (`location_matcher_v2.py` ~L443–465),
when the top level is a region and the province slot does not match a province, the province
slot is tried as a **city first**: "Metro Manila" fuzzy-matches "City of Manila" (threshold
60), so the actual municipality ("Quezon City") is never looked at. Verified: Quezon City, Las
Piñas, Parañaque, Makati and Pateros all resolve to Manila when written with "Metro Manila"; any
of them alone, or with "NCR" only, resolves correctly. **Impact:** every NCR row in DROMIC
tables (which use "Metro Manila" in the province column) is placed in Manila. NCR fires are
among the most frequent DROMIC reports, so this matters a lot for the map.
Proposed fix: when there is a more specific level, try it first (scoped to the region); only
reinterpret the province slot as a city if that fails.

### Bug B (transform): assistance tables always fail, and the events are then skipped
`transform/dromic.py:336` writes a debug copy to the relative path `dump/assistance.csv`; the
`dump/` folder does not exist, so `load_assistance` raises for **every** event. `run_dromic`
then (1) drops the assistance data and (2) appends the folder to `_needs_rerun.txt`, so **the
next `run_dromic` run skips all three events**. The rerun entry for the fire is also truncated
to "Fire Incident in Brgy. E": `Path(folder).stem` treats the "." in "Brgy. E." as a file
extension. Proposed fix: remove the debug write, and use `.name` instead of `.stem`. Until then,
empty `_needs_rerun.txt` before re-running.

### QA idea (thesis-worthy)
The fire event had two independent location signals that disagreed: the title (Quezon City)
and the table (Manila). An automatic consistency check (the title location should equal, or
contain, the table locations) would have flagged Bug A on its own. Candidate validation rule
and evaluation metric.

## 8.13 Fixes for the sample-run bugs (2026-11-21)

Both bugs from §8.12 are fixed. No re-parse is needed (the fixes act in the transform/matching
stage, which `run_dromic` runs), but `_needs_rerun.txt` must be emptied once.

### Bug A fixed: "X, Metro Manila, NCR" → City of Manila
**File:** `etl/semantic_processing/location_matcher_v2.py`
- `match()`, branch "top level is a region, but the province slot is not a province": new order
  1. province slot as a city in the region, **exact label only** (keeps "Lahug, Cebu City, VII"
     → Cebu City, i.e. a barangay below a city);
  2. the municipality level, exact then fuzzy (fixes "Quezon City, Metro Manila, NCR");
  3. province slot as a fuzzy city match (old behaviour); else the region.
- `_fuzzy_match_municipality_in_region()`: new `exact_only` flag, and an extra exact variant
  "X City" → "city of X" (PSGC labels most cities "City of X").

**How it was verified:**
- Probe of 34 strings, old (`HEAD`) vs new: **exactly the 16 NCR cities/municipality written
  with "Metro Manila" changed**, all from City of Manila to the correct city. All other cases are
  unchanged, including the barangay-below-city ones.
- A first attempt (simply trying the municipality level first) was **rejected**: it fixed NCR but
  broke barangay-below-city rows, because the loose fuzzy threshold (60) matched barangay names
  to other towns: Lahug → City of Naga, Bagong Silang (Caloocan) → Pasig, Talomo (Davao City) →
  Tagum. Worth one sentence in the paper: matcher changes need old-vs-new regression probes.
- Built-in smoke tests (`python -m semantic_processing.location_matcher_v2`): 34/42 both before
  and after, with identical output. The 8 failures predate this work: their expectations are
  outdated (Region IV-A IRI format, island-group passthroughs). Left as is; candidates for
  cleanup.

### Bug B fixed: assistance tables always failed and events were then skipped
- `etl/transform/dromic.py` (`load_assistance`, ~L336): removed the debug write to
  `dump/assistance.csv`.
- `etl/pipeline/run_dromic.py` (`_record_assistance_failure`): `Path(folder).name` instead of
  `.stem`, so folder names with dots ("Brgy. E. Rodriguez …") are recorded whole.
- Verified: all 3 sample folders now load assistance (El Niño 1 row, fire 1 row, Mayon 21 rows),
  and the fire's assistance row resolves to Quezon City.

### Expected after re-run
Fire event point = Quezon City centroid **14.680492, 121.060467** (precision City), instead of
Manila's 14.600622, 120.985461 (12.0 km off). El Niño and Mayon points unchanged.
NCR evaluation note: Quezon City is the largest NCR city, so a city-precision point can be several
km from the affected barangay. This is the case for barangay precision as future work (the
event already stores `:hasBarangay "E. Rodriguez"`).

## 8.14 Re-run after the fixes: centroids right, SHACL failed on organisations (2026-11-21)

**Re-run result:** both §8.13 fixes worked. The fire point is now Quezon City (14.6805, 121.0605,
City), skipped 0, and there are no "Skipping assistance" warnings; El Niño and Mayon are unchanged.
But SHACL failed with 23 violations: `:contributingOrg` values (`org/DSWD`, `org/LGU`, `org/NGO`)
"do not have class prov:Organization".

**Cause (inherited, newly exposed):** organisations get their `prov:Organization` type from
`data/rdf/orgs/orgs.ttl`, which was **never built on this machine**. The validator loads context
files only if they exist and skipped it silently. Assistance rows never reached validation
before, because Bug B made them fail first; fixing Bug B exposed this gap.

**Related problems found:**
1. The documented build command `python semantic_processing/org_registry.py` (AGENTS.md,
   `etl/semantic_processing/README.md`) **fails** with `No module named 'mappings'` when the file
   is run directly. It must be run as a module, `python -m semantic_processing.org_registry`. Docs
   corrected.
2. `constants/org_registry.json` had no `NGA` (national government agencies), although the DROMIC
   assistance mapping emits `org/NGA`. The first report with an NGA column would fail SHACL. Also
   missing: `DROMIC` (cited by every DROMIC provenance node) and `CRED` (EM-DAT provenance). All
   three added (now 52 organisations). The untracked copy `api/constants/org_registry.json` was
   **not** updated, so the two copies now differ.
3. `data/rdf/prov/prov.ttl` is expected by `validate.py` and `load_graphdb.py`, but **no script in
   the repo builds it**. No shape currently depends on it. Inherited gap; left as is.

**Guard added:** `run_dromic --validate` now logs a WARNING for every missing SHACL context file
(`pipeline/run_dromic.py`, step 2), so a missing `orgs.ttl` can no longer fail silently.

**Verified before handing over:** built `orgs.ttl` into a scratch folder, mapped the 3 folders in
memory with a scratch rerun file, and validated with the same context `run_dromic` uses:
**SHACL passed, 359 data triples**, all three centroids as expected.

## 8.15 Determinism fixes, prov.ttl restored, pipeline linked to GraphDB (2026-11-22)

### Determinism fixes (needed before automation re-runs the pipeline constantly)
1. **Event precision flipped between identical runs** (Mayon: "Municipality" on one run, "City" on
   the next). City and Municipality share a rank, and `max()` returned whichever came first.
   Fix: `coarsest_precision()` in `etl/semantic_processing/event_geo.py` with a fixed tie order;
   a mix of cities and municipalities is always "Municipality". New check 10b in
   `check_event_centroid` (same inputs, reversed order, same precision). **14/14 pass.**
2. **Row IRIs depended on row order.** Affected-population rows come out of an unordered polars
   `join(how="full")`, and the row number becomes part of the IRI (`{event}/affectedpopulation/{n}`),
   so the same town could get a different IRI per run. Fix (`etl/transform/dromic.py`): sort
   the input file list, and sort rows by `hasLocation` before numbering in `load_aff_pop`,
   `load_housing` and `load_assistance` (the last two were already stable; sorted for one rule).
   **Verified:** mapped the 3 events in two separate processes; graphs isomorphic, and all 267
   triples without blank nodes byte-identical. (Monetary amounts use blank nodes, whose labels
   are random by design; this is not instability.)

### prov.ttl restored
`data/rdf/prov/prov.ttl` was missing (deleted from git in `bcaed89`, Feb 2026, see §8.14). The
existing GraphDB repository `SakunaGraPH` still had the `https://sakuna.ph/prov` graph (24
triples, a newer version than the git copy: it uses `org/CRED`, `org/UPDG`). Exported it with a
read-only `CONSTRUCT`, saved it as `data/rdf/prov/prov.ttl`, and added `:dromic_website_access`
(prov:Activity, associated with `org/DROMIC`): 28 triples. `run_dromic --validate` now runs with
no missing-context warnings.

### GraphDB environment (local laptop)
- GraphDB **Free 11.4.0** on `localhost:7200`.
- Existing repository **`SakunaGraPH`** (internship copy, untouched): ruleset `owl2-rl-optimized`,
  5.87 M statements incl. inferred; graphs events/dromic 321,711 · events/ndrrmc 1,425,247 ·
  events/emdat 31,666 · events/gda 12,975 · psgc 16,088 (older PSGC, no barangays/coordinates) ·
  ontology 2,495 · orgs 246 · prov 24 · resolution 1,937.
- Also exists: a copy on school servers (set up over SSH by an intern mate), and other interns'
  local copies.

### New repository `SakunaGraPH-dev` and first load (Goal 5 core)
- Created via the GraphDB REST API (`POST /rest/repositories`), with the config cloned from
  `SakunaGraPH` (same ruleset `owl2-rl-optimized`, same index settings), so inference behaves the
  same.
- Loaded with the existing loader, unchanged:
  `python -m pipeline.load_graphdb --repo SakunaGraPH-dev --replace --timeout 1200`
  → 7 files into 5 named graphs, each replaced in one atomic transaction, **5 min 10 s** (dominated by
  OWL 2 RL inference over the 357k-triple PSGC graph).
- Safety note: the loader's default `--repo` is `sakunagraph` (lowercase), which does not exist
  here, so a run without `--repo` fails instead of touching the main repository. **Always pass
  `--repo SakunaGraPH-dev`.**

| Named graph | Triples (explicit) |
|---|---|
| events/dromic | 359 |
| ontology | 2,648 |
| orgs | 255 |
| prov | 28 |
| psgc (psgc.ttl + psgc_geo.ttl) | 368,819 |

**SPARQL verification (all passed):**
1. All 3 events return lat/lon + precision, method, location count and spread (values as in §8.14).
2. Mayon: all 13 affected towns return with their own coordinates (event → town → point chain,
   which is what the Goal 6 map needs).
3. Inference as predicted: events are inferred `geo:Feature`, `:DisasterEvent`, `prov:Entity`.
4. PSGC points by precision: Municipality 1,493 · City 163 (149 cities + 14 Manila districts) ·
   Province 82 · Region 18 = 1,756.
5. **GeoSPARQL functions work without extra setup:** `geof:distance` gives the Mayon event point
   17 km from the crater (13.257 N, 123.685 E); El Niño 266 km; QC fire 324 km. So spatial queries
   ("events within X km") are available for the thesis.

### Deploying to the school server later (thinking, not decided)
- **No schema changes needed**: GraphDB is schema-less; the new properties are just triples. Only
  the `ontology` graph must be reloaded so they are defined.
- Safest: **a second repository on the same server** (like `SakunaGraPH-dev` here); the API's
  repository ID becomes a config switch. A second GraphDB *instance* is unnecessary.
- Replacing graphs in the live repository is possible per named graph (`--replace`), but only once
  the new pipeline has re-processed the **full** DROMIC history; replacing `events/dromic` with a few
  events would drop the rest. Replacing `psgc` with the 3Q-2026 version would also change codes that
  old events from other sources point to (NIR, Sulu, Maguindanao).


## 8.16 Goal 4 — automated DROMIC updates (2026-11-22)

### Answers recorded before building (the user's three questions)

**Q1. What runs, and how often?** One wrapper, `etl/pipeline/update_dromic.py`, performs one poll per run:

| # | Stage | Code | What it does |
|---|---|---|---|
| 1 | fetch | `fetch/dromic_rss.py` (new) | GET the year's RSS feed; download report files not yet in `manifest.json` (key = file URL) |
| 2 | stage | wrapper; `parse/dromic-docx-to-pdf.py` only for Word files | copy new PDFs to `raw/dromic-new/{year}-pdf/`, refresh the manifest copy there (the parser reads provenance from it) |
| 3 | parse | `parse/dromic.py` `process_file()` | parse **only the new** PDFs; append them to `_parsed.txt` |
| 4 | supersede | wrapper `supersede_old_reports()` | keep the newest report folder per event (see below) |
| 5 | map + SHACL | `run_dromic.run(validate, quarantine=True)` | rebuild `dromic-{year}.ttl`; failing events are quarantined, not fatal |
| 6 | load | `load_graphdb --replace --file dromic-*.ttl` | atomic replace of graph `events/dromic` only (all years' files, so other years stay) |
| 7 | record | wrapper | `logs/update_dromic.log` and one row in `logs/update_runs.csv` |

Three intermediate steps were not obvious:
- The DOCX→PDF conversion. It uses Word COM, so it runs on Windows only. All current feed items are PDFs, so it rarely runs.
- The manifest copy into `{year}-pdf/`. The old copy there went stale because `copy_pdfs_jsons` never overwrites.
- The supersede step.

Time quantum:
- RSS is **pull**: DROMIC cannot notify us, and there is no push or WebSub.
- We poll **hourly**, as the feed itself declares (`sy:updatePeriod hourly`).
- An unchanged poll is a conditional GET (ETag / If-Modified-Since → HTTP 304) and stops there. Stages 2–6 run only when a file was downloaded.
- DROMIC publishes on 6AM/6PM cycles, so new data reaches GraphDB at most about 1 h after posting.
- `update_runs.csv` records `latency_min`: the load's finish time minus the feed pubDate of the newest new report. This is the latency figure for the thesis.

**Q2. Does the dev repository need the whole catalogue re-parsed?** No.
- Put the seniors' TTL under `data/rdf/events/dromic-legacy/`. The loader makes each folder its own named graph (`https://sakuna.ph/events/dromic-legacy`) with no code change.
- The automated chain only ever replaces `events/dromic`, so legacy data is never overwritten.
- If the seniors used the same `_event_id` scheme, the same event name and start date give the same IRI. One SPARQL query can then list the overlaps, which helps the manual-versus-automated comparison.
- A full re-parse is optional. RSS `?paged=N` and the Selenium scraper remain available for history.

**Q3. Testing and accuracy.** Two layers.
- *A. Engineering tests* (pytest, `etl/tests/`, to do):
  - Wrap `validate/check_event_centroid.py`, `check_psgc_geo.py` (slow) and the matcher smoke tests (fix 8 stale expectations).
  - Add new tests: RSS parsing on a saved feed, supersede, quarantine, determinism, and a load round-trip on the dev repository.
- *B. Accuracy versus manual parsing* (design now; the full study is possibly 2nd semester):
  - Gold standard: a stratified set of about 30–50 reports, hand-annotated (ideally by the Geography partners) with the PSGC code of each row and the key figures.
  - Metrics, one per stage so errors are attributable:
    1. location resolution: precision and recall at PSGC-code level, by admin level;
    2. figures: exact-match rate and median relative error per field;
    3. centroid error: haversine distance between the pipeline centroid and the centroid recomputed from the gold locations and figures. This isolates parsing and matching errors from the centroid method.
  - Score the seniors' hand-parsed data against the same gold set where events overlap.

### What was built

**`etl/fetch/dromic_rss.py`** (no new dependency: `requests` plus the stdlib `xml.etree`)
- **Feed:** `https://dromic.dswd.gov.ph/category/situation-reports/{year}/feed/`, 10 posts per page, newest first.
  - Each post's `content:encoded` links every report file, **newest first**.
  - Each file is linked twice (file block plus download button), so links are de-duplicated in order.
- **Default:** only the newest file per post. `--all-reports` fetches every version.
- **Paging:** if every post on page 1 is newer than the last poll, it also reads `?paged=2…` (up to `--max-pages`). A burst of more than 10 posts between polls is therefore not missed.
- **State:**
  - The shared `manifest.json`, with the same entry shape as the Selenium scraper (`page: 0` means RSS).
  - A separate `rss_state.json` holding the ETag, Last-Modified, newest pubDate, and per-file title, guid, categories, report number, terminal flag and as-of date.
  - They are separate because `fetch/dromic.py` builds `ManifestEntry(**entry)` and would crash on extra keys.
- **Download safety:**
  - Each file is written to a temp file, then atomically renamed.
  - A PDF must start with `%PDF` and a DOCX with `PK`.
  - The manifest is saved after every file.
  - The ETag only advances when all downloads succeeded, so failures retry.
- **Title-vs-file guard** (for the Goal 3 mix-up where the Tandag post yielded the Butuan PDF): at least one place word of the post title must appear in the file name. Otherwise it logs a warning and sets `title_check: mismatch` in the state. Tested: the Tandag title against a Butuan file name gives a mismatch.
- **Live dry run** (22 Nov 2026, 23:02 PHT): 10 posts and 10 new files. Report #1, unnumbered reports and **Terminal Reports** (`DSWD-DROMIC-Terminal-Report-on-…`, no number) were all recognised, and every title check passed.
- `make_direct_download_link` and `sanitize_filename` are copied from `fetch/dromic.py`, because importing that module pulls in Selenium.

**Supersede rule** (`update_dromic.supersede_old_reports`)
- **Problem:** event IRIs are minted from event name plus start date (`transform/dromic.py:_event_id`), so Report #111 and Report #112 of Mayon get the **same IRI**. Parsed folders, however, are named with the report date (`Mayon Volcano Seismic Activities 20261117`). If both folders stayed, `run_dromic` would attach old and new figures to one event.
- **Rule:**
  - Group folders by (eventName, startDate) from `metadata.json`.
  - Keep the newest: by folder date suffix, then reportNumber, then mtime.
  - Move the rest to `parsed/dromic/{year}/_superseded/`. They are kept for provenance and a future time series. `run_dromic` ignores folders starting with `_` because they hold no `metadata.json`.
- **Test** on a scratch copy with three Mayon folders (…15, …17, …21): it kept …21 and archived the other two. A second run moved nothing.

**Quarantine mode** (`run_dromic.run(..., quarantine=True)`, CLI `--quarantine`)
- **Problem:** one SHACL failure used to abort the whole year file. In automation, one bad report would then block every later update.
- **Behaviour now:**
  - Each event is mapped into its own graph, so a mapping exception drops only that event.
  - The batch is validated as before. If it fails, its events are validated one by one, and only the failing ones are skipped and appended to `_needs_rerun.txt`, which `run_dromic` already skips.
  - `run()` now returns a summary dict: processed, skipped, quarantined, triples and out_file.
  - The default CLI behaviour is unchanged.
- **Test** on scratch copies: the 3 real events, plus one folder with an invalid `metadata.json` and one copy with a lat/lon-swapped point.
  - The 2 bad ones were quarantined (a mapping error and a SHACL violation).
  - The 3 good ones were kept, with the same **359 triples** as the normal run.

**`etl/pipeline/update_dromic.py`** (the wrapper)
- `--repo` is **required**, with no default, so it can never hit the seniors' repository by accident.
- It changes into `etl/` itself, so Task Scheduler's working directory doesn't matter.
- A lock file, `logs/update_dromic.lock` (stale after 6 h), prevents overlapping runs.
- It never loads after a failed stage 5.
- **Retry after failure:** if a run fails after fetching (e.g. GraphDB was down), its files are already in the manifest. The **next** run sees the failed row in `update_runs.csv` and retries stages 4–6 even without new files.
- **Options:** `--dry-run`, `--rebuild` (skip fetch), `--no-load`, `--year`, `--batch-size` (25), `--timeout` (600 s).
- **Provenance needed no change:** `prov_mapping` already links each event to its report Source with the post URL, download URL and `obtainedDate`. The RSS manifest writes valid ISO timestamps; the Selenium scraper wrote `…+00:00Z`, which is not a valid `xsd:dateTime`.

**`scripts/schedule_dromic.ps1`**
- Registers the task `SakunaGraPH-DROMIC-Update`, which runs every 60 min as the current user while logged on.
- Settings: `StartWhenAvailable` (missed runs start on wake), `IgnoreNew` for overlaps, and a 2 h time limit.
- `-Unregister` removes the task.
- GraphDB must be running for stage 6. If it isn't, the run is recorded as failed and retried the next hour.

**Known limits (for the paper)**
- Stage 5 rebuilds the whole year file each time. This is simple and always consistent, but the cost grows with the number of events; per-event TTL files would be the optimisation.
- The wrapper handles one year per run. Around New Year, also run it with `--year` for the previous year.
- Event identity depends on the parsed event name being identical across all reports of one event.


## 8.17 Test suite, accuracy harness, Geography guide (2026-11-23)

### Test suite (`etl/tests/`, pytest; `pytest` added to `etl/requirements.txt`)
Run from `etl/`. Heavy tests only run when selected (configured in `tests/conftest.py`, `pytest.ini`):

| Command | What runs | Time | Result (23 Nov) |
|---|---|---|---|
| `python -m pytest tests` | fast unit tests (no data files needed except the saved feed) | ~2 s | **65 passed** |
| `python -m pytest tests -m pipeline` | matcher probes, 14 centroid checks, mapping the 3 samples, determinism, quarantine, harness self-check | ~7 min | **20 passed** |
| `python -m pytest tests -m slow` | `check_psgc_geo` (7 geodata checks) | >10 min | see below |
| `python -m pytest tests -m graphdb` | the dev repository: every DROMIC event has a point | seconds | skipped (GraphDB was not running) |
| `python -m pytest tests -m network` | one live dry-run poll of the feed | seconds | not run |

What the files cover:
- `test_event_geo_math.py` (19 tests): haversine, spherical mean, the worked example in the paper and the guide, single location, barangay fallback, 3:1 weights, weight-field rule, zero weights, duplicate merge, unresolved, spread, precision order-independence (6 cases), cycle safety, `|`-split.
- `test_dromic_rss.py` (13 + 1 network): feed parsing on `tests/fixtures/dromic_feed_2026.xml` (live feed saved 22 Nov), de-duplication and newest-first order, report number / terminal / as-of extraction, title guard (Tandag vs Butuan), Google Viewer rewrite. The fetch loop runs against a fake HTTP session: first fetch, conditional 304, a rebuilt feed with known files, `--all-reports`, a failed download retried next poll (ETag not advanced), an HTML body rejected as a PDF, and a dry run that writes nothing.
- `test_update_dromic.py` (10): supersede (newest kept, idempotent, same name with a different start date kept apart, report-number tie-break, name clash, dry run), lock (overlap and stale takeover), retry-after-failure detection, run-log columns, `--repo` required.
- `test_evaluation_metrics.py` (23): PSGC code normalisation (Excel-dropped leading zero), barangay → municipality, count parsing, aggregation, recall/precision, exact-match rate and relative error.
- `test_pipeline_stages.py` (`pipeline`):
  - 15 location-matcher probes: the 6 NCR "Metro Manila" cases (Bug A), the barangay-below-city cases that the first Bug A fix broke, and the places in the pilot reports;
  - `check_event_centroid` (14 checks);
  - the 3 samples mapped and validated, giving the expected **359 triples** and the expected three points;
  - determinism: two mappings are isomorphic;
  - quarantine: a broken and a SHACL-violating event are isolated;
  - the accuracy harness self-check (a reference built from the pipeline scores 100%, and a planted wrong count and wrong municipality are detected);
  - `slow`: `check_psgc_geo`; `graphdb`: SPARQL on `SakunaGraPH-dev`.

### Two errors the new tests caught (both fixed)
1. **Superscript digits counted as digits** (`etl/evaluation/metrics.py`). Python's `\d` matches "¹", so a count printed "1,053¹" (footnote mark) would have been read as 10531 in the accuracy comparison. Fixed with `[0-9]`. The pipeline's own `transform/helpers.to_int` already uses `[^0-9.\-]` and is not affected.
2. **The "well under a metre" claim was wrong** (paper §5.4.2, `event_geo.py` docstring, first draft of the guide). Measured difference between the spherical mean and a plain lat/lon average:

   | Event extent | Difference |
   |---|---|
   | 4 neighbouring towns | 0.7–1.2 m |
   | one region (Western Visayas) | ~16 m |
   | country-wide | **0.8–1.7 km** |

   Corrected everywhere. The partners' guide now asks for the spherical method (as spreadsheet columns) instead of offering the plain average as a shortcut. New test `test_plain_mean_drifts_for_country_wide_events`.

### Accuracy harness (`etl/evaluation/`)
- `metrics.py`: pure functions. The comparison unit is the municipality-level PSGC code (barangay rows fold into their municipality, as the centroid does).
- `compare.py`:
  - **Inputs:** the annotator's `reports.csv`, `rows.csv` and `centroids.csv`, from `evaluation/reference/<annotator>/`.
  - **Pipeline side:** recomputed with the same `transform` and `event_geo` code that `run_dromic` uses, from the parsed folder whose `source.json` names the PDF. `_superseded/` is searched too.
  - **Outputs:** in `evaluation/results/<annotator>/`: `summary.md`, `per_report.csv`, `location_errors.csv` and `figure_errors.csv`.
  - **Centroid comparison, three distances:**
    1. pipeline vs the annotator's hand point (total error);
    2. pipeline vs the pipeline's formula applied to the **reference rows** (upstream error: parsing and matching);
    3. hand point vs that same formula (the annotator's arithmetic; should be ≈ 0).
- Command: `python -m evaluation.compare --annotator <initials>`.

### Partners' guide and materials
- `geog_partners_comparison_guide.md`: step-by-step protocol mirroring the pipeline. It covers:
  - which table and rows count;
  - CUM, not NOW;
  - displaced = inside + outside;
  - PSGC coding pitfalls ("Metro Manila", "City of X" vs "X City", Ozamis/Ozamiz, NIR, Sulu);
  - the centroid rules (location source priority, one weight field, merge duplicates, spherical mean with Excel columns and the `ATAN2` argument-order trap, unweighted point, spread by haversine, precision);
  - a worked example with intermediate sums to self-check the spreadsheet.
- `evaluation/`:
  - `pilot_reports.csv`: the 10 reports, stratified;
  - `pdfs/`: the same 10 PDFs, prefixed R01–R10;
  - `templates/`: the three CSV templates;
  - `reference_points.csv`: the 1,756 unit points the pipeline uses;
  - `reference/` and `results/`, empty for now.
- **PSGC codes in the guide were checked against the data.** My first draft had wrong NCR codes: Quezon City is `1381300000` and Las Piñas `1380200000`. PSGC also labels Tuguegarao "Tuguegarao City", not "City of …".

**Before sending to the partners:** only 3 of the 10 pilot PDFs are parsed. Parse the other 7 so the comparison has pipeline output:
`python -m parse.dromic --year 2026 --single <file>`, or let `update_dromic` pick them up.
Note: `evaluation/` is not covered by `.gitignore`. The PDFs (7.2 MB) would be committed with it unless you add `evaluation/pdfs/` to `.gitignore`.

**Parsing defects already visible in the sample (for the pilot to quantify):**
- Mayon "cost of assistance": one amount split across two columns (`"996 , 264.00 12"`, `", 336 , 730.59"`, i.e. 12,336,730.59 broken at the line).
- El Niño affected-population header absorbed digits (`Families .9 ,`).

### Advice: Word (DOCX) reports
- **How common:** all 19 reports downloaded on 17 Nov 2026, and all 10 in the 22 Nov feed, are PDFs. DOCX and Google Docs links appear mostly in older posts. Measure before investing: the run log now notes unconverted DOCX files, and the manifest records every file's extension.
- **Current behaviour:** `parse/dromic-docx-to-pdf.py` converts with **Microsoft Word through COM**, so it works only on Windows with Word installed. It also tidies tables first: repeats header rows and sets a minimum row height, which the PDF parser relies on.
- **Risk:** on the school server (Linux, no Word) every DOCX report would be lost. Before today, a conversion failure also failed the **whole** run.
- **Fixed today:** a conversion failure is now non-fatal (`update_dromic.stage_files`). The Word reports are skipped, named in the run log's `message`, and counted in `parse_failures`. Every PDF in the run still proceeds.
- **Recommended next step, when time allows:** replace Word COM with **LibreOffice headless** (`soffice --headless --convert-to pdf`). It is free and runs on Windows and Linux. Keep the header-row pre-processing (it uses python-docx, not Word). Validate on 5 DOCX reports by comparing the parsed tables with the Word-converted ones.
- Docling could read DOCX directly, but the parser's level detection works on the PDF layout, so this would be a bigger change.

### Advice: the manifest copy
- **Problem:** the parser reads each report's provenance (post URL, download URL, download time) from `manifest.json` **next to the PDF**, i.e. `raw/dromic-new/{year}-pdf/`. The fetchers write `raw/dromic-new/{year}/manifest.json`. The only copy step (`copy_pdfs_jsons` in the DOCX script) **never overwrites**. After the first run the copy goes stale, and every later report is parsed **without provenance**: empty `reportLink` and `downloadUrl`. Nothing errors.
- **Fixed today, two ways:**
  1. `update_dromic` refreshes the copy on every run;
  2. `parse/dromic.py` `process_file` now reads the **source** manifest (`{year}/manifest.json`) when it exists, and only falls back to the copy. Manual `parse.dromic` runs are therefore covered too.
- **Possible later check:** a SHACL rule that every DROMIC `Source` has a `downloadUrl`. It would catch any future provenance loss.
