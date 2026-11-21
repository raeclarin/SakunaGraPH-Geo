"""Checks for the event centroid (semantic_processing.event_geo) on synthetic
events built from real PSGC units (Albay, around Mayon Volcano).

Run from etl/ after `python -m pipeline.run_psgc_geo`:
    python -m validate.check_event_centroid

No parsed DROMIC data is needed; this isolates the calculation from parsing.
"""

from __future__ import annotations

from pathlib import Path

from rdflib import Graph, Literal, URIRef

from mappings.dromic import Event, event_geometry_mapping, event_mapping
from mappings.graph import SKG
from semantic_processing.event_geo import (
    GeoIndex,
    compute_event_centroid,
    haversine_km,
    spherical_mean,
)
from validate.validate import validate_graph

ROOT = Path(__file__).resolve().parents[2]
PSGC = ROOT / "data" / "rdf" / "psgc" / "psgc.ttl"
PSGC_GEO = ROOT / "data" / "rdf" / "psgc" / "psgc_geo.ttl"
SHAPES = ROOT / "ontology" / "shapes" / "shapes.ttl"

DARAGA = SKG["0500503000"]        # Municipality
TABACO = SKG["0500517000"]        # City
LEGAZPI = SKG["0500506000"]       # City
GUINOBATAN = SKG["0500504000"]    # Municipality
ALBAY = SKG["0500500000"]         # Province
DARAGA_ALOBO = SKG["0500503002"]  # Barangay (no own point)
BOGUS = URIRef("https://sakuna.ph/9999999999")


def main() -> int:
    psgc = Graph(); psgc.parse(PSGC, format="turtle")
    geo = Graph(); geo.parse(PSGC_GEO, format="turtle")
    index = GeoIndex.from_graphs(psgc, geo)
    results: list[bool] = []

    def report(name: str, passed: bool, detail: str = "") -> None:
        results.append(passed)
        print(f"{'PASS' if passed else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))

    def point(iri):
        p = index.resolve(iri)
        return p.lat, p.lon

    # 1. Single location -> that town's point, spread 0
    c = compute_event_centroid(index, [(DARAGA, {"affectedPersons": 120})])
    report("single location = municipality point",
           (c.lat, c.lon) == point(DARAGA) and c.method == "single-location"
           and c.spread_km == 0 and c.precision == "Municipality",
           f"{c.lat},{c.lon} {c.method}")

    # 2. Barangay walks up to its municipality
    c = compute_event_centroid(index, [(DARAGA_ALOBO, {})])
    report("barangay resolves to parent municipality",
           (c.lat, c.lon) == point(DARAGA) and c.precision == "Municipality",
           c.precision)

    # 3. Equal weights == unweighted mean == spherical midpoint
    c = compute_event_centroid(index, [
        (DARAGA, {"affectedPersons": 500}), (TABACO, {"affectedPersons": 500}),
    ])
    mid = spherical_mean([point(DARAGA), point(TABACO)])
    report("equal weights = midpoint",
           haversine_km(c.lat, c.lon, *mid) < 0.001 and c.weighting_shift_km < 0.001,
           f"shift {c.weighting_shift_km} km")

    # 4. 3:1 weights -> point sits 1/4 of the way from the heavier town
    c = compute_event_centroid(index, [
        (DARAGA, {"affectedPersons": 3000}), (TABACO, {"affectedPersons": 1000}),
    ])
    total = haversine_km(*point(DARAGA), *point(TABACO))
    to_daraga = haversine_km(c.lat, c.lon, *point(DARAGA))
    ratio = to_daraga / total
    report("3:1 weights put point 1/4 of the way",
           abs(ratio - 0.25) < 0.005 and c.method == "weighted-mean:affectedPersons",
           f"ratio {ratio:.4f}, {c.method}, shift {c.weighting_shift_km} km")

    # 5. One field missing for one town -> next field that covers all
    c = compute_event_centroid(index, [
        (DARAGA, {"affectedPersons": 900, "affectedFamilies": 300}),
        (TABACO, {"affectedFamilies": 100}),
    ])
    report("falls back to a field every location has",
           c.method == "weighted-mean:affectedFamilies", c.method)

    # 6. No common weight field -> unweighted
    c = compute_event_centroid(index, [
        (DARAGA, {"affectedPersons": 900}), (TABACO, {"affectedFamilies": 100}),
    ])
    report("no common field -> unweighted",
           c.method == "unweighted-mean" and (c.lat, c.lon) == (c.unweighted_lat, c.unweighted_lon),
           c.method)

    # 7. Duplicate rows for the same town merge (weights summed)
    c = compute_event_centroid(index, [
        (DARAGA, {"affectedPersons": 1000}), (DARAGA, {"affectedPersons": 2000}),
        (TABACO, {"affectedPersons": 1000}),
    ])
    report("duplicate locations merge", c.location_count == 2
           and abs(haversine_km(c.lat, c.lon, *point(DARAGA)) / total - 0.25) < 0.005,
           f"{c.location_count} locations")

    # 8. A province-only row makes the event Province precision
    c = compute_event_centroid(index, [(DARAGA, {}), (ALBAY, {})])
    report("coarsest precision wins", c.precision == "Province", c.precision)

    # 9. Unresolvable IRIs are reported, not fatal; all-unresolvable -> None
    c = compute_event_centroid(index, [(DARAGA, {}), (BOGUS, {})])
    none = compute_event_centroid(index, [(BOGUS, {})])
    report("unresolved locations reported", c.unresolved == [str(BOGUS)] and none is None)

    # 10. Mayon-like event: 4 towns, spread is the farthest town
    c = compute_event_centroid(index, [
        (DARAGA, {"affectedPersons": 4000}), (LEGAZPI, {"affectedPersons": 2500}),
        (GUINOBATAN, {"affectedPersons": 3000}), (TABACO, {"affectedPersons": 1500}),
    ])
    farthest = max(haversine_km(c.lat, c.lon, *point(i)) for i in (DARAGA, LEGAZPI, GUINOBATAN, TABACO))
    report("spread = farthest location",
           abs(c.spread_km - farthest) < 0.001,
           f"{c.lat},{c.lon} spread {c.spread_km} km, shift {c.weighting_shift_km} km")

    # 11. Emitted RDF passes the main SHACL shapes; a bad point is caught
    g = Graph()
    event = SKG["dromic/test-event"]
    event_geometry_mapping(g, event, c)
    ok = _conforms(g)
    report("event geometry conforms to SHACL", ok)
    bad = Graph()
    swapped = type(c)(**{**c.__dict__, "lat": c.lon, "lon": c.lat})
    event_geometry_mapping(bad, event, swapped)
    report("swapped lat/lon caught by SHACL", not _conforms(bad))

    # 12. Event metadata with several IRIs joined by "|" -> one hasLocation each
    g = Graph()
    ev = Event(eventName="Test", hasDisasterType="Fire", startDate=None, endDate=None,
               id="x", remarks="", hasBarangay=None,
               hasLocation=URIRef(f"{DARAGA}|{TABACO}"))
    uri = event_mapping(g, ev)
    locs = set(g.objects(uri, SKG.hasLocation))
    report("pipe-joined location split into IRIs", locs == {DARAGA, TABACO}, str(len(locs)))

    print(f"\n{sum(results)}/{len(results)} checks passed")
    return 0 if all(results) else 1


def _conforms(g: Graph) -> bool:
    try:
        validate_graph(
            g,
            label="event geometry",
            shapes_graph=SHAPES,
            ontology_graph=None,
            include_context_graphs=False,
            include_default_context=False,
        )
        return True
    except Exception:
        return False


if __name__ == "__main__":
    raise SystemExit(main())
