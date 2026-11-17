"""Orchestrate PSGC geodata enrichment: compute, join, validate, publish.

Reads the existing psgc.ttl, computes centroids for administrative units from
the boundary GeoJSON, and writes psgc_geo.ttl after SHACL validation. The
output is loaded into GraphDB alongside psgc.ttl — never merged into psgc.ttl
itself, so re-running this step never disturbs the base graph.

Usage (from etl/):
    python -m pipeline.run_psgc_geo [--levels Prov,City,Mun]
"""

from __future__ import annotations

import argparse
import logging
import shutil
from pathlib import Path

from rdflib import Graph

from transform import psgc_geodata
from validate.validate import ShaclValidationError, validate_graph

log = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_PSGC = ROOT_DIR / "data" / "rdf" / "psgc" / "psgc.ttl"
DEFAULT_GEOJSON = ROOT_DIR / "frontend" / "static" / "data" / "regions.geojson"
DEFAULT_SHAPES = ROOT_DIR / "ontology" / "shapes" / "psgc" / "shapes.ttl"


def run(
    *,
    psgc_path: Path = DEFAULT_PSGC,
    geojson_path: Path = DEFAULT_GEOJSON,
    out_path: Path | None = None,
    shapes_path: Path = DEFAULT_SHAPES,
    levels: list[str] | None = None,
    keep_staged: bool = False,
) -> Path:
    levels = levels or list(psgc_geodata.DEFAULT_LEVELS)
    out_path = out_path or (DEFAULT_PSGC.parent / "psgc_geo.ttl")
    staged_path = out_path.with_name(f"{out_path.stem}.staged{out_path.suffix}")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    log.info("=== PSGC geodata pipeline start ===")

    log.info("Step 1/4: Loading base graph %s", psgc_path)
    g = Graph()
    g.parse(psgc_path, format="turtle")

    log.info("Step 2/4: Computing centroids from %s", geojson_path)
    centroids, unmatched = psgc_geodata.load_centroids(geojson_path, levels=levels)
    for feature in unmatched:
        log.warning("Unmatched feature (%s): %s", feature.get("reason"), feature)

    joined, still_unmatched = psgc_geodata.join_centroids(g, centroids, levels=levels)
    for centroid in still_unmatched:
        log.warning(
            "No PSGC individual for centroid %s (%s)",
            centroid.code,
            centroid.name or "?",
        )
    log.info(
        "Joined %d of %d centroid features (by code or name)",
        len(joined),
        len(centroids),
    )

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
        "--levels",
        type=lambda s: [p.strip() for p in s.split(",") if p.strip()],
        default=None,
        help="Comma-separated PSGC levels to enrich (default: Prov,City,Mun).",
    )
    parser.add_argument("--keep-staged", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    )
    run(levels=args.levels, keep_staged=args.keep_staged)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
