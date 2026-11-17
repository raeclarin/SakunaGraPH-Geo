"""Geodata enrichment for PSGC administrative units.

Computes representative coordinates (centroids) for PSGC individuals from
polygon boundary data (GeoJSON) and emits a separate RDF file
(`psgc_geo.ttl`) that is loaded alongside `psgc.ttl`.

Design notes
------------
- Coordinates are attached to PSGC location IRIs (not to events). Events
  inherit coordinates through :hasLocation, so every source benefits.
- Canonical form is GeoSPARQL (geo:hasGeometry / geo:asWKT, EPSG:4326,
  "POINT (lon lat)"); :latitude/:longitude are convenience duplicates.
- The join is PSGC-code based; a normalized-name fallback handles codes that
  changed with administrative reorganizations (e.g. Negros Island Region,
  Sulu's return to Region IX).
- Deterministic output: no randomness, no timestamps baked into triples.

Usage (from etl/):
    python -m pipeline.run_psgc_geo
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from rdflib import Graph, Literal, Namespace, RDF, RDFS, URIRef
from rdflib.namespace import SKOS, XSD
from shapely.geometry import shape

log = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parents[2]

DEFAULT_GEOJSON = ROOT_DIR / "frontend" / "static" / "data" / "regions.geojson"
DEFAULT_PSGC = ROOT_DIR / "data" / "rdf" / "psgc" / "psgc.ttl"
DEFAULT_OUT = ROOT_DIR / "data" / "rdf" / "psgc" / "psgc_geo.ttl"

SKG = Namespace("https://sakuna.ph/")
GEO = Namespace("http://www.opengis.net/ont/geosparql#")

# Levels eligible for a point coordinate from this dataset.
DEFAULT_LEVELS = ("Prov", "City", "Mun")

# Higher-priority levels come first when a normalized name is ambiguous
# (e.g. Siquijor is both a province and its capital municipality).
LEVEL_PRIORITY = {"Reg": 0, "Prov": 1, "City": 2, "Mun": 3, "SubMun": 4, "Bgy": 5}

# PSGC codes that administrative-boundary datasets carry under older or
# alternate numbering. Maps boundary-dataset code -> current PSGC code.
# Document every entry: these are data-quality drift, not ontology facts.
ALIAS_CODES = {
    # GeoJSON (pre-2025 vintage) numbers Cotabato City 1909900000;
    # PSGC 3Q-2026 lists "City of Cotabato" as 1908703000.
    "1909900000": "1908703000",
}

# Feature properties tried, in order, for the PSGC code.
CODE_PROPERTIES = ("adm2_psgc", "adm3_psgc", "psgc", "code")
NAME_PROPERTIES = ("adm2_en", "adm3_en", "name", "NAME_2", "NAME_3")

_DECIMALS = 6


@dataclass(frozen=True)
class Centroid:
    code: str
    lon: float
    lat: float
    name: str = ""          # normalized display name for fallback joins
    matched_by: str = "code"


# ---------------------------------------------------------------------------
# GeoJSON parsing
# ---------------------------------------------------------------------------

def _normalize_code(value: str | None) -> str | None:
    """Zero-pad PSGC codes; tolerate 9-digit artifacts in boundary datasets."""
    if value is None:
        return None
    digits = re.sub(r"\D", "", str(value))
    if len(digits) in (9, 10):
        return digits.zfill(10)
    return None


def _normalize_name(value: str | None) -> str:
    """Loose key for name-based joins: lowercase, ASCII, no honorifics, no
    parenthetical qualifiers such as "(Not a Province)"."""
    if not value:
        return ""
    text = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    text = re.sub(r"\([^)]*\)", " ", text)
    text = text.lower()
    text = re.sub(r"\b(province of|city of|municipality of)\b", "", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return text.strip()


def load_centroids(
    geojson_path: Path,
    levels: Iterable[str] = DEFAULT_LEVELS,
) -> tuple[dict[str, Centroid], list[dict]]:
    """Compute one centroid per GeoJSON feature.

    Returns (centroids_by_code, unmatched_features).
    """
    levels = set(levels)
    with geojson_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    centroids: dict[str, Centroid] = {}
    unmatched: list[dict] = []

    for feature in data.get("features", []):
        props = feature.get("properties", {})
        code = next(
            (_normalize_code(props.get(p)) for p in CODE_PROPERTIES if props.get(p)),
            None,
        )
        geom = shape(feature["geometry"]) if feature.get("geometry") else None
        if geom is None or geom.is_empty:
            unmatched.append({**props, "reason": "no geometry"})
            continue

        # Largest part wins (island provinces, MultiPolygons).
        parts = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
        main = max(parts, key=lambda part: part.area)
        point = main.centroid
        if not main.covers(point):
            point = main.representative_point()
        lon = round(point.x, _DECIMALS)
        lat = round(point.y, _DECIMALS)

        if code is None:
            name = next((props.get(p) for p in NAME_PROPERTIES if props.get(p)), None)
            unmatched.append({**props, "reason": "no PSGC code"})
            continue

        if code in centroids:
            unmatched.append({**props, "reason": "duplicate code"})
            continue

        display_name = next(
            (props.get(p) for p in NAME_PROPERTIES if props.get(p)), ""
        )
        centroids[code] = Centroid(
            code=code,
            lon=lon,
            lat=lat,
            name=_normalize_name(display_name),
        )

    log.info("Computed %d centroids from %s", len(centroids), geojson_path.name)
    return centroids, unmatched


# ---------------------------------------------------------------------------
# RDF join and emission
# ---------------------------------------------------------------------------

def _label_candidates(g: Graph, subject: URIRef) -> set[str]:
    keys = {_normalize_name(str(o)) for o in g.objects(subject, RDFS.label)}
    keys |= {_normalize_name(str(o)) for o in g.objects(subject, SKOS.altLabel)}
    keys.discard("")
    return keys


def _level_of(g: Graph, subject: URIRef) -> str:
    level = g.value(subject, SKG.geographicLevel)
    return str(level) if level is not None else "Bgy"


def join_centroids(
    g: Graph,
    centroids: dict[str, Centroid],
    levels: Iterable[str] = DEFAULT_LEVELS,
) -> tuple[dict[URIRef, Centroid], list[Centroid]]:
    """Attach centroids to PSGC individuals, by code first then by label.

    Code lookup tries the feature code, then ALIAS_CODES remapping. Name
    lookup resolves ambiguities by administrative-level priority (province
    before its same-named capital municipality).
    """
    levels = set(levels)
    by_code: dict[str, URIRef] = {}
    by_name: dict[str, list[URIRef]] = {}

    for s, _p, level in g.triples((None, SKG.geographicLevel, None)):
        if str(level) not in levels:
            continue
        code = g.value(s, SKG.psgcCode)
        if code is not None:
            by_code[str(code)] = s
        for key in _label_candidates(g, s):
            by_name.setdefault(key, []).append(s)

    joined: dict[URIRef, Centroid] = {}
    unmatched: list[Centroid] = []

    for code, centroid in centroids.items():
        subject = by_code.get(code) or by_code.get(ALIAS_CODES.get(code, ""))
        if subject is not None:
            joined[subject] = centroid
            continue

        # Fallback for pre-reorganization codes (e.g. Negros provinces under
        # old region codes, Sulu before its return to Region IX): retry on
        # the normalized feature name. Disambiguation order:
        #   1. candidates sharing the feature code's region prefix
        #      ("Isabela" the city in Basilan, code prefix 09, must not match
        #      "Isabela" the province in Cagayan Valley, code prefix 02)
        #   2. administrative-level priority (Siquijor: province beats its
        #      same-named capital municipality)
        candidates = by_name.get(centroid.name, [])
        region_prefix = centroid.code[:2]
        by_prefix = [s for s in candidates if str(g.value(s, SKG.psgcCode) or "").startswith(region_prefix)]
        if len(by_prefix) == 1:
            joined[by_prefix[0]] = Centroid(
                **{**centroid.__dict__, "matched_by": "name"}
            )
            continue

        for pool in (by_prefix or candidates, candidates):
            if not pool:
                continue
            best = min(
                pool,
                key=lambda s: LEVEL_PRIORITY.get(_level_of(g, s), 99),
            )
            ties = [
                s for s in pool
                if LEVEL_PRIORITY.get(_level_of(g, s), 99)
                == LEVEL_PRIORITY.get(_level_of(g, best), 99)
            ]
            if len(ties) == 1:
                joined[best] = Centroid(
                    **{**centroid.__dict__, "matched_by": "name"}
                )
                break
        else:
            unmatched.append(centroid)

    return joined, unmatched


def emit_geometry_graph(
    g_psgc: Graph,
    joined: dict[URIRef, Centroid],
) -> Graph:
    """Build the psgc_geo.ttl graph: geometry + convenience coordinates."""
    out = Graph()
    out.bind("geo", GEO)
    out.bind("skg", SKG)

    for subject, centroid in sorted(joined.items(), key=lambda kv: str(kv[0])):
        wkt = Literal(f"POINT ({centroid.lon} {centroid.lat})", datatype=GEO.wktLiteral)
        geometry = URIRef(f"{subject}/geometry")
        out.add((subject, GEO.hasGeometry, geometry))
        out.add((geometry, RDF.type, GEO.Geometry))
        out.add((geometry, GEO.asWKT, wkt))
        out.add((subject, SKG.latitude, Literal(centroid.lat, datatype=XSD.decimal)))
        out.add((subject, SKG.longitude, Literal(centroid.lon, datatype=XSD.decimal)))

    log.info("Emitted geometry for %d PSGC individuals", len(joined))
    return out
