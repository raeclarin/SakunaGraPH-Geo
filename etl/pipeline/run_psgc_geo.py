"""Orchestrate PSGC geodata enrichment: compute, join, validate, publish.

Reads the existing psgc.ttl, computes centroids for administrative units from
boundary polygons, and writes psgc_geo.ttl after SHACL validation. The
output is loaded into GraphDB alongside psgc.ttl — never merged into psgc.ttl
itself, so re-running this step never disturbs the base graph.

Sources:
    cod-ab   (default) HDX COD-AB shapefiles in data/raw/boundaries/cod-ab/:
             ADM2 -> provinces, ADM3 -> cities/municipalities (+ SubMun by
             parent inheritance, + barangay-dissolve for post-vintage towns).
             Writes a coverage report to logs/psgc_geo_coverage.csv.
    geojson  legacy: province polygons from frontend/static/data/regions.geojson.

Usage (from etl/):
    python -m pipeline.run_psgc_geo [--source cod-ab|geojson] [--levels ...]
"""

from __future__ import annotations

import argparse
import collections
import csv
import logging
import shutil
from pathlib import Path

from rdflib import Graph, URIRef

from transform import psgc_geodata
from validate.validate import ShaclValidationError, validate_graph

log = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_PSGC = ROOT_DIR / "data" / "rdf" / "psgc" / "psgc.ttl"
DEFAULT_GEOJSON = ROOT_DIR / "frontend" / "static" / "data" / "regions.geojson"
DEFAULT_SHAPES = ROOT_DIR / "ontology" / "shapes" / "psgc" / "shapes.ttl"
DEFAULT_REPORT_DIR = ROOT_DIR / "logs"

COD_AB_PROVINCE_LEVELS = ("Prov",)
COD_AB_MUNI_LEVELS = ("City", "Mun", "SubMun")


def _joined_from_geojson(g: Graph, geojson_path: Path, levels: list[str]) -> dict[URIRef, psgc_geodata.Centroid]:
    centroids, unmatched = psgc_geodata.load_centroids(geojson_path, levels=levels)
    for feature in unmatched:
        log.warning("Unmatched feature (%s): %s", feature.get("reason"), feature)

    joined, still_unmatched = psgc_geodata.join_centroids(g, centroids, levels=levels)
    for centroid in still_unmatched:
        log.warning("No PSGC individual for centroid %s (%s)", centroid.code, centroid.name or "?")
    log.info("Joined %d of %d centroid features (by code or name)", len(joined), len(centroids))

    level_of = {s: str(l) for s, _p, l in g.triples((None, psgc_geodata.SKG.geographicLevel, None))}
    return {
        s: psgc_geodata.Centroid(
            **{
                **c.__dict__,
                "precision": psgc_geodata.PRECISION_BY_LEVEL.get(level_of.get(s, ""), ""),
                "source": psgc_geodata.GEOJSON_DATASET,
            }
        )
        for s, c in joined.items()
    }


def _joined_from_cod_ab(
    g: Graph, cod_ab_dir: Path, report_dir: Path
) -> dict[URIRef, psgc_geodata.Centroid]:
    units = psgc_geodata.index_psgc(g)
    records: dict[str, psgc_geodata.PointRecord] = {}
    unjoined_rows: list[dict] = []

    for adm, levels in ((2, COD_AB_PROVINCE_LEVELS), (3, COD_AB_MUNI_LEVELS)):
        log.info("Reading COD-AB ADM%d from %s", adm, cod_ab_dir)
        gdf = psgc_geodata.read_cod_ab_layer(cod_ab_dir, adm)
        recs, unjoined = psgc_geodata.join_cod_ab(gdf, units, levels)
        log.info("ADM%d: %d features, %d joined, %d unjoined", adm, len(gdf), len(recs), len(unjoined))
        records.update(recs)
        for row in unjoined:
            unjoined_rows.append({"adm": adm, **row})

    unjoined_adm3 = [r for r in unjoined_rows if r["adm"] == 3 and r["reason"] != "no geometry"]
    missing = psgc_geodata.fill_gaps(
        records, units, COD_AB_MUNI_LEVELS, cod_ab_dir, unjoined_adm3
    )
    missing += [
        u for c, u in units.items()
        if u.level in COD_AB_PROVINCE_LEVELS and c not in records
    ]

    _write_reports(records, missing, unjoined_rows, units, report_dir)

    by_rule = collections.Counter(r.matched_by for r in records.values())
    target = [u for u in units.values() if u.level in COD_AB_PROVINCE_LEVELS + COD_AB_MUNI_LEVELS]
    log.info(
        "Coverage: %d of %d PSGC Prov/City/Mun/SubMun units have a point; by rule: %s",
        len(records), len(target), dict(sorted(by_rule.items())),
    )
    for unit in missing:
        log.warning("No point for PSGC %s %s (%s)", unit.code, unit.label, unit.level)

    return {r.unit.iri: r.as_centroid() for r in records.values()}


def _write_reports(records, missing, unjoined_rows, units, report_dir: Path) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    coverage = report_dir / "psgc_geo_coverage.csv"
    with coverage.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "psgc_code", "label", "level", "status", "matched_by", "precision",
            "lat", "lon", "source_pcode", "source_name",
            "cod_ab_center_lat", "cod_ab_center_lon", "km_to_cod_ab_center",
        ])
        for code in sorted(records):
            r = records[code]
            dist = (
                round(psgc_geodata.haversine_km(r.lat, r.lon, r.ref_lat, r.ref_lon), 3)
                if r.ref_lat is not None and r.ref_lon is not None else ""
            )
            w.writerow([
                code, r.unit.label, r.unit.level, "point", r.matched_by, r.precision,
                r.lat, r.lon, r.source_code, r.source_name,
                r.ref_lat if r.ref_lat is not None else "",
                r.ref_lon if r.ref_lon is not None else "", dist,
            ])
        for u in sorted(missing, key=lambda u: u.code):
            w.writerow([u.code, u.label, u.level, "missing", "", "", "", "", "", "", "", "", ""])
    log.info("Wrote coverage report: %s", coverage)

    unjoined_path = report_dir / "psgc_geo_unjoined_source.csv"
    with unjoined_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["adm", "pcode", "name", "reason"])
        w.writeheader()
        w.writerows(unjoined_rows)
    log.info("Wrote unjoined COD-AB features: %s (%d rows)", unjoined_path, len(unjoined_rows))


def run(
    *,
    source: str = "cod-ab",
    psgc_path: Path = DEFAULT_PSGC,
    geojson_path: Path = DEFAULT_GEOJSON,
    cod_ab_dir: Path = psgc_geodata.DEFAULT_COD_AB_DIR,
    out_path: Path | None = None,
    shapes_path: Path = DEFAULT_SHAPES,
    levels: list[str] | None = None,
    report_dir: Path = DEFAULT_REPORT_DIR,
    keep_staged: bool = False,
) -> Path:
    out_path = out_path or (DEFAULT_PSGC.parent / "psgc_geo.ttl")
    staged_path = out_path.with_name(f"{out_path.stem}.staged{out_path.suffix}")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    log.info("=== PSGC geodata pipeline start (source: %s) ===", source)

    log.info("Step 1/4: Loading base graph %s", psgc_path)
    g = Graph()
    g.parse(psgc_path, format="turtle")

    log.info("Step 2/4: Computing and joining centroids")
    if source == "geojson":
        joined = _joined_from_geojson(g, geojson_path, levels or list(psgc_geodata.DEFAULT_LEVELS))
    elif source == "cod-ab":
        if levels:
            log.warning("--levels is ignored for the cod-ab source")
        joined = _joined_from_cod_ab(g, cod_ab_dir, report_dir)
    else:
        raise ValueError(f"unknown source: {source}")

    log.info("Step 3/4: Emitting geometry triples")
    geo_graph = psgc_geodata.emit_geometry_graph(g, joined)

    geo_graph.serialize(destination=str(staged_path), format="turtle")
    log.info("Wrote staged geodata RDF: %s", staged_path)

    log.info("Validating staged geodata RDF against %s", shapes_path)
    staged = Graph()
    staged.parse(staged_path, format="turtle")
    try:
        validate_graph(
            staged,
            label=str(staged_path),
            shapes_graph=shapes_path,
            ontology_graph=None,
            include_context_graphs=False,
            include_default_context=False,
        )
    except ShaclValidationError:
        log.error("Validation failed; geodata output not published: %s", out_path)
        raise
    log.info("SHACL validation passed")

    log.info("Step 4/4: Promoting staged RDF to %s", out_path)
    if keep_staged:
        shutil.copyfile(staged_path, out_path)
    else:
        staged_path.replace(out_path)

    log.info("=== PSGC geodata pipeline complete ===")
    return out_path


def _main() -> int:
    parser = argparse.ArgumentParser(description="Enrich PSGC RDF with centroids.")
    parser.add_argument(
        "--source",
        choices=("cod-ab", "geojson"),
        default="cod-ab",
        help="Boundary source (default: cod-ab).",
    )
    parser.add_argument(
        "--levels",
        type=lambda s: [p.strip() for p in s.split(",") if p.strip()],
        default=None,
        help="geojson source only: comma-separated PSGC levels (default: Prov,City,Mun).",
    )
    parser.add_argument("--keep-staged", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    )
    run(source=args.source, levels=args.levels, keep_staged=args.keep_staged)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
