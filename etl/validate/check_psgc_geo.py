"""One-off Step 2.5 verification: combined-graph + negative SHACL tests.

Run from etl/:
    python -m validate.check_psgc_geo

Checks:
1. psgc.ttl + psgc_geo.ttl + ontology validate together against the PSGC shapes.
2. A lat/lon swap on a copy is CAUGHT by the shapes.
3. A malformed WKT is CAUGHT by the shapes.
"""

from __future__ import annotations

from pathlib import Path

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import XSD

from validate.validate import validate_graph

ROOT = Path(__file__).resolve().parents[2]
PSGC = ROOT / "data" / "rdf" / "psgc" / "psgc.ttl"
GEO = ROOT / "data" / "rdf" / "psgc" / "psgc_geo.ttl"
ONTOLOGY = ROOT / "ontology" / "sakunagraph.ttl"
SHAPES = ROOT / "ontology" / "shapes" / "psgc" / "shapes.ttl"

SKG = Namespace("https://sakuna.ph/")


def _validate(g: Graph, label: str) -> tuple[bool, str]:
    try:
        validate_graph(
            g,
            label=label,
            shapes_graph=SHAPES,
            ontology_graph=ONTOLOGY,
            include_context_graphs=False,
            include_default_context=False,
        )
        return True, ""
    except Exception as exc:
        return False, str(exc)


def _sample_subject(g: Graph) -> URIRef:
    for s in g.subjects(SKG.latitude, None):
        return s
    raise RuntimeError("no coordinate subject found")


def main() -> int:
    base = Graph(); base.parse(PSGC, format="turtle")
    geo = Graph(); geo.parse(GEO, format="turtle")
    ontology = Graph(); ontology.parse(ONTOLOGY, format="turtle")

    combined = base + geo
    ok, text = _validate(combined, "combined psgc + psgc_geo (+ ontology)")
    print(f"TEST 1 combined graph conforms: {ok}")
    if not ok:
        print(text)
        return 1

    # TEST 2: lat/lon swap must be caught (longitude range + latitude range)
    swapped = combined
    victim = _sample_subject(swapped)
    lat = swapped.value(victim, SKG.latitude)
    lon = swapped.value(victim, SKG.longitude)
    swapped.remove((victim, SKG.latitude, lat))
    swapped.remove((victim, SKG.longitude, lon))
    swapped.add((victim, SKG.latitude, lon))
    swapped.add((victim, SKG.longitude, lat))
    ok, _ = _validate(swapped, "swapped coordinates")
    print(f"TEST 2 lat/lon swap detected as violation: {not ok}")

    # TEST 3: malformed WKT must be caught
    broken = base + geo
    from rdflib.namespace import Namespace as NS
    GEO_NS = NS("http://www.opengis.net/ont/geosparql#")
    geom_subject = next(broken.subjects(GEO_NS.asWKT, None))
    wkt = broken.value(geom_subject, GEO_NS.asWKT)
    broken.remove((geom_subject, GEO_NS.asWKT, wkt))
    broken.add(
        (geom_subject, GEO_NS.asWKT, Literal("POLYGON ((0 0))", datatype=GEO_NS.wktLiteral))
    )
    ok, _ = _validate(broken, "malformed WKT")
    print(f"TEST 3 malformed WKT detected as violation: {not ok}")

    return 0 if ok is False else 1


if __name__ == "__main__":
    raise SystemExit(main())
