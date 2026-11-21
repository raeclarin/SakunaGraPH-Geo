1. Abstract

2. Introduction

3. Review of Related Literature

4. Research Objective

5. Data and Methodology

> Draft status (2026-11-21): 5.1–5.4 drafted from notes.md §8.6–8.10. Sections marked
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

   This is exact on the sphere and independent of any map projection. At the extent of the
   Philippines it agrees with a simple average of coordinates to well under a metre.
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

**[TODO]** Replace or extend with results from real DROMIC reports (Quezon City fire, Mayon
Report 111, El Niño Western Visayas) once the sample run is complete.

5.5 Automated collection **[TODO, Goal 4]**

Planned content: DROMIC publishes on WordPress; the category RSS feed lists every report file
per post, which allows per-file (not per-post) change detection, unlike the original
Selenium scraper that took only the first file per post and never revisited a post. Polling
interval, idempotent manifest keyed by file URL, incremental parse → map → load chain.

5.6 Evaluation design **[TODO]**

Planned measures:
- Join coverage per rule and level (done, Section 5.3.2).
- Agreement of unit points with COD-AB's published centres (done, Section 5.3.1).
- Event points for a sample of reports checked by hand against the locations in the report.
- Weighting shift distribution (weighted vs unweighted) across events.
- Pipeline freshness once automated: delay between DROMIC publication and graph update.

6. Software and Implementation

> Draft status (2026-11-21): reflects the code in `etl/` as of this date.

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
- **`check_event_centroid` (13 checks):** single-location, barangay-fallback,
  equal-weight, 3:1-weight, field-fallback, unweighted, duplicate-merge, precision, unresolved
  and spread cases on synthetic Albay events. The generated RDF conforms to SHACL, a swapped
  point is rejected, and multi-location event metadata is split correctly.

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

6.7 Reproducing the results **[TODO: finalise with the automation step]**

From `etl/`:
```
python -m pipeline.run_psgc --barangay      # PSGC backbone
python -m pipeline.run_psgc_geo             # unit coordinates (COD-AB)
python -m validate.check_psgc_geo           # 7 checks
python -m validate.check_event_centroid     # 13 checks
python -m parse.dromic --year 2026 [--single FILE]
python -m pipeline.run_dromic --year 2026 --validate
```

7. Testing

8. Results and Conclusion

9. Recommendations

10. Future Works

Candidate items (from notes so far):
- Barangay-level precision (COD-AB ADM4 already provides polygons; DROMIC fire reports name a
  single barangay).
- Population-weighted unit centroids (PSGC 2024 population is already in the graph).
- Per-report event snapshots (DROMIC events evolve over many reports).
- Unit polygons in the graph (not only points), enabling GeoSPARQL containment queries.

11. Acknowledgments

Data: OCHA / HDX COD-AB Philippines (CC BY-IGO 3.0; sources PSA and NAMRIA); PSA PSGC; DSWD
DROMIC.
