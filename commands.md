# SakunaGeoGraPH — command reference (gathering → validation → GraphDB → map)

All Python commands run **from `SakunaGraPH-Geo/etl/`** (the pipeline uses `../data/...` paths).
Windows PowerShell syntax; GraphDB Free on `http://localhost:7200`; the thesis repository is
**`SakunaGraPH-dev`**. Never point a write command at `SakunaGraPH` (the seniors' copy).

```powershell
cd "C:\Users\Acer\Desktop\Claude\Thesis Project\SakunaGraPH-Geo\etl"
```

---

## 0. One-time setup (only on a fresh machine, or when the inputs change)

| # | What | Command | Output |
|---|---|---|---|
| 0.1 | Python dependencies | `pip install -r requirements.txt` | — |
| 0.2 | PSGC backbone (needs the PSA workbook in `data/raw/psgc/`) | `python -m pipeline.run_psgc --barangay` | `data/rdf/psgc/psgc.ttl` (SHACL-checked before promotion) |
| 0.3 | Unit points from COD-AB boundaries (needs `data/raw/boundaries/cod-ab/shp/`) | `python -m pipeline.run_psgc_geo` | `data/rdf/psgc/psgc_geo.ttl`, `logs/psgc_geo_coverage.csv` |
| 0.4 | Organisation registry → RDF | `python -m semantic_processing.org_registry` | `data/rdf/orgs/orgs.ttl` |
| 0.5 | Provenance graph | (no builder; `data/rdf/prov/prov.ttl` is kept in the data folder, notes §8.15) | — |
| 0.6 | Load everything into the dev repository | `python -m pipeline.load_graphdb --repo SakunaGraPH-dev --replace --timeout 1200` | named graphs `ontology`, `psgc`, `orgs`, `prov`, `events/dromic` (~5 min, PSGC inference dominates) |

Repeat 0.6 only for the graph that changed, e.g. after an ontology edit:
`python -m pipeline.load_graphdb --repo SakunaGraPH-dev --replace --scope ontology`.

---

## 1. The automated chain (what the scheduler runs every hour)

One command does gathering → parsing → centroid calculation → SHACL → load:

```powershell
python -m pipeline.update_dromic --repo SakunaGraPH-dev
```

| Stage | Module | What happens |
|---|---|---|
| 1 fetch | `fetch/dromic_rss.py` | GET the year's RSS feed (conditional: HTTP 304 = nothing new, stop); download the newest report file of each new post into `data/raw/dromic-new/{year}/` (+ `manifest.json`, `rss_state.json`) |
| 2 stage | `update_dromic.stage_files` | copy PDFs to `{year}-pdf/`; convert DOCX via Word (Windows only, failures non-fatal) |
| 3 parse | `parse/dromic.py` (Docling) | PDF → `data/parsed/dromic/{year}/<event> <date>/` (`metadata.json`, `source.json`, table CSVs), via `_staging/` |
| 4 supersede | `update_dromic.supersede_old_reports` | keep only the newest report per event; older folders → `_superseded/` |
| 5 map + SHACL | `pipeline/run_dromic.py` (`transform/dromic.py`, `LOCATION_MATCHER`, `event_geo.py`) | rows → PSGC IRIs → **event centroid** → RDF; SHACL per batch, failing events **quarantined** → `data/rdf/events/dromic/dromic-{year}.ttl`, `logs/dromic_event_centroids.csv` |
| 6 load | `pipeline/load_graphdb.py` | atomic replace of graph `events/dromic` only |
| 7 record | wrapper | `logs/update_dromic.log`, one row in `logs/update_runs.csv` |

Useful variants:

| Purpose | Command |
|---|---|
| See what would be fetched, change nothing | `python -m pipeline.update_dromic --repo SakunaGraPH-dev --dry-run` |
| **Backfill older posts** (read N feed pages, 10 posts each, even past posts already seen) | `python -m pipeline.update_dromic --repo SakunaGraPH-dev --backfill --max-pages 5` |
| Re-map and reload existing parsed data (no fetch) | `python -m pipeline.update_dromic --repo SakunaGraPH-dev --rebuild` |
| Run everything but do not touch GraphDB | `python -m pipeline.update_dromic --repo SakunaGraPH-dev --no-load` |
| Previous year (around New Year) | `python -m pipeline.update_dromic --repo SakunaGraPH-dev --year 2025` |

Schedule it hourly (Windows Task Scheduler, runs while logged on; GraphDB must be running):

```powershell
powershell -ExecutionPolicy Bypass -File ..\scripts\schedule_dromic.ps1
```
(`-Unregister` removes the task.)

---

## 2. The same chain, stage by stage (for debugging one stage)

| Stage | Command |
|---|---|
| Fetch only | `python -m fetch.dromic_rss --year 2026` (`--dry-run`, `--all-reports`, `--backfill --max-pages N`, `--force`) |
| Parse one PDF | `python -m parse.dromic --year 2026 --single "<file name inside data/raw/dromic-new/2026-pdf>"` |
| Parse all un-parsed PDFs of a year (skips those in `_parsed.txt`) | `python -m parse.dromic --year 2026` |
| Map + centroid + SHACL | `python -m pipeline.run_dromic --year 2026 --validate --quarantine` |
| Validate any Turtle file against the shapes | `python -m validate.validate ..\data\rdf\events\dromic\dromic-2026.ttl` |
| Load only the DROMIC graph | `python -m pipeline.load_graphdb --repo SakunaGraPH-dev --replace --scope events` |

**Recovering a report that failed to parse** (the run log shows `Parse failed: <file>`; it is not retried
automatically, because the file is already in the manifest):

```powershell
python -m parse.dromic --year 2026 --single "<file name from the log>"
python -m pipeline.update_dromic --repo SakunaGraPH-dev --rebuild
```

Triage files: `data/parsed/dromic/{year}/_needs_rerun.txt` (quarantined events),
`_assistance_failures.txt` (events kept without assistance rows), `_superseded/` (older reports).

---

## 3. Testing and validation

| Level | Command | Time |
|---|---|---|
| Fast unit tests (default) | `python -m pytest tests` | seconds |
| Pipeline tests (matcher probes, 14 centroid checks, 3 sample events, determinism, quarantine) | `python -m pytest tests -m pipeline` | ~7 min |
| GraphDB checks on `SakunaGraPH-dev` (every event has a point) | `python -m pytest tests -m graphdb` | seconds |
| Geodata regression (7 PSGC point checks) | `python -m pytest tests -m slow` | ~9 min |
| Live feed dry run | `python -m pytest tests -m network` | seconds |
| Everything except the network test | `python -m pytest tests -m "not network" --all` | ~15 min |
| Stand-alone checks | `python -m validate.check_event_centroid`, `python -m validate.check_psgc_geo` | — |
| Accuracy vs hand-parsed reference (pilot) | `python -m evaluation.compare --annotator <initials>` | — |

Quick SPARQL sanity check (events with a point):

```powershell
curl.exe -s -H "Accept: text/csv" --data-urlencode "query=PREFIX : <https://sakuna.ph/> SELECT (COUNT(DISTINCT ?e) AS ?n) WHERE { ?e a :DisasterEvent ; :latitude ?lat }" http://localhost:7200/repositories/SakunaGraPH-dev
```

---

## 4. Viewing on the site

From `SakunaGeoGraPH-frontend/sakuna.ph` (`.env` needs `GRAPHDB_REPO_NAME=SakunaGraPH-dev` and the
two `PUBLIC_SUPABASE_*` keys):

| Purpose | Command | Then open |
|---|---|---|
| Development server | `npm run dev` | `http://localhost:5173/disasters/coordinate-map` |
| Type check | `npm run check` | — |
| Production build + run | `npm run build`, then `node build` (PM2 on the server: `ecosystem.config.cjs`) | port 3000 |

The page queries GraphDB on every load and every filter change, so new data appears after a
browser refresh; nothing has to be rebuilt.
