"""Regression checks for psgc_geo.ttl (centroid enrichment of PSGC).

Run from etl/ after `python -m pipeline.run_psgc_geo`:
    python -m validate.check_psgc_geo

Checks:
1. psgc.ttl + psgc_geo.ttl + ontology validate together against the PSGC shapes.
2. A lat/lon swap on a copy is CAUGHT by the shapes.
3. A malformed WKT is CAUGHT by the shapes.
4. An invalid :geoPrecision value is CAUGHT by the shapes.
5. Every PSGC Prov/City/Mun/SubMun has exactly one point (coverage).
6. Known places land within REFERENCE_TOLERANCE_KM of a reference location,
   covering each join rule (code, NIR/Sulu re-code, HUC name match, parent).
7. A renumbered Maguindanao code is NOT joined to the COD-AB feature that
   carries the same code (guards against the silent code-only mis-join).
"""

from __future__ import annotations

import csv
from pathlib import Path

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import XSD

from transform.psgc_geodata import haversine_km
from validate.validate import validate_graph

ROOT = Path(__file__).resolve().parents[2]
PSGC = ROOT / "data" / "rdf" / "psgc" / "psgc.ttl"
GEO_TTL = ROOT / "data" / "rdf" / "psgc" / "psgc_geo.ttl"
ONTOLOGY = ROOT / "ontology" / "sakunagraph.ttl"
SHAPES = ROOT / "ontology" / "shapes" / "psgc" / "shapes.ttl"
COVERAGE = ROOT / "logs" / "psgc_geo_coverage.csv"

SKG = Namespace("https://sakuna.ph/")
GEO = Namespace("http://www.opengis.net/ont/geosparql#")

COVERED_LEVELS = {"Reg", "Prov", "City", "Mun", "SubMun"}

# Compact units only, so the polygon centroid is close to the town itself.
REFERENCE_TOLERANCE_KM = 10.0
REFERENCE_POINTS = {
    # code: (label, lat, lon, join rule exercised)
    "1380600000": ("City of Manila", 14.599, 120.984, "name (HUC)"),
    "1380300000": ("City of Makati", 14.557, 121.025, "name (HUC)"),
    "0631000000": ("City of Iloilo", 10.715, 122.555, "name (HUC)"),
    "1804610000": ("City of Dumaguete", 9.310, 123.300, "recode (NIR)"),
    "0906602000": ("Jolo", 6.050, 121.000, "recode (Sulu)"),
    "1380601000": ("Tondo I/II", 14.620, 120.970, "parent"),
}

# PSGC 1908822000 is South Upi; COD-AB PH1908822 is Pagagawan.
MISJOIN_GUARD = ("1908822000", "PH1908822")


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
    geo = Graph(); geo.parse(GEO_TTL, format="turtle")
    results: list[bool] = []

    def report(n: int, text: str, passed: bool) -> None:
        results.append(passed)
        print(f"TEST {n} {text}: {passed}")

    combined = base + geo
    ok, text = _validate(combined, "combined psgc + psgc_geo (+ ontology)")
    report(1, "combined graph conforms", ok)
    if not ok:
        print(text)
        return 1

    # TEST 2: lat/lon swap must be caught (longitude range + latitude range)
    swapped = base + geo
    victim = _sample_subject(swapped)
    lat = swapped.value(victim, SKG.latitude)
    lon = swapped.value(victim, SKG.longitude)
    swapped.remove((victim, SKG.latitude, lat))
    swapped.remove((victim, SKG.longitude, lon))
    swapped.add((victim, SKG.latitude, lon))
    swapped.add((victim, SKG.longitude, lat))
    ok, _ = _validate(swapped, "swapped coordinates")
    report(2, "lat/lon swap detected as violation", not ok)

    # TEST 3: malformed WKT must be caught
    broken = base + geo
    geom_subject = next(broken.subjects(GEO.asWKT, None))
    wkt = broken.value(geom_subject, GEO.asWKT)
    broken.remove((geom_subject, GEO.asWKT, wkt))
    broken.add((geom_subject, GEO.asWKT, Literal("POLYGON ((0 0))", datatype=GEO.wktLiteral)))
    ok, _ = _validate(broken, "malformed WKT")
    report(3, "malformed WKT detected as violation", not ok)

    # TEST 4: invalid precision value must be caught
    bad_precision = base + geo
    geom_subject = next(bad_precision.subjects(SKG.geoPrecision, None))
    old = bad_precision.value(geom_subject, SKG.geoPrecision)
    bad_precision.remove((geom_subject, SKG.geoPrecision, old))
    bad_precision.add((geom_subject, SKG.geoPrecision, Literal("Street")))
    ok, _ = _validate(bad_precision, "invalid precision")
    report(4, "invalid geoPrecision detected as violation", not ok)

    # TEST 5: coverage — every Reg/Prov/City/Mun/SubMun has exactly one point
    missing, multiple = [], []
    for s, _p, level in base.triples((None, SKG.geographicLevel, None)):
        if str(level) not in COVERED_LEVELS:
            continue
        n = len(list(geo.objects(s, SKG.latitude)))
        if n == 0:
            missing.append(f"{base.value(s, SKG.psgcCode)} {base.value(s, URIRef('http://www.w3.org/2000/01/rdf-schema#label'))}")
        elif n > 1:
            multiple.append(str(s))
    report(5, "every Reg/Prov/City/Mun/SubMun has exactly one point", not missing and not multiple)
    for m in missing:
        print(f"    missing: {m}")
    for m in multiple:
        print(f"    multiple: {m}")

    # TEST 6: reference locations
    far = []
    for code, (label, ref_lat, ref_lon, rule) in REFERENCE_POINTS.items():
        s = SKG[code]
        lat, lon = geo.value(s, SKG.latitude), geo.value(s, SKG.longitude)
        if lat is None:
            far.append(f"{label} ({rule}): no point")
            continue
        d = haversine_km(float(lat), float(lon), ref_lat, ref_lon)
        print(f"    {label:<20} {rule:<14} {float(lat):.4f},{float(lon):.4f}  {d:5.1f} km")
        if d > REFERENCE_TOLERANCE_KM:
            far.append(f"{label} ({rule}): {d:.1f} km")
    report(6, f"reference places within {REFERENCE_TOLERANCE_KM:g} km", not far)
    for f in far:
        print(f"    {f}")

    # TEST 7: renumbering mis-join guard (needs the cod-ab coverage report)
    if COVERAGE.exists():
        code, wrong_pcode = MISJOIN_GUARD
        with COVERAGE.open(encoding="utf-8") as fh:
            row = next((r for r in csv.DictReader(fh) if r["psgc_code"] == code), None)
        ok = row is not None and row["source_pcode"] != wrong_pcode
        detail = f"{row['label']} <- {row['source_pcode']} {row['source_name']} ({row['matched_by']})" if row else "no row"
        report(7, "renumbered code not mis-joined", ok)
        print(f"    {detail}")
    else:
        print("TEST 7 skipped: no coverage report (run the cod-ab source first)")

    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
