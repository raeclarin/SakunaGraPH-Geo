# SakunaGraPH Glossary

Terms a new researcher will meet in this codebase and in the surrounding literature
(semantic web, geospatial data, ETL engineering). Definitions are written for a layman,
with project-specific context where relevant.

---

## 1. Knowledge graphs & the Semantic Web

**Knowledge graph (KG)** — A database whose facts are stored as connections between things
rather than rows in tables. SakunaGraPH's KG stores "the typhoon *Odette* **affected** the
municipality *Sogod*" as linked entries rather than a spreadsheet row.

**RDF (Resource Description Framework)** — The W3C standard format for knowledge graphs.
Everything is a **triple**: `subject → predicate → object` (e.g. `Event_X → hasLocation → Municipality_Y`).

**Triple / statement** — One atomic fact in RDF, the three-part sentence above. A knowledge
graph is just a very large set of triples — hence "triple store".

**Triple store (triplestore)** — A database specialized in storing and querying triples.
This project uses **GraphDB** (by Ontotext). You can think of it as "PostgreSQL for knowledge graphs".

**Turtle (.ttl)** — The human-readable text file format for RDF (the `.ttl` files in `ontology/`
and `data/rdf/`). RDF can also be serialized as JSON-LD, N-Triples, etc.; Turtle is the friendliest.

**Ontology** — A formal, machine-readable *dictionary + grammar* for a domain: the classes of
things that exist (`DisasterEvent`, `Province`), their properties (`hasLocation`, `startDate`),
and rules (a Province is part of a Region). SakunaGraPH's ontology lives in
`ontology/sakunagraph.ttl`.

**Vocabulary / scheme** — A lighter-weight ontology: usually just terms and labels without deep
structure. See **SKOS** below.

**IRI vs URI vs URL** — A URL is a web address that locates a page. A URI identifies *anything*.
An IRI is the extended-Unicode version of a URI, used in RDF. SakunaGraPH mints IRIs like
`https://sakuna.ph/ndrrmc/{uuid}` — they *identify* an event even if no web page exists there.
(Deconfusion tip: in RDF an IRI is a name, not necessarily a link.)

**Literal** — A plain value in RDF (string, number, date) as opposed to an IRI. Literals are
"leaves"; IRIs are "nodes".

**Class vs individual (instance)** — A *class* is a category (`:Typhoon`); an *individual* is a
specific thing in the data (`the Odette event of Dec 2021`). The file `sakunagraph.ttl` mostly
defines classes; the ETL output creates individuals.

**owl:Class / OWL (Web Ontology Language)** — The W3C language for writing rich ontologies:
inheritance, restrictions ("every Incident must belong to a DisasterEvent"), equivalences.
`owl:` is the prefix for OWL terms.

**rdfs:subClassOf, rdfs:domain, rdfs:range** — RDFS (RDF Schema) building blocks:
`subClassOf` = "is a kind of"; `domain` = "subjects of this property must be of this class";
`range` = "values of this property must be of this class". E.g. `:hasProvince` has range
`:Province`.

**Object property vs datatype property** — An *object property* links two resources
(`Event → hasLocation → IRI of a municipality`); a *datatype property* links a resource to a
literal (`Event → latitude → 14.6`).

**Restriction (owl:someValuesFrom / allValuesFrom)** — A rule attached to a class, e.g.
"`:AffectedPopulation` must have some `affectedPersons` value". Used heavily in
`sakunagraph.ttl`.

**owl:sameAs** — An OWL assertion that two IRIs refer to the *same real-world thing*. This is
what cross-source alignment produces (two NDRRMC and DROMIC records of the same typhoon).

**Semantic web / linked data** — The research program of publishing structured data on the web
so machines can integrate it. The general field this project's modeling comes from.

**SPARQL** — The query language for RDF (like SQL for triple stores). The `api/` backend sends
SPARQL to GraphDB. Example: "all incidents in Region VIII since 2025-11-01".

## 2. Vocabularies this project imports

**SKOS (Simple Knowledge Organization System)** — The W3C vocabulary for taxonomies and
thesauri: `skos:prefLabel` (official name), `skos:altLabel` (alternate names), `skos:broader`.
Used in `disaster_type_scheme.ttl` for disaster types.

**PROV** — The W3C vocabulary for **provenance**: where a fact came from
(`prov:wasDerivedFrom`, `prov:wasAttributedTo`). Critical for a data-integration project — it
records that an event fact came from a specific DROMIC report PDF.

**QUDT** — "Quantities, Units, Dimensions and Types": vocabulary for physical quantities and
units (e.g. PHP pesos, km²). Used for damage amounts and magnitudes.

**beAWARE** — An EU-project ontology for crisis/disaster reporting that SakunaGraPH reuses
classes from (`baw:Impact`, `baw:Incident`, `baw:ClimateParameter`). The "prefixes" in `.ttl`
files (`baw:`, `skos:`, `geo:`) are just namespace shorthand for these vocabularies.

**GeoSPARQL** — The OGC standard for representing *geometries* (points, polygons) and *spatial
queries* (distance, containment) in RDF/SPARQL. This is the standard your GPS-coordinate
contribution will use. Key terms it defines are below (§4).

## 3. Validation & constraints

**SHACL** — "SHApes Constraint Language": a W3C language for *validating* RDF data against
rules ("every Incident must have a startDate"). Analogous to schema constraints / unit tests
for a database. This project validates all output with SHACL shapes in
`ontology/shapes/shapes.ttl` via the `etl/validate/` module (library: pySHACL).

**Shapes file** — The `.ttl` containing SHACL rules (the "schema for the data", as opposed to
the ontology being the "schema for the world").

## 4. Geospatial terms (your thesis area)

**Geocoding** — Converting a *name or address* ("Sogod, Southern Leyte") into *coordinates*.
**Reverse geocoding** is the opposite. Note: geocoding ≠ geolocation ≠ georeferencing, terms
often used loosely in papers.

**Centroid** — The geometric "center of mass" of a polygon — the natural pin location for an
administrative unit. For concave or multi-island shapes the plain centroid can fall in the sea;
alternatives exist (largest-part centroid, `representative_point()`).

**Polygon / MultiPolygon** — Boundary shapes. A MultiPolygon is a shape made of several
disconnected parts (an island province).

**GeoJSON** — The de-facto JSON format for map features (what `frontend/static/data/regions.geojson`
uses). Widely supported by map libraries; convertible to/from WKT.

**WKT (Well-Known Text)** — The text format for geometries, e.g. `POINT (125.1 8.6)`.
**Watch the order: X = longitude, Y = latitude** — `POINT (lon lat)`, not the reverse.

**CRS (Coordinate Reference System) / EPSG codes** — Defines *which* coordinate system numbers
refer to. The world-standard for GPS is **EPSG:4326 (WGS84)** — lat/lon in decimal degrees.
Mixing CRSs is a classic silent-data-corruption bug; always state the CRS.

**Geometry vs feature** — In GeoSPARQL, a `geo:Feature` is the real-world thing (a province);
a `geo:Geometry` is its shape. They are linked by `geo:hasGeometry`, and the shape is serialized
with `geo:asWKT`.

**Boundary dataset / admin boundaries** — Officially published polygon files for administrative
units. Sources relevant to the Philippines: OCHA's **COD-AB** (Common Operational Dataset –
Administrative Boundaries, published on **HDX**, the Humanitarian Data Exchange), **GADM**,
**geoBoundaries**, and NAMRIA/PSA government GIS data.

**Pcode (place code)** — A stable administrative code used by humanitarian datasets — for the
Philippines this is the **PSGC**.

**Geospatial join** — Matching two datasets by spatial relationship or shared key (here: joining
DROMIC's municipality names to PSGC codes, then codes to boundary polygons). Contrast with
*fuzzy text matching*, which is what the current pipeline does (see §6).

**Population-weighted centroid** — A centroid adjusted toward where people actually live rather
than the geometric middle — a later-stage accuracy refinement.

## 5. Philippine disaster-data context

**PSGC (Philippine Standard Geographic Code)** — PSA's official hierarchical coding of all
administrative units (10-digit codes: region → province → municipality → barangay). The
geographic backbone of SakunaGraPH. Source: PSA publication datafile (Excel).

**Administrative levels (PH)** — Region (e.g. Region VIII) → Province (Southern Leyte) →
Municipality/City (Sogod) → Barangay (smallest unit, village-level). Some units are special:
HUC (Highly Urbanized City), ICC (Independently Component City), NCR/Metro Manila, and the
occasionally created regions (e.g. Negros Island Region) — these edge cases are why the
location matcher is tricky.

**NDRRMC** — National Disaster Risk Reduction and Management Council; publishes PDF situation
reports per disaster. One of the five sources.

**DSWD / DROMIC** — Department of Social Welfare and Development; **DROMIC** is its Disaster
Response Operations Monitoring and Information Center. Publishes situation/terminal reports on
a WordPress site, typically twice daily (6AM/6PM cycles). The thesis's primary source.

**EM-DAT** — The international Emergency Events Database (CRED, Brussels). Global, but updated
infrequently — a secondary/backfill source here.

**GDA** — "Geography Disaster Archive", a historical disaster records source used in the pipeline.

**Situation report (sitrep) / terminal report** — Ongoing updates during a disaster vs the final
closing report ([Terminated] in DROMIC titles). Reports are numbered (#1, #2, …) — the *same*
disaster accumulates many reports, each a snapshot of evolving figures.

**MajorEvent vs Incident** — SakunaGraPH classes: a `MajorEvent` is the whole disaster (e.g. a
typhoon spanning days); an `Incident` is a specific occurrence (a fire, a flood in one area)
linked to it. Alignment decides which records from different sources describe the same event.

## 6. ETL & data engineering

**ETL** — Extract, Transform, Load: the classic pipeline pattern. In this repo: `fetch/` (extract),
`transform/` + `semantic_processing/` (transform), `mappings/` + `pipeline/` + `load_graphdb.py` (load).

**Dataclass** — A Python construct for typed record objects. The dataclasses in `transform/`
(e.g. `Event`, `AffectedPopulation`) are the internal exchange format between pipeline stages.

**Entity resolution (ER) / record linkage** — Deciding whether records from different sources
refer to the same real-world entity ("Typhoon Odette" in NDRRMC = "Odette" in DROMIC?).
`pipeline/build_alignment.py` does this across sources.

**Blocking** — An ER optimization: only compare records that share a coarse key (e.g. same
month + disaster type) instead of all pairs. Used in `build_alignment.py`.

**Fuzzy matching / string similarity** — Matching names despite typos and variants. Libraries
here: **thefuzz** (edit-distance ratios) and **rapidfuzz**; thresholds like `FUZZ_THRESHOLD_MUNI = 60`
are minimum similarity scores.

**Semantic similarity / embeddings** — Matching by *meaning* rather than spelling: text is
converted to vectors (embeddings) and compared by cosine similarity. Used by
`disaster_classifier.py` via **sentence-transformers**.

**Manifest** — A state file recording what has been fetched/processed so far (`manifest.json` in
the DROMIC raw folders). Enables **idempotent, resumable** runs.

**Idempotent** — Re-running produces the same result as running once (no duplicates). A key
property for automated daily pipelines: safe to re-run after a crash.

**Incremental update** — Processing only new/changed data instead of rebuilding everything.
Supported by the manifest design, `build_alignment --incremental`, and GraphDB PUT loading.

**Provenance tracking** — Recording the origin of each fact (see PROV above). Also a general
data-engineering term.

**SHACL batch validation** — Validating each batch of output triples during ETL so bad data
fails early with a pointer to the offending batch (implemented in `run_dromic.py`).

## 7. Web scraping & automation

**Web scraping** — Programmatically extracting data from websites. Two flavors here:
browser automation and feed/API consumption.

**Selenium / WebDriver / headless browser** — Drives a real Chrome programmatically (click
"Read More", paginate, download). Robust against JS-heavy pages but slow and brittle; used by
`etl/fetch/dromic.py`.

**RSS / Atom feed** — A standard XML "what's new" stream that websites publish. Discovered
finding: DROMIC's WordPress category feeds are open and contain every report link — enabling a
Selenium-free fetcher.

**WordPress** — The CMS DROMIC runs on. Its conventions (posts, categories, `wp-json` REST API,
feeds, `wp-block-file` blocks) matter for scraping. The REST API (`/wp-json/...`) is the
machine-readable endpoint, but DROMIC's returns 401 — the RSS feed is the workable one.

**REST API / endpoint** — A machine-readable HTTP interface returning JSON/XML. Distinct from
scraping HTML.

**Rate limiting / robots.txt / scraping etiquette** — Politeness and legality norms: throttle
requests, identify your client, respect disallowed paths. Include this in your thesis methodology.

**DAG (Directed Acyclic Graph)** — In workflow engines (Airflow), a pipeline defined as tasks
with dependencies, no cycles: *fetch → parse → map → align → load*. Also used generally for any
dependency graph.

**Apache Airflow** — A workflow scheduler for DAGs; the repo contains a DAG template
(`airflow_fetch_sources_template.py`) for scheduled fetching.

**Cron** — The classic time-based scheduler syntax (`0 22 * * *` = daily 22:00 UTC = 6AM PHT).
GitHub Actions and Task Scheduler use the same idea.

**GitHub Actions** — CI/automation service that runs scripts in GitHub's cloud on a schedule
(`.github/workflows/*.yml`). Candidate for the daily DROMIC run.

**Self-hosted runner** — A GitHub Actions agent running on your own machine — needed if the
automated job must reach a local GraphDB.

**Windows Task Scheduler** — The built-in Windows job scheduler; simplest option for a daily
local run.

## 8. Libraries & tools in this repo

**rdflib** — The Python RDF library: builds graphs, serializes Turtle, runs SPARQL.

**GraphDB** — Ontotext's triple store; supports GeoSPARQL functions (relevant for spatial
queries once coordinates exist).

**Docling** — IBM's document-understanding library; converts PDFs (incl. tables) into structured
documents. Used for DROMIC reports. Heavy (PyTorch); slow — a reason to keep daily runs
incremental.

**pdfplumber** — Lighter PDF text/table extractor; used for NDRRMC and as a low-level helper in
the DROMIC parser (font-based hierarchy classification).

**Polars / Pandas** — Dataframe libraries (like in-memory spreadsheets). Polars is the primary;
Pandas appears where Docling exports tables.

**thefuzz / rapidfuzz** — Fuzzy string matching libraries (§6).

**sentence-transformers** — Embedding models for semantic similarity (§6).

**SvelteKit / Svelte** — The frontend framework; `frontend/` renders the graph explorer, event
tables, and map.

**FastAPI** — The Python web framework behind `api/`, which queries GraphDB and serves the
frontend.

**pytest** — The Python test framework; `api/tests/` holds the repo's only tests.

**pySHACL** — Python SHACL validation engine used by `etl/validate/validate.py`.

**Shapely** — Python geometry library (centroid, intersection, `representative_point()`) — the
tool for the planned centroid computation.

**UUID5** — Deterministic UUIDs derived from a namespace + name (hash, not random). The reason
re-running the ETL regenerates *identical* IRIs (`mappings/iris.py`).

## 9. Research/thesis vocabulary

**RRL (Review of Related Literature)** — The Philippine-thesis term for the literature review.

**Proof of concept vs accuracy evaluation** — Your scoping distinction: first demonstrate the
pipeline produces geocoded KG data at all; later, quantify how well automated parsing matches
manually curated parsing.

**Ground truth / gold standard** — A manually verified dataset against which automated results
are scored (needed later for the accuracy work).

**Baseline** — The existing approach you compare against (e.g. the current text-only matching).

**Primary vs secondary source (data context)** — DROMIC as the freshest primary producer; EM-DAT
as a secondary aggregator. Note this conflicts slightly with the *provenance* sense of the words
(first-hand vs derivative records) — define your usage explicitly in the thesis.

**FAIR data principles** — Findable, Accessible, Interoperable, Reusable — a common rubric for
arguing the value of standards-based KGs; useful framing for the thesis motivation.
