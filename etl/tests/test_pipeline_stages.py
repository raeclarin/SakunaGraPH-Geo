"""Pipeline-level tests on real data (marker `pipeline`; `slow` for the full
geodata checks; `graphdb` for the loaded repository).

    python -m pytest tests -m pipeline
    python -m pytest tests -m slow
    python -m pytest tests -m graphdb
"""

from __future__ import annotations

import os
import shutil

import pytest

pytestmark = pytest.mark.pipeline


# ----------------------------------------------------------------------------
# Location matcher: regression probes (notes.md §8.13)
# ----------------------------------------------------------------------------

MATCHER_PROBES = [
    # NCR rows written with "Metro Manila" (Bug A: all used to resolve to Manila)
    ("Quezon City, Metro Manila, NCR", "1381300000"),
    ("City of Las Pinas, Metro Manila, NCR", "1380200000"),
    ("Makati, Metro Manila, NCR", "1380300000"),
    ("Pateros, Metro Manila, NCR", "1381701000"),
    ("Manila, Metro Manila, NCR", "1380600000"),
    ("Quezon City, NCR", "1381300000"),
    # Barangay below a city: the first Bug A fix broke these (Lahug -> City of Naga)
    ("Lahug, Cebu City, VII", "0730600000"),
    ("Talomo, Davao City, XI", "1130700000"),
    # Ordinary municipality / city rows, and the pilot reports' places
    ("Guinobatan, Albay, V", "0500504000"),
    ("Daraga, Albay, REGION V", "0500503000"),
    ("Tapaz, Capiz, REGION VI", "0601917000"),
    ("Tuguegarao City, Cagayan, II", "0201529000"),
    ("City of Ozamis, Misamis Occidental, X", "1004210000"),   # report spelling vs PSGC "Ozamiz"
    ("Alangalang, Leyte, VIII", "0803702000"),
    ("Albay, V", "0500500000"),                                 # province-only row
]


@pytest.fixture(scope="module")
def matcher(psgc_files):
    from semantic_processing.location_matcher_v2 import LOCATION_MATCHER
    return LOCATION_MATCHER


@pytest.mark.parametrize("text, code", MATCHER_PROBES)
def test_location_matcher_probe(matcher, text, code):
    assert matcher.match([text]) == [f"https://sakuna.ph/{code}"]


# ----------------------------------------------------------------------------
# Event centroid on real PSGC points (validate/check_event_centroid.py, 14 checks)
# ----------------------------------------------------------------------------

def test_event_centroid_checks(psgc_files):
    from validate import check_event_centroid
    assert check_event_centroid.main() == 0


# ----------------------------------------------------------------------------
# Mapping the 3 sample events: SHACL, centroids, determinism, quarantine
# ----------------------------------------------------------------------------

EXPECTED_POINTS = {   # notes.md §8.14 (after the Bug A/B fixes)
    "Effects of El Nino in Western Visayas 20261117": (11.174777, 122.454205, "Municipality"),
    "Fire Incident in Brgy. E. Rodriguez Quezon City 20261115": (14.680492, 121.060467, "City"),
    "Mayon Volcano Seismic Activities 20261117": (13.169899, 123.552245, "Municipality"),
}


def _map_samples(sample_event_dirs, tmp_path, validator=None, quarantine=False):
    from pipeline import run_dromic
    work = tmp_path / "parsed"
    for d in sample_event_dirs:
        shutil.copytree(d, work / d.name)
    g, rows, quarantined = run_dromic.create_graph(), [], []
    n = run_dromic.process_event_batches(
        sorted(p.name for p in work.iterdir()), g, sub_data_dir=str(work),
        needs_rerun_path=str(work / "_needs_rerun.txt"), validator=validator,
        centroid_rows=rows, quarantine=quarantine, quarantined=quarantined,
    )
    return n, g, rows, quarantined, work


@pytest.fixture(scope="module")
def validator(psgc_files):
    from validate.validate import ShaclValidator
    return ShaclValidator.from_paths(include_context_graphs=True)


def test_sample_events_map_validate_and_centroids(sample_event_dirs, tmp_path, validator):
    n, g, rows, quarantined, _ = _map_samples(sample_event_dirs, tmp_path, validator, quarantine=True)
    assert n == 3 and quarantined == []
    assert len(g) == 359                                   # notes.md §8.14
    got = {r["folder"]: (r["lat"], r["lon"], r["precision"]) for r in rows}
    assert got == EXPECTED_POINTS


def test_mapping_is_deterministic(sample_event_dirs, tmp_path):
    from rdflib.compare import isomorphic
    _, g1, *_ = _map_samples(sample_event_dirs, tmp_path / "a")
    _, g2, *_ = _map_samples(sample_event_dirs, tmp_path / "b")
    assert isomorphic(g1, g2)


def test_quarantine_isolates_bad_events(sample_event_dirs, tmp_path, validator):
    from rdflib import Literal, Namespace
    from pipeline import run_dromic
    geo = Namespace("http://www.opengis.net/ont/geosparql#")

    work = tmp_path / "parsed"
    for d in sample_event_dirs:
        shutil.copytree(d, work / d.name)
    shutil.copytree(sample_event_dirs[1], work / "ZZ SHACL BAD 20261115")
    (work / "ZZ BROKEN 20261115").mkdir()
    (work / "ZZ BROKEN 20261115" / "metadata.json").write_text("{not json")

    original = run_dromic._map_event

    def patched(folder_path, g, needs_rerun_path, rows=None):
        original(folder_path, g, needs_rerun_path, rows)
        if "SHACL BAD" in folder_path:                     # swap lat/lon -> outside the PH box
            for geom in list(g.objects(None, geo.hasGeometry)):
                g.set((geom, geo.asWKT, Literal("POINT (13.0 123.0)", datatype=geo.wktLiteral)))

    run_dromic._map_event = patched
    try:
        g, rows, q = run_dromic.create_graph(), [], []
        n = run_dromic.process_event_batches(
            sorted(p.name for p in work.iterdir() if p.is_dir()), g, sub_data_dir=str(work),
            needs_rerun_path=str(work / "_needs_rerun.txt"), validator=validator,
            centroid_rows=rows, quarantine=True, quarantined=q,
        )
    finally:
        run_dromic._map_event = original
    assert n == 3 and sorted(q) == ["ZZ BROKEN 20261115", "ZZ SHACL BAD 20261115"]
    assert len(g) == 359
    assert "ZZ SHACL BAD 20261115" in (work / "_needs_rerun.txt").read_text(encoding="utf-8")
    assert {r["folder"] for r in rows} == set(EXPECTED_POINTS)


# ----------------------------------------------------------------------------
# Accuracy harness: a reference built from the pipeline itself must score 100%,
# and a planted error must be detected.
# ----------------------------------------------------------------------------

def test_compare_detects_planted_errors(sample_event_dirs, tmp_path):
    import csv
    from evaluation import compare

    pilot = {r["report_id"]: r["file_name"] for r in compare.read_csv(compare.EVAL_DIR / "pilot_reports.csv")}
    rid = next(k for k, v in pilot.items() if "Mayon" in v)
    pipe = compare.load_pipeline_report(pilot[rid])
    assert pipe.folder is not None, pipe.error

    rows = []
    for code, vals in sorted(pipe.rows.items()):
        rows.append({"report_id": rid, "table": "affected", "psgc_code": code,
                     **{k: "" if v is None else str(v) for k, v in vals.items()}})
    c = pipe.centroid
    ref = tmp_path / "ref"
    ref.mkdir()
    for name, data in (
        ("reports.csv", [{"report_id": rid, "event_name": pipe.event_name, "start_date": pipe.start_date,
                          "report_number": pipe.report_number}]),
        ("rows.csv", rows),
        ("centroids.csv", [{"report_id": rid, "lat": c.lat, "lon": c.lon, "precision": c.precision}]),
    ):
        with (ref / name).open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(dict.fromkeys(k for r in data for k in r)))
            w.writeheader()
            w.writerows(data)

    perfect = compare.run("selfcheck", [rid], reference_dir=ref, out_dir=tmp_path / "out1")[0]
    assert perfect["location_recall"] == perfect["location_precision"] == 1.0
    assert perfect["affected_persons_exact"] == 1.0
    assert perfect["km_pipeline_vs_hand"] == 0 and perfect["km_pipeline_vs_ref_formula"] == 0
    assert perfect["name_match"] is True

    # Plant one wrong count and one wrong municipality in the "reference".
    rows[0]["affected_persons"] = str(int(rows[0]["affected_persons"] or 0) + 100)
    rows[1]["psgc_code"] = "0500510000"
    with (ref / "rows.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    planted = compare.run("selfcheck", [rid], reference_dir=ref, out_dir=tmp_path / "out2")[0]
    assert planted["location_recall"] < 1.0 and planted["location_precision"] < 1.0
    assert planted["affected_persons_exact"] < 1.0
    assert planted["km_pipeline_vs_ref_formula"] > 0        # upstream difference shows in the point
    errors = (tmp_path / "out2" / "location_errors.csv").read_text(encoding="utf-8")
    assert "0500510000" in errors


# ----------------------------------------------------------------------------
# Full geodata regression (validate/check_psgc_geo.py, 7 checks, >10 min)
# ----------------------------------------------------------------------------

@pytest.mark.slow
def test_psgc_geo_checks(psgc_files):
    from validate import check_psgc_geo
    assert check_psgc_geo.main() == 0


# ----------------------------------------------------------------------------
# Loaded repository (GraphDB on localhost:7200, repository SakunaGraPH-dev)
# ----------------------------------------------------------------------------

@pytest.mark.graphdb
def test_dev_repository_events_have_points():
    import requests
    repo = os.environ.get("SAKUNA_TEST_REPO", "SakunaGraPH-dev")
    query = """
    PREFIX geo: <http://www.opengis.net/ont/geosparql#>
    PREFIX : <https://sakuna.ph/>
    SELECT (COUNT(DISTINCT ?e) AS ?events) (COUNT(DISTINCT ?missing) AS ?without) WHERE {
      # events by (inferred) class: :hasLocation alone also matches table rows
      ?e a :DisasterEvent .
      GRAPH <https://sakuna.ph/events/dromic> { ?e ?p ?o }
      OPTIONAL { FILTER NOT EXISTS { ?e geo:hasGeometry ?g } BIND(?e AS ?missing) }
    }"""
    try:
        r = requests.get(f"http://localhost:7200/repositories/{repo}", params={"query": query},
                         headers={"Accept": "application/sparql-results+json"}, timeout=30)
    except requests.ConnectionError:
        pytest.skip("GraphDB not running on localhost:7200")
    r.raise_for_status()
    b = r.json()["results"]["bindings"][0]
    events, without = int(b["events"]["value"]), int(b["without"]["value"])
    assert events > 0
    assert without == 0, f"{without} of {events} event(s) without a point"
