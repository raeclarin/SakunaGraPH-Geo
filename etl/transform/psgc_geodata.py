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
    precision: str = ""     # admin level the point was computed from
    source: URIRef | None = None  # dataset IRI for prov:wasDerivedFrom


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
    """Build the psgc_geo.ttl graph: geometry + convenience coordinates.

    When a centroid carries `precision` / `source`, the geometry node also
    gets :geoPrecision and prov:wasDerivedFrom, and the dataset itself is
    described once as a prov:Entity.
    """
    out = Graph()
    out.bind("geo", GEO)
    out.bind("skg", SKG)
    out.bind("prov", PROV)

    sources: set[URIRef] = set()
    for subject, centroid in sorted(joined.items(), key=lambda kv: str(kv[0])):
        wkt = Literal(f"POINT ({centroid.lon} {centroid.lat})", datatype=GEO.wktLiteral)
        geometry = URIRef(f"{subject}/geometry")
        out.add((subject, GEO.hasGeometry, geometry))
        out.add((geometry, RDF.type, GEO.Geometry))
        out.add((geometry, GEO.asWKT, wkt))
        if centroid.precision:
            # Plain literal (= xsd:string in RDF 1.1); a typed literal fails
            # pySHACL's sh:in comparison against the plain list values.
            out.add((geometry, SKG.geoPrecision, Literal(centroid.precision)))
        if centroid.source is not None:
            out.add((geometry, PROV.wasDerivedFrom, centroid.source))
            sources.add(centroid.source)
        out.add((subject, SKG.latitude, Literal(centroid.lat, datatype=XSD.decimal)))
        out.add((subject, SKG.longitude, Literal(centroid.lon, datatype=XSD.decimal)))

    for source in sources:
        meta = DATASETS.get(source)
        out.add((source, RDF.type, PROV.Entity))
        if meta:
            out.add((source, RDFS.label, Literal(meta["label"])))
            out.add((source, RDFS.comment, Literal(meta["comment"])))
            out.add((source, RDFS.seeAlso, URIRef(meta["url"])))

    log.info("Emitted geometry for %d PSGC individuals", len(joined))
    return out


# ---------------------------------------------------------------------------
# HDX COD-AB source (municipality/city + province polygons)
# ---------------------------------------------------------------------------
#
# COD-AB pcodes are "PH" + the PSGC digits without trailing zeros
# (PH0102801 = PSGC 0102801000; barangay PH0102801001 = 0102801001).
#
# Join chain for each boundary feature, in order:
#   1. code    - pcode == PSGC code AND the names agree
#   2. recode  - region re-code (REGION_RECODES) AND the names agree
#   3. name    - unique normalized-name match among still-unclaimed PSGC
#                units of the same region (e.g. HUCs that COD-AB files
#                under their province, renumbered Maguindanao towns)
# Then, for PSGC units that still have no point:
#   4. parent       - SubMunicipality inherits its parent city's point
#                     (precision = parent's level, e.g. Manila districts)
#   5. bgy-dissolve - union of the unit's barangay polygons (ADM4), for
#                     municipalities created after the COD-AB vintage
#                     (BARMM Special Geographic Area towns)
#
# The name check in steps 1-2 matters: Maguindanao del Norte/Sur were
# renumbered after the COD-AB vintage, so 16 COD-AB codes now belong to a
# *different* PSGC municipality. A code-only join silently misplaces them.

DEFAULT_COD_AB_DIR = ROOT_DIR / "data" / "raw" / "boundaries" / "cod-ab"
PROV = Namespace("http://www.w3.org/ns/prov#")

COD_AB_DATASET = SKG["dataset/cod-ab-phl-v03"]
GEOJSON_DATASET = SKG["dataset/frontend-regions-geojson"]
DATASETS = {
    COD_AB_DATASET: {
        "label": "HDX COD-AB Philippines administrative boundaries v03",
        "comment": (
            "OCHA Common Operational Dataset - Administrative Boundaries, "
            "version v03, boundaries valid on 2025-02-13, HDX release May 2026. "
            "CRS EPSG:4326. Centroids computed in EPSG:32651."
        ),
        "url": "https://data.humdata.org/dataset/cod-ab-phl",
    },
    GEOJSON_DATASET: {
        "label": "Sakuna.PH frontend province boundaries (regions.geojson)",
        "comment": "Province polygons bundled with the frontend map, keyed by adm2_psgc.",
        "url": "https://sakuna.ph/frontend/static/data/regions.geojson",
    },
}

# Region re-codes between the COD-AB vintage and PSGC 3Q-2026, as 4-digit
# code prefixes (region + province). Document every entry.
REGION_RECODES = {
    "0604": "1804",  # Negros Occidental: Region VI -> Negros Island Region
    "0704": "1804",  # Negros Oriental: Region VII -> NIR (province 046 kept)
    "0706": "1806",  # Siquijor: Region VII -> NIR
    "1906": "0906",  # Sulu: BARMM -> Region IX (2024 Supreme Court ruling)
}

# Extra names for PSGC units whose PSA label is unusable for matching.
NAME_ALIASES = {
    "0431200000": ("lucena",),  # PSA 3Q-2026 label is "City of Lucenaa/" (typo)
}

# PSGC codes where code agrees but name does not, accepted as renames.
# Each needs a source; unconfirmed entries should stay out.
CONFIRMED_RENAMES: dict[str, str] = {
    # Same code, and PSGC 3Q-2026 has no other San Isidro in Davao del Norte,
    # so this is a rename, not a renumbering.
    "1102324000": "COD-AB 'San Isidro' = PSGC 'Sawata' (Davao del Norte)",
}

NAME_MATCH_MIN = 85          # rapidfuzz token_set_ratio for code+name agreement
PROJECTED_CRS = "EPSG:32651"  # UTM 51N; covers PH apart from far-west Palawan

PRECISION_BY_LEVEL = {
    "Reg": "Region",
    "Prov": "Province",
    "City": "City",
    "Mun": "Municipality",
    "SubMun": "SubMunicipality",
    "Bgy": "Barangay",
}


@dataclass
class PsgcUnit:
    iri: URIRef
    code: str
    level: str
    label: str
    names: set[str]
    parent: URIRef | None


@dataclass
class PointRecord:
    """One computed point plus everything the coverage report needs."""
    unit: PsgcUnit
    lon: float
    lat: float
    matched_by: str
    precision: str
    source_code: str = ""
    source_name: str = ""
    ref_lat: float | None = None   # COD-AB's own center_lat, for cross-check
    ref_lon: float | None = None

    def as_centroid(self) -> Centroid:
        return Centroid(
            code=self.unit.code,
            lon=self.lon,
            lat=self.lat,
            name=self.source_name,
            matched_by=self.matched_by,
            precision=self.precision,
            source=COD_AB_DATASET,
        )


def pcode_to_psgc(pcode: str) -> str:
    """'PH0102801' -> '0102801000'; barangay 'PH0102801001' -> '0102801001'."""
    digits = re.sub(r"\D", "", str(pcode))
    return digits.ljust(10, "0")


def _match_key(value: str | None) -> str:
    """Name key for COD-AB <-> PSGC: like _normalize_name, but also drops
    'city' and '(Capital)' so 'Angeles City' == 'City of Angeles'."""
    text = _normalize_name(value)
    text = re.sub(r"\b(city|capital)\b", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def index_psgc(g: Graph) -> dict[str, PsgcUnit]:
    """PSGC code -> unit, with every label/altLabel as a match key."""
    units: dict[str, PsgcUnit] = {}
    for s, _p, level in g.triples((None, SKG.geographicLevel, None)):
        code = g.value(s, SKG.psgcCode)
        if code is None:
            continue
        code = str(code)
        names = {_match_key(str(o)) for o in g.objects(s, RDFS.label)}
        names |= {_match_key(str(o)) for o in g.objects(s, SKOS.altLabel)}
        names |= set(NAME_ALIASES.get(code, ()))
        names.discard("")
        parent = g.value(s, SKG.isPartOf)
        units[code] = PsgcUnit(
            iri=s,
            code=code,
            level=str(level),
            label=str(g.value(s, RDFS.label) or ""),
            names=names,
            parent=parent if isinstance(parent, URIRef) else None,
        )
    return units


def _points_from_geoseries(geoms):
    """Largest-part centroid per geometry, computed in PROJECTED_CRS.

    Returns a list of (lon, lat) in EPSG:4326. Falls back to
    representative_point() when the centroid lies outside the part
    (crescent-shaped coasts, bays).
    """
    import geopandas as gpd

    projected = geoms.to_crs(PROJECTED_CRS)
    points = []
    for geom in projected:
        if geom is None or geom.is_empty:
            points.append(None)
            continue
        parts = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
        main = max(parts, key=lambda part: part.area)
        point = main.centroid
        if not main.covers(point):
            point = main.representative_point()
        points.append(point)
    back = gpd.GeoSeries(
        [p for p in points if p is not None], crs=PROJECTED_CRS
    ).to_crs("EPSG:4326")
    it = iter(back)
    result = []
    for p in points:
        if p is None:
            result.append(None)
        else:
            q = next(it)
            result.append((round(q.x, _DECIMALS), round(q.y, _DECIMALS)))
    return result


def read_cod_ab_layer(cod_ab_dir: Path, adm: int, where: str | None = None):
    """Read phl_admin{adm}.shp; returns a GeoDataFrame with code/name columns."""
    import geopandas as gpd

    path = cod_ab_dir / "shp" / f"phl_admin{adm}.shp"
    gdf = gpd.read_file(path, engine="pyogrio", where=where)
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")
    gdf["pcode"] = gdf[f"adm{adm}_pcode"].astype(str)
    gdf["psgc"] = gdf["pcode"].map(pcode_to_psgc)
    gdf["name"] = gdf[f"adm{adm}_name"].astype(str)
    return gdf


def _names_agree(feature_name: str, unit: PsgcUnit) -> bool:
    from rapidfuzz import fuzz

    key = _match_key(feature_name)
    if not key or not unit.names:
        return False
    return max(fuzz.token_set_ratio(key, n) for n in unit.names) >= NAME_MATCH_MIN


def join_cod_ab(
    gdf,
    units: dict[str, PsgcUnit],
    levels: Iterable[str],
) -> tuple[dict[str, PointRecord], list[dict]]:
    """Join one COD-AB layer to PSGC units of the given levels.

    Returns (records by PSGC code, unjoined feature rows).
    """
    levels = set(levels)
    targets = {c: u for c, u in units.items() if u.level in levels}
    coords = _points_from_geoseries(gdf.geometry)

    records: dict[str, PointRecord] = {}
    deferred: list[int] = []

    def _record(i: int, unit: PsgcUnit, how: str) -> None:
        row = gdf.iloc[i]
        lon, lat = coords[i]
        records[unit.code] = PointRecord(
            unit=unit,
            lon=lon,
            lat=lat,
            matched_by=how,
            precision=PRECISION_BY_LEVEL.get(unit.level, unit.level),
            source_code=row["pcode"],
            source_name=row["name"],
            ref_lat=_float(row.get("center_lat")),
            ref_lon=_float(row.get("center_lon")),
        )

    # Passes 1-2: code (or region re-code) with name agreement.
    for i, row in enumerate(gdf.itertuples(index=False)):
        if coords[i] is None:
            deferred.append(i)
            continue
        code = row.psgc
        recoded = REGION_RECODES.get(code[:4], code[:4]) + code[4:]
        for candidate, how in ((code, "code"), (recoded, "recode")):
            unit = targets.get(candidate)
            if unit is None or unit.code in records:
                continue
            if _names_agree(row.name, unit) or unit.code in CONFIRMED_RENAMES:
                _record(i, unit, how)
                break
            log.info(
                "Code %s matches PSGC '%s' but COD-AB says '%s'; trying name match",
                candidate, unit.label, row.name,
            )
        else:
            deferred.append(i)

    # Pass 3: unique name match within the region, among unclaimed units.
    unjoined: list[dict] = []
    for i in deferred:
        row = gdf.iloc[i]
        if coords[i] is None:
            unjoined.append({"pcode": row["pcode"], "name": row["name"], "reason": "no geometry"})
            continue
        code = row["psgc"]
        region = REGION_RECODES.get(code[:4], code[:4])[:2]
        key = _match_key(row["name"])
        pool = [
            u for u in targets.values()
            if u.code not in records and u.code.startswith(region) and key in u.names
        ]
        if len(pool) == 1:
            _record(i, pool[0], "name")
        else:
            unjoined.append({
                "pcode": row["pcode"],
                "name": row["name"],
                "reason": "ambiguous name" if pool else "no PSGC match",
            })

    return records, unjoined


def fill_gaps(
    records: dict[str, PointRecord],
    units: dict[str, PsgcUnit],
    levels: Iterable[str],
    cod_ab_dir: Path,
    unjoined_adm3: list[dict],
) -> list[PsgcUnit]:
    """Steps 4-5: parent inheritance, then barangay dissolve.

    Returns the PSGC units that still have no point.
    """
    import geopandas as gpd
    from shapely.ops import unary_union

    levels = set(levels)
    by_iri = {u.iri: u for u in units.values()}
    missing = [u for c, u in units.items() if u.level in levels and c not in records]

    # Step 4: parent inheritance (SubMun -> City), non-recursive on purpose.
    still: list[PsgcUnit] = []
    for unit in missing:
        parent = by_iri.get(unit.parent) if unit.parent else None
        if unit.level == "SubMun" and parent and parent.code in records:
            p = records[parent.code]
            records[unit.code] = PointRecord(
                unit=unit, lon=p.lon, lat=p.lat, matched_by="parent",
                precision=p.precision, source_code=p.source_code,
                source_name=p.source_name, ref_lat=p.ref_lat, ref_lon=p.ref_lon,
            )
        else:
            still.append(unit)
    if not still:
        return []

    # Step 5: barangay dissolve. Candidate barangays: exact barangay code,
    # or (renumbered barangays) any barangay inside a COD-AB ADM3 feature
    # that no PSGC unit claimed.
    children: dict[URIRef, list[PsgcUnit]] = {}
    for u in units.values():
        if u.level == "Bgy" and u.parent is not None:
            children.setdefault(u.parent, []).append(u)

    wanted_bgy = {b.code for unit in still for b in children.get(unit.iri, [])}
    free_adm3 = [r["pcode"] for r in unjoined_adm3]
    clauses = []
    if wanted_bgy:
        clauses.append("adm4_pcode IN ({})".format(
            ",".join(f"'PH{c}'" for c in sorted(wanted_bgy))))
    if free_adm3:
        clauses.append("adm3_pcode IN ({})".format(
            ",".join(f"'{p}'" for p in sorted(free_adm3))))
    if not clauses:
        return still
    adm4 = read_cod_ab_layer(cod_ab_dir, 4, where=" OR ".join(clauses))
    adm4["key"] = adm4["name"].map(_match_key)

    remaining: list[PsgcUnit] = []
    for unit in still:
        bgys = children.get(unit.iri, [])
        region = unit.code[:2]
        geoms = []
        for b in bgys:
            hit = adm4[adm4["psgc"] == b.code]
            if hit.empty:
                hit = adm4[
                    adm4["adm3_pcode"].isin(free_adm3)
                    & adm4["psgc"].str.startswith(region)
                    & adm4["key"].isin(b.names)
                ]
            if len(hit) == 1:
                geoms.append(hit.geometry.iloc[0])
        if not geoms:
            remaining.append(unit)
            continue
        merged = gpd.GeoSeries([unary_union(geoms)], crs=adm4.crs)
        lon, lat = _points_from_geoseries(merged)[0]
        records[unit.code] = PointRecord(
            unit=unit, lon=lon, lat=lat, matched_by="bgy-dissolve",
            precision=PRECISION_BY_LEVEL.get(unit.level, unit.level),
            source_name=f"{len(geoms)}/{len(bgys)} barangays",
        )
        log.info(
            "Dissolved %d of %d barangays for %s (%s)",
            len(geoms), len(bgys), unit.label, unit.code,
        )
    return remaining


def _float(value) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if f != f else f  # NaN -> None


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    from math import asin, cos, radians, sin, sqrt

    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * 6371.0088 * asin(sqrt(a))
