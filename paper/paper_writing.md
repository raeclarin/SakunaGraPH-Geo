1. Abstract

2. Introduction

3. Review of Related Literature

4. Research Objective

5. Data and Methodology

> Draft status (2026-11-23): 5.1–5.6 drafted from notes.md §8.6–8.16. Sections marked
> **[TODO]** wait on later steps (DROMIC sample results, automation, frontend). Numbers in
> this section are from the runs logged in notes.md; re-check them before submission.

5.1 Overview

This work extends SakunaGraPH, a knowledge graph that integrates Philippine disaster records
from several heterogeneous sources into a single RDF graph, with two contributions: (1) point
coordinates for every administrative unit in the Philippine Standard Geographic Code (PSGC)
down to the municipality/city level, and (2) a representative point (centroid) for each
disaster event, computed from the locations the event affected. Because events in
SakunaGraPH reference locations through PSGC identifiers rather than free text, the
coordinates are attached once, to the administrative units, and every event from every
source inherits them through its location links. The DSWD DROMIC situation reports are the
primary event source studied here, since they are the most frequently updated.

The method has three stages (Figure F1):
1. **Location backbone.** Build an RDF graph of all PSGC units from the PSA publication
   datafile.
2. **Unit coordinates.** Derive one point per region, province, city, municipality and
   sub-municipality from official boundary polygons, and join them to the PSGC graph.
3. **Event coordinates.** For each DROMIC event, aggregate the points of its affected
   municipalities into one event point, weighted by the number of affected people.

5.2 Data sources

| Source | Content used | Version | Role | Licence / terms |
|---|---|---|---|---|
| PSA Philippine Standard Geographic Code (PSGC) publication datafile | Codes, names, levels and hierarchy of all administrative units | 3rd Quarter 2026 | Location backbone (43,766 units) | PSA public release; cite PSA |
| OCHA / HDX Common Operational Dataset – Administrative Boundaries (COD-AB), Philippines | Province (ADM2, 88 features), municipality/city (ADM3, 1,642 features) and barangay (ADM4) polygons, from PSA and NAMRIA | v03, valid 2025-02-13 (HDX release May 2026) | Geometry from which centroids are derived | CC BY-IGO 3.0 |
| DSWD Disaster Response Operations Monitoring and Information Center (DROMIC) situation reports | Per-municipality counts of affected and displaced families/persons, damaged houses, assistance | Reports published 2026 | Event records and centroid weights | Public government reports |

*Attribution (required by CC BY-IGO 3.0):* Administrative boundaries: OCHA / HDX COD-AB
Philippines (v03, valid 2025-02-13; sources PSA and NAMRIA), licensed CC BY-IGO 3.0.
Centroids derived by the authors.

PSGC codes are 10-digit hierarchical identifiers (region, province, city/municipality,
barangay). COD-AB identifies features by "pcodes", which are the PSGC digits prefixed with
`PH` and without trailing zeros (e.g. `PH0102801` corresponds to PSGC `0102801000`). The two
datasets have different vintages: the boundaries are valid as of February 2025, while the
PSGC release is from the third quarter of 2026. Administrative changes between the two dates
are the main difficulty in joining them (Section 5.3.2).

5.3 Administrative-unit coordinates

5.3.1 Centroid computation

For each COD-AB polygon we compute a single representative point as follows. The polygon is
projected from WGS 84 (EPSG:4326) to UTM zone 51N (EPSG:32651), a metric projection covering
most of the country, so that areas and centroids are computed in metres rather than degrees.
For a multipart polygon (e.g. a province made of several islands) only the largest part is
used, so that the point falls on the main landmass instead of in the sea between islands. The
area-weighted centroid of that part is taken. If the centroid falls outside the part (for
concave shapes such as crescent-shaped coastal towns), Shapely's `representative_point()` is
used instead, which is guaranteed to lie inside the polygon. The point is then projected back
to WGS 84 and rounded to six decimal places (≈ 0.1 m).

As a cross-check, each computed point is compared with the centre point that COD-AB publishes
for the same feature. Over 1,730 provinces, cities and municipalities the median distance is
1.0 km, and 95% are within 5.5 km. The largest differences come from multipart units where
COD-AB centres the whole shape and our method uses the largest part: Palawan (313 km, pulled
by the distant Kalayaan Islands), Sarangani (76 km; its whole-shape centre falls in Sarangani
Bay, between the province's two halves), Aparri (66 km, Fuga Island group) and Romblon
(60 km). For a map marker the largest-part point is preferable; the trade-off is that for a
split unit such as Sarangani the marker represents one of its two halves.

5.3.2 Joining boundaries to PSGC units

Each COD-AB feature is matched to a PSGC unit by a sequence of rules, applied in order. A
feature that fails one rule falls through to the next:

1. **Code**: the converted pcode equals a PSGC code *and* the names agree (fuzzy token-set
   similarity ≥ 85 after normalisation).
2. **Region re-code**: units moved to a new region after the boundary vintage are re-coded
   before the code match: Negros Occidental, Negros Oriental and Siquijor to the Negros Island
   Region (created 2024), and Sulu from BARMM back to Region IX.
3. **Name**: a unique normalised-name match among the not-yet-matched PSGC units of the same
   region. This covers highly urbanised cities, which COD-AB files under their province but
   PSGC codes independently, as well as renumbered municipalities.

PSGC units still without a point are then filled from related units:

4. **Parent**: the 14 sub-municipalities of the City of Manila (e.g. Tondo, Binondo) have no
   polygon of their own and take the City of Manila's point; their precision is recorded as
   City (Section 5.3.3).
5. **Barangay dissolve**: eight municipalities created in 2024 from barangays of the BARMM
   Special Geographic Area do not exist in the boundary data. Their polygons are rebuilt by
   uniting the polygons of their constituent barangays (COD-AB ADM4).
6. **Member dissolve (regions)**: the COD-AB region layer predates the Negros Island Region,
   so regions are rebuilt by uniting the polygons of each region's current member provinces,
   cities and municipalities as given by the PSGC hierarchy.

| Rule | Units joined |
|---|---|
| Code | 1,562 |
| Region re-code | 85 |
| Name | 69 |
| Member dissolve (regions) | 18 |
| Parent | 14 |
| Barangay dissolve | 8 |
| **Total** | **1,756 of 1,756** |

The name-agreement requirement in rule 1 is essential. In Maguindanao del Norte and del Sur,
renumbered after the boundary vintage, 16 COD-AB codes now identify a *different* PSGC
municipality; for example, COD-AB code `PH1908822` is Pagagawan while PSGC `1908822000` is
South Upi. A join on codes alone would have placed those 16 towns' points in neighbouring
towns without raising any error. With the name check they are rejected at rule 1 and
correctly matched by name at rule 3. Two further data issues were handled with documented
exceptions: one PSA label error ("City of Lucenaa/" for the City of Lucena) and one rename
(San Isidro, Davao del Norte, now Sawata, with an unchanged code).

5.3.3 Representation in the knowledge graph

Coordinates follow the OGC GeoSPARQL 1.1 standard. Each PSGC unit is linked to a geometry
node carrying a Well-Known Text point in longitude–latitude order (EPSG:4326). Plain
`:latitude`/`:longitude` values are added for convenience:

```turtle
:0500503000  geo:hasGeometry  :0500503000/geometry ;
             :latitude 13.114813 ; :longitude 123.688861 .
:0500503000/geometry  a geo:Geometry ;
    geo:asWKT  "POINT (123.688861 13.114813)"^^geo:wktLiteral ;
    :geoPrecision  "Municipality" ;
    prov:wasDerivedFrom  <https://sakuna.ph/dataset/cod-ab-phl-v03> .
```

`:geoPrecision` records the administrative level of the polygon the point was computed from,
which can differ from the unit's own level (a Manila sub-municipality has precision City).
Provenance uses W3C PROV-O: each geometry is derived from a dataset node that records the
COD-AB version, validity date and licence. The coordinates are written to a separate file
(`psgc_geo.ttl`) that is loaded alongside the PSGC graph, so the base PSGC graph is never
modified.

5.4 Event centroids

5.4.1 Inputs

A DROMIC situation report tabulates the impact of an event per affected municipality, e.g.
the number of affected families and persons and the number of displaced persons inside and
outside evacuation centres. The existing SakunaGraPH pipeline resolves each row's location
text to a PSGC unit; province and regional subtotal rows are removed. For each event the
centroid uses the first available of: (a) the affected-population rows, which carry counts;
(b) the location named in the report title (e.g. a fire in a single barangay); (c) the
housing-damage rows. Sources (b) and (c) carry no counts.

5.4.2 Calculation

Let an event have *n* distinct affected locations with points (φᵢ, λᵢ) (latitude, longitude)
and weights wᵢ.

1. **Point lookup.** Each location takes its unit's point from Section 5.3. A location
   without its own point (a barangay) takes the point of its nearest ancestor in the PSGC
   hierarchy.
2. **Weights.** A single count field is chosen for the whole event: the first of affected
   persons, affected families, displaced persons and displaced families that is positive for
   every location. Rows referring to the same location are merged by summing their counts.
   Weights from different fields are never mixed (e.g. persons for one town, families for
   another), because they are not comparable. If no single field covers every location, all
   weights are set to 1 (unweighted mean). An event with one location takes that location's
   point.
3. **Geographic mean.** Points are averaged as unit vectors on the sphere rather than as raw
   latitude/longitude pairs:

   xᵢ = cos φᵢ cos λᵢ,  yᵢ = cos φᵢ sin λᵢ,  zᵢ = sin φᵢ

   X = Σ wᵢxᵢ,  Y = Σ wᵢyᵢ,  Z = Σ wᵢzᵢ

   φ̄ = atan2(Z, √(X² + Y²)),  λ̄ = atan2(Y, X)

   This is exact on the sphere and independent of any map projection. A simple average of
   latitudes and longitudes differs from it by about 1 m for a handful of neighbouring towns,
   about 15 m across a region, and up to about 1.7 km for an event spanning the whole country,
   which is why the spherical form is used.
4. **Precision.** The event point's precision is the coarsest precision among its inputs.
   An event with even one location known only to the province level is labelled Province.
5. **Spread.** The spread is the largest great-circle (haversine) distance from the event
   point to any of its input points, a simple measure of how dispersed the event is (0 for a
   single location).

The unweighted mean is always computed as well, and the distance between the weighted and
unweighted points ("weighting shift") is reported for evaluation.

5.4.3 Representation

The event point uses the same GeoSPARQL pattern as the units, with three extra properties
recording how it was obtained:

```turtle
<event>  geo:hasGeometry <event/geometry> ; :latitude 13.155547 ; :longitude 123.670364 .
<event/geometry>  a geo:Geometry ;
    geo:asWKT "POINT (123.670364 13.155547)"^^geo:wktLiteral ;
    :geoPrecision "Municipality" ;
    :centroidMethod "weighted-mean:affectedPersons" ;
    :locationCount 4 ;
    :spreadKm 18.669 .
```

5.4.4 Worked example

Four municipalities around Mayon Volcano (Albay) with illustrative affected-person counts:
Daraga 4,000, Guinobatan 3,000, Legazpi 2,500 and Tabaco 1,500. The weighted event point is
(13.1555° N, 123.6704° E); the spread is 18.7 km, and weighting moves the point 2.5 km from the
unweighted mean, toward the most affected towns. In a two-town case with a 3:1 ratio
(Daraga 3,000, Tabaco 1,000), the point falls exactly one quarter of the way from Daraga to
Tabaco, as expected for a weighted mean.

5.4.5 Results on real reports

Three DROMIC situation reports of different shapes were processed end to end (Table T4): a
multi-province drought report, a volcanic report covering 13 municipalities and cities, and an
urban fire in a single barangay.

| Event (report) | Affected locations | Event point (lat, lon) | Method | Precision | Spread | Weighting shift |
|---|---|---|---|---|---|---|
| El Niño, Western Visayas (Report 1) | Dumarao, Tapaz (Capiz); Dueñas, Lambunao (Iloilo) | 11.174777, 122.454205 | weighted, affected persons | Municipality | 33.0 km | 8.8 km |
| Mayon Volcano seismic activity (Report 111) | 13 Albay municipalities and cities | 13.169899, 123.552245 | weighted, affected persons | Municipality | 38.1 km | 7.4 km |
| Fire, Brgy. E. Rodriguez, Quezon City (Report 4) | Quezon City | 14.680492, 121.060467 | single location | City | 0 km | 0 km |

All three points are plausible on inspection:
- **El Niño:** the point lies in central Panay, pulled toward Tapaz, the most affected town
  (22,795 persons).
- **Mayon:** the point lies about 17 km south-west of the crater, between the two most affected
  towns, Guinobatan (102,147 persons) and Ligao (80,926). Every one of the 13 towns was
  resolved.
- **Fire:** the point is the Quezon City point. This illustrates the limit of municipality
  precision for urban events: Quezon City is large, so the marker can be several kilometres
  from the affected barangay (Section 10).

The first run of the fire report placed it in the City of Manila, 12.0 km away. The parsed table
was correct (`NCR | Metro Manila | Quezon City`). However, the location matcher tried the province
column ("Metro Manila") as a city before looking at the municipality column, and "Metro Manila"
fuzzy-matched "City of Manila". Every NCR row written this way was affected. The defect was
visible only because the report title and the table named different places. A consistency check
between the two is proposed as a quality measure (Section 5.6).

5.5 Automated collection

DROMIC publishes its situation reports as posts on a WordPress site, one post per event. A post
is a living document: each new report is added to the same post, typically on the 6 AM and 6 PM
cycles. A long-running event such as Mayon therefore accumulates more than a hundred report
files in one post. The original collector was a Selenium scraper that downloaded only the first
file of a post and then recorded the post as done. As a result, an event stayed frozen at the
report captured on the first visit.

The new collector reads the site's category RSS feed
(`/category/situation-reports/{year}/feed/`). Each feed item carries:
- the post title, which includes the incident date and, when the event closes, a "[Terminated]"
  status;
- the publication time;
- the post's categories, which name the disaster type;
- the full post HTML, from which every linked report file is extracted, newest first.

Change detection is per file: a manifest keyed by the file URL records what has been downloaded,
so a new report in an old post is detected just like a new post. The feed is polled hourly,
which is the update period the feed itself declares. Because RSS is pull-based, freshness is
bounded by the polling interval. A conditional request (ETag / If-Modified-Since) makes a poll
with no changes cost a single HTTP 304 response.

Each poll that finds new files runs the rest of the pipeline incrementally (Figure F12):
1. **Fetch** the new report files and record them in the manifest.
2. **Parse** only the new PDFs into per-event tables.
3. **Supersede** older reports. Event identifiers are minted from the event name and start
   date, so all reports of one event map to the same event IRI. Only the newest report of each
   event is kept active, and older report folders are archived. Figures from different report
   dates are therefore never mixed in one event.
4. **Map and validate** all active events of the year into RDF, with SHACL validation. An event
   that fails mapping or validation is quarantined: it is skipped and listed for manual review,
   instead of blocking the update of every other event.
5. **Load** the result by atomically replacing the DROMIC named graph in GraphDB.

Two safeguards address failure modes observed during development:
- A downloaded file must be a real document; for example, a PDF must begin with `%PDF`.
- At least one place name in the post title must appear in the file name. This check would have
  caught a case where the original scraper saved another event's report under a post.

Every run appends one record to a run log with the files fetched, events mapped, events
quarantined, triples, load status, and the delay between the report's publication and its
appearance in the graph. This log provides the freshness measurements reported in Section 8.

5.6 Evaluation design

The evaluation separates two questions. *Engineering correctness* asks whether each component
does what it specifies. *Accuracy* asks whether the automated output agrees with what a careful
human would extract from the same report.

Engineering correctness is tested with automated checks (Section 7):
- join coverage per rule and level (Section 5.3.2);
- agreement of unit points with COD-AB's published centres (Section 5.3.1);
- unit tests of the centroid calculation on synthetic events with known answers;
- SHACL validation of every output;
- determinism: two runs on the same input give identical graphs;
- the behaviour of the automation steps (change detection, supersede, quarantine).

Accuracy is measured against a hand-built reference ("gold standard") for a sample of reports.
Partners from the Department of Geography prepare it independently, following a written
protocol that mirrors the pipeline step by step. For each report the annotators record:
- the event metadata;
- every table row, with its location written out and its PSGC code;
- the key counts;
- an event centroid computed by hand with the same rules (Section 5.4.2).

Comparing the pipeline with the reference stage by stage makes every error attributable to one
stage:

| Stage | Measure |
|---|---|
| Collection | share of sampled reports fetched, and fetched with the correct file |
| Event metadata | exact match of event name, disaster type and start date |
| Table extraction | row recall and precision (rows matched by location) |
| Location resolution | share of rows resolved to the same PSGC code; errors by administrative level |
| Figures | exact-match rate and median relative error per count field |
| Event centroid | (1) great-circle distance between the pipeline point and the reference point; (2) distance between the pipeline point and a point recomputed with the pipeline's own formula from the *reference* rows, which isolates upstream errors from the centroid method |

A pilot uses 10 reports stratified by disaster type and table size; a larger sample is future
work. Three further descriptive measures are reported:
- the distribution of the weighting shift across events;
- a title-versus-table location consistency check;
- the freshness of the graph (the delay from DROMIC publication to GraphDB).

6. Software and Implementation

> Draft status (2026-11-23): reflects the code in `etl/` as of this date.

6.1 Architecture

SakunaGraPH's ETL pipeline is written in Python and runs as a sequence of stages, each
reading the previous stage's files: fetch (download reports) → parse (PDF to tables) →
transform (tables to typed records, with location, organisation and disaster-type
resolution) → map (records to RDF triples) → validate (SHACL) → load (GraphDB). This work
adds a geodata stage that runs once per PSGC or boundary release, and an event-centroid step
inside the mapping stage (Figure F2).

| Component | File | Added / changed in this work |
|---|---|---|
| PSGC backbone | `etl/transform/psgc_datafile.py`, `etl/pipeline/run_psgc.py` | unchanged (run with barangays) |
| Boundary join + centroids | `etl/transform/psgc_geodata.py` | added: COD-AB reader, join rules, dissolves, provenance |
| Geodata orchestration | `etl/pipeline/run_psgc_geo.py` | added: staged output, SHACL gate, coverage reports |
| Event centroid | `etl/semantic_processing/event_geo.py` | new |
| DROMIC mapping | `etl/mappings/dromic.py`, `etl/pipeline/run_dromic.py` | event geometry triples, centroid report, multi-location fix |
| Ontology | `ontology/sakunagraph.ttl` | GeoSPARQL terms; `:geoPrecision`, `:centroidMethod`, `:locationCount`, `:spreadKm` |
| Constraints | `ontology/shapes/psgc/shapes.ttl`, `ontology/shapes/shapes.ttl` | coordinate range, WKT, precision and event-geometry shapes |
| Checks | `etl/validate/check_psgc_geo.py`, `etl/validate/check_event_centroid.py` | new regression checks |
| RSS collector | `etl/fetch/dromic_rss.py` | new: feed parsing, per-file manifest, download checks |
| Update orchestration | `etl/pipeline/update_dromic.py` | new: fetch → parse → supersede → map → load, run log, lock, retry |
| Quarantine | `etl/pipeline/run_dromic.py` | changed: failing events skipped instead of aborting the run |
| Location matcher | `etl/semantic_processing/location_matcher_v2.py` | fixed: NCR rows resolved to the City of Manila |
| Scheduling | `scripts/schedule_dromic.ps1` | new: hourly Windows Task Scheduler job |
| Tests and evaluation | `etl/tests/`, `etl/evaluation/` | new: pytest suite; comparison against a hand-built reference |

6.2 Tools and libraries

| Purpose | Tool |
|---|---|
| Language / runtime | Python 3.14 |
| RDF graphs and Turtle serialisation | rdflib |
| SHACL validation | pySHACL |
| Reading boundary shapefiles | GeoPandas with the pyogrio engine |
| Geometry operations (centroid, union, representative point) | Shapely |
| Coordinate projection (EPSG:4326 ↔ EPSG:32651) | pyproj (via GeoPandas) |
| Fuzzy name matching | RapidFuzz (token-set ratio) |
| Tabular processing | Polars, pandas |
| PDF table extraction (DROMIC) | Docling, pdfplumber |
| Triple store | Ontotext GraphDB |
| Standards | RDF 1.1, OWL 2, SHACL, GeoSPARQL 1.1, PROV-O, SKOS |

6.3 Geodata stage

`run_psgc_geo` loads the PSGC graph, reads the COD-AB ADM2 and ADM3 shapefiles (the GeoJSON
versions are 0.5–0.7 GB each; ADM4 is read only for the few barangays needed, using an
attribute filter), applies the join rules of Section 5.3.2, and writes the geometry triples
to a *staged* file. The staged file is validated against the PSGC SHACL shapes and promoted
to `psgc_geo.ttl` only if it conforms, so a failed run never replaces good data. Each run
also writes two reports: a coverage table listing, for every PSGC unit, its point, the join
rule used, the source feature and the distance to COD-AB's own centre; and a list of boundary
features that matched no PSGC unit, each with a reason. All documented exceptions (region
re-codes, the PSA label error, the confirmed rename) are kept as named constants with
comments in the source, so that every deviation from a pure code join is auditable.

6.4 Event-centroid step

The event-centroid module builds an in-memory index from PSGC IRI to point (with each
unit's parent, for the ancestor fallback) once per process. It reuses the PSGC graph already
loaded by the location matcher. During mapping, `run_dromic` collects each event's locations
and counts, calls the centroid function, and emits the geometry triples next to the event's
other triples, so they are validated in the same SHACL batch. A per-run report
(`logs/dromic_event_centroids.csv`) records the weighted and unweighted points, method,
precision, spread and any locations that could not be resolved.

6.5 Validation

Validation uses SHACL shapes in two places. The PSGC shapes require at most one coordinate
pair per unit within a loose Philippine bounding box (latitude 4–21.5°, longitude 111–128°),
exactly one WKT literal of the form `POINT (lon lat)` per geometry, and a precision value from
a fixed list. The event-geometry shape additionally constrains the WKT numerically to the
Philippine bounding box, so that a swapped latitude/longitude pair always fails, and checks the
method, location count and spread.

Two regression scripts complement the shapes:
- **`check_psgc_geo` (7 checks):** the combined PSGC graph conforms; deliberately injected
  errors (swapped coordinates, malformed WKT, invalid precision) are detected; every
  region, province, city, municipality and sub-municipality has exactly one point; reference
  towns covering each join rule fall within 10 km of their known locations; and the
  renumbered Maguindanao code is not mis-joined.
- **`check_event_centroid` (14 checks):** single-location, barangay-fallback,
  equal-weight, 3:1-weight, field-fallback, unweighted, duplicate-merge, precision, unresolved
  and spread cases on synthetic Albay events, plus independence from input order. The generated
  RDF conforms to SHACL, a swapped point is rejected, and multi-location event metadata is
  split correctly.

All checks pass on the current data.

6.6 Defects found and fixed during implementation

These are reported because they show the kind of silent errors that geographic enrichment of
a knowledge graph can introduce:
1. **Code collisions after renumbering:** 16 Maguindanao municipalities would have been
   placed in the wrong town by a code-only join (Section 5.3.2).
2. **Name collision across regions** (earlier, province-level prototype): "City of Isabela"
   (Basilan) name-matched the Province of Isabela (Cagayan Valley). Fixed by requiring the
   region prefix to agree.
3. **Stale region polygons:** using the boundary dataset's region layer put Region VII's
   point on Negros Island, which is now in a different region. Fixed by rebuilding regions
   from their current members.
4. **Multiple locations collapsed into one IRI:** report titles naming several places
   produced a single invalid IRI joining all of them. Fixed to emit one location link per place.
5. **Numeric literal type:** decimal coordinates stored as floating-point values failed SHACL's
   `xsd:decimal` check during in-memory validation. Fixed by emitting exact decimals.
6. **Metro Manila resolved to the City of Manila:** the location matcher tried the province
   column as a city before the municipality column. Every NCR row written "X, Metro Manila,
   NCR" was therefore placed in Manila (Section 5.4.5). The fix tries an exact city match first,
   then the municipality, then the old fuzzy rule. A first fix that simply tried the
   municipality first was rejected: an old-versus-new comparison on 34 probe strings showed it
   broke barangay-below-city rows (e.g. Lahug, Cebu City → City of Naga).
7. **Every event silently re-queued:** a leftover debug write to a non-existent folder made the
   assistance table fail for every event. The failure handler then listed the event for
   re-processing, so the next run skipped all events. The handler also truncated folder names
   at the first dot ("Brgy. E. Rodriguez" → "Brgy. E").
8. **Missing validation context:** organisation classes come from a separate graph that had
   never been built. The validator skipped the missing file without warning, so valid
   assistance data failed SHACL. Missing context graphs now produce a warning. Three
   organisations the mappings use (NGA, DROMIC, CRED) were also missing from the registry.
9. **Non-deterministic output:** two defects made repeated runs give different graphs.
   - An event mixing cities and municipalities was labelled City on one run and Municipality on
     the next, because the two levels tie in rank and the first one seen won.
   - Table rows came out of an unordered join and were numbered in that order, so a row's IRI
     could change between runs.

   Both matter once the pipeline re-runs automatically. They were fixed by a fixed tie order and
   by sorting rows before numbering; two runs now give isomorphic graphs.
10. **Events frozen at their first report:** the original scraper recorded progress per post and
    took only a post's first file, so later reports of an event were never collected
    (Section 5.5).

6.7 Storage and loading

The knowledge graph is stored in Ontotext GraphDB (Free 11.4) with the OWL 2 RL ruleset:
- Each source folder is loaded into its own named graph (e.g. `https://sakuna.ph/events/dromic`,
  `https://sakuna.ph/psgc`).
- `--replace` swaps a named graph in a single RDF4J transaction, so a failed load leaves the
  previous version in place.
- Development uses a separate repository with the same configuration as the production one, so
  new data never overwrites the existing graph.

The first full load took 5 min 10 s, dominated by inference over the PSGC graph. It covered the
ontology, organisations, provenance, PSGC with coordinates, and the sample events: 372k explicit
triples.

SPARQL checks confirmed:
- the event points;
- the event → municipality → point chain needed by the map;
- the inferred classes (events become `geo:Feature`s);
- that GeoSPARQL functions work without extra configuration: `geof:distance` places the Mayon
  event point 17 km from the crater.

6.8 Reproducing the results

From `etl/`:
```
python -m pipeline.run_psgc --barangay      # PSGC backbone
python -m pipeline.run_psgc_geo             # unit coordinates (COD-AB)
python -m semantic_processing.org_registry  # organisations graph
python -m validate.check_psgc_geo           # 7 checks
python -m validate.check_event_centroid     # 14 checks
python -m pytest tests                      # test suite (Section 7)
python -m pipeline.update_dromic --repo SakunaGraPH-dev            # one automated update
python -m pipeline.load_graphdb --repo SakunaGraPH-dev --replace   # full load
```
Hourly scheduling: `scripts/schedule_dromic.ps1`.

7. Testing

> Draft status (2026-11-23): automated suite complete; the accuracy pilot with the Department of
> Geography is prepared but not yet run (Section 7.3). Results go to Section 8.

7.1 Test levels

Testing follows the pipeline's stages and is organised in four levels, each selectable
separately so that the fast level can run on every change:

| Level | Scope | Data needed | Tests | Run time |
|---|---|---|---|---|
| Unit | centroid arithmetic, feed parsing, change detection, supersede, run log, accuracy measures | a saved copy of the live feed | 65 | ~2 s |
| Pipeline | location-matcher probes, centroid checks on real PSGC points, mapping and SHACL validation of real reports, determinism, quarantine, accuracy-harness self-check | PSGC graph, parsed sample reports | 20 | ~7 min |
| Geodata | full join and coordinate regression (Section 6.5) | PSGC graph, COD-AB boundaries | 1 (7 checks) | >10 min |
| Deployment | every DROMIC event in the loaded repository has a point; one live poll of the feed | GraphDB, internet | 2 | seconds |

Unit tests use synthetic inputs with known answers. Examples:
- for a 3:1 weighting the event point must lie one quarter of the way between two towns;
- the precision label must not depend on input order;
- a cycle in the administrative hierarchy must not hang the lookup.

The collector is tested without network access against a fake web server that replays the saved
feed. The tests cover the first poll, an unchanged poll (HTTP 304), a rebuilt feed containing only
known files, a failed download (which must be retried on the next poll), and a server returning a
web page instead of a PDF.

The pipeline level contains regression probes for every location-matching defect found during
development (Section 6.6), including the cases that a first, rejected fix had broken. It also
checks that the three sample reports produce exactly the expected 359 triples and event points.

7.2 Defects found by the tests

Writing the tests exposed two errors that the earlier checks had not:
- **Footnote marks read as digits.** The accuracy script read a count printed "1,053¹" as 10,531,
  because the regular-expression digit class also matches superscript digits. The pipeline's own
  number parser was not affected.
- **An overstated claim.** The claim that a plain average of coordinates agrees with the
  spherical mean to under a metre held only for small events. It is about 16 m for a region and up
  to 1.7 km for a country-wide event (Section 5.4.2 corrected).

7.3 Accuracy pilot (prepared)

The accuracy evaluation of Section 5.6 is implemented as a comparison script. It reads the
annotators' sheets and recomputes the pipeline's output for the same reports with the
production code, so no database is involved. The annotation protocol is written for partners
without programming background and mirrors each pipeline rule: which table rows count, which
column (cumulative vs current), how displaced counts are combined, how to code places in PSGC, and
how to compute the event point. The event point is computed on the sphere in a spreadsheet, with a
worked example to verify the set-up.

Before use, the comparison script was itself tested. A reference generated from the pipeline's
own output scores 100% on every measure. A reference with one altered count and one altered
municipality produces the expected drop in figure accuracy and location recall and precision,
and lists both errors.

The pilot sample has 10 reports stratified by event type and table size: volcanic, drought
(regional and national), health, weather, and five fires. The fires include two in Metro Manila,
a city whose report spelling differs from PSGC, a terminal report, and a city named in the "X City"
form.

8. Results and Conclusion

9. Recommendations

10. Future Works

Candidate items (from notes so far):
- Barangay-level precision (COD-AB ADM4 already provides polygons; DROMIC fire reports name a
  single barangay).
- Population-weighted unit centroids (PSGC 2024 population is already in the graph).
- Per-report event snapshots (DROMIC events evolve over many reports).
- Unit polygons in the graph (not only points), enabling GeoSPARQL containment queries.
- Larger accuracy study (Section 5.6) beyond the 10-report pilot, with two annotators per report
  and inter-annotator agreement.
- Incremental mapping (one RDF file per event) instead of rebuilding the year's file on every
  update.
- Title-versus-table location consistency as an automatic validation rule.
- Word (DOCX) reports converted without Microsoft Word (e.g. LibreOffice headless), so the
  pipeline can run on a Linux server.

11. Acknowledgments

Data: OCHA / HDX COD-AB Philippines (CC BY-IGO 3.0; sources PSA and NAMRIA); PSA PSGC; DSWD
DROMIC.
