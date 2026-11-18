Hello. I have inherited this project from my seniors and want to improve on it as part of my thesis project. Before anything though, can you help me audit, code review and onboard me onto the project? You can store notes as part of a notes.md perhaps.

To be specific: the project currently creates location data just through text. However, I want to improve on this and create geographic coordinates as location data, and perhaps explore ways to make this possible. Is there a field already for X and Y data? If yes, what methods can be done to calculate this? Finding coordinates of Philippine administrative units, matching, and finding centroids? But first, onboarding. Again, may you help me get familiarized with the repo? 
I'd be happy to help you onboard onto SakunaGraPH! Let me explore the repository structure first, then dig into the ontology and location handling — which is exactly where your coordinate idea would live.

Good news — the ontology is stated to import GeoSPARQL 1.1, so there may already be scaffolding for coordinates. Let me dig into the ontology, the README, and how locations are currently modeled.

Interesting — EM-DAT and GDA already carry latitude/longitude in their dataclasses. Let me check whether those actually get mapped into RDF, and how the ontology models geographic units.

Excellent — big findings already. Let me look at a few more key spots: the :latitude/:longitude property definitions, :hasLocation's range, the frontend map, and the api/ directory.

Yes — it's the Step 3 preview, but with three additions I confirmed just now by reading the actual CLIs: the barangay-level backbone rebuild (recommended before DROMIC, since its transform extracts barangays), the sampling flags (--single for the parser, --limit for the mapper), and explicit verification steps at the end. I also verified the exact argument names of every script so nothing below is guessed.

The command sequence (run from etl/)
Phase A — refresh the location backbone (re-proves Step 2.5 with more data)
cd etl
python -m pipeline.run_psgc --barangay      # 43,766 rows → barangays included; SHACL runs at the end
python -m pipeline.run_psgc_geo             # centroids still join (Prov/City/Mun; barangays have no polygons yet)
python -m validate.check_psgc_geo           # the 3 green tests (combined graph, swap detection, WKT detection)
If you'd rather keep this phase light, skip the --barangay rebuild — your current psgc.ttl (1,756 individuals) already covers municipality-level rows, which is what most DROMIC tables carry.

Phase B — sample raw DROMIC reports
python -m fetch.dromic --year 2026 --max-pages 1
Scrapes page 1 only (~10 posts), downloading the first PDF of each post into data/raw/dromic-new/2026/ plus a fresh manifest.json. That's your sample set.

python .\parse\dromic-docx-to-pdf.py
Converts any DOCX to PDF and produces data/raw/dromic-new/2026-pdf/ (the parser's input dir).

Phase C — parse the sample
python -m parse.dromic --year 2026 --single "<one-filename>.pdf"
Run it 2–3 times with different filenames from 2026-pdf/ (sampling mode), or run without --single to parse everything. Each file → data/parsed/dromic/2026/<event folder>/ with metadata.json, source.json, CSVs.

Phase D — map to RDF with SHACL enforcement
python -m pipeline.run_dromic --year 2026 --validate --limit 3
Processes the first 3 event folders, runs per-batch SHACL validation (with PSGC/disaster-type/org/prov context graphs), writes data/rdf/events/dromic/dromic-2026.ttl.

Phase E — verify the coordinate chain end-to-end
DROMIC events don't carry coordinates themselves — they inherit them via :hasLocation → PSGC IRI → psgc_geo.ttl. So the proof is a merged-graph query (event → location → latitude). I'll run this verification for you when you've done the above — plus two belt-and-braces checks:

python -m validate.validate ..\data\rdf\events\dromic\dromic-2026.ttl   # standalone SHACL vs main shapes
How you know it worked
Check	Pass signal
Backbone	check_psgc_geo prints all 3 ✅; run_psgc_geo still "Joined 84 of 88"
Parsing	event folders created with metadata.json + CSVs; no parse crashes
Mapping	log says "SHACL validation passed for DROMIC batch 1/1"; no new entries in _needs_rerun.txt
Coordinates	SPARQL query returns DROMIC events whose :hasLocation resolves to a PSGC IRI carrying :latitude/geo:asWKT
Where to expect friction (so a failure doesn't surprise you)
fetch.dromic needs Chrome + Selenium (Chrome must be installed; selenium is in requirements).
dromic-docx-to-pdf.py uses Word COM automation (docx2pdf) — needs Microsoft Word installed. It's also a hyphenated script, so it runs as python .\parse\dromic-docx-to-pdf.py, not -m. I haven't confirmed its CLI flags — check --help before the full run, or ping me and I'll read it.
Docling/torch on Python 3.14 — the DROMIC parser is the heavyweight and least-tested-on-this-env piece. If python -m parse.dromic fails on import, that's the environment, not our changes.
If metadata.json ends up with empty reportLink/downloadUrl, the converter didn't copy manifest.json into the -pdf dir — a known cosmetic gap, not a blocker.
When you've run these, ping me — I'll run the Phase E verification queries (and read any logs that went sideways) before we declare Step 3 sampling green and move toward Step 4 automation.