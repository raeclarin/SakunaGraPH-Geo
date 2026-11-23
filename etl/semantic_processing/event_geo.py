"""Event-level centroid: one representative point per disaster event.

An event's affected locations are PSGC IRIs (resolved by LOCATION_MATCHER).
Each PSGC unit already has a point in psgc_geo.ttl (COD-AB centroids). This
module aggregates those points into one event point.

Method
------
1. Resolve each location IRI to a point. A unit without its own point (e.g. a
   barangay) walks up :isPartOf to the nearest ancestor that has one; the
   point's precision is the level it actually came from.
2. Merge duplicate locations (same IRI): weights are summed.
3. Choose ONE weight field for the whole event, the first of WEIGHT_FIELDS
   that is a positive number for every location. Mixing fields (persons for
   one town, families for another) would make weights incomparable, so if no
   field covers every location, the centroid is unweighted.
4. Average on the sphere: convert each point to a 3-D unit vector, take the
   (weighted) mean vector, convert back to lat/lon. This is the standard
   geographic mean; it is exact regardless of map projection. A plain lat/lon
   mean differs by ~1 m for neighbouring towns, ~15 m across a region and up
   to ~1.7 km for a country-wide event (measured, notes.md §8.17).
5. Precision of the event point = the coarsest level among its inputs (an
   event with one province-only row is not a municipality-precise point).
6. Spread = the largest great-circle distance from the centroid to any input
   point; a simple measure of how dispersed the event is (0 for one town).

The unweighted point is always computed too, so the thesis can compare the
two (distance between them = how much weighting moved the point).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from math import asin, atan2, cos, degrees, hypot, radians, sin, sqrt
from pathlib import Path
from typing import Iterable, Mapping

from rdflib import Graph, Namespace, URIRef

log = logging.getLogger(__name__)

SKG = Namespace("https://sakuna.ph/")

ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_PSGC_GEO = ROOT_DIR / "data" / "rdf" / "psgc" / "psgc_geo.ttl"

# Weight fields tried in order; the first one present (> 0) for every
# location is used for the whole event.
WEIGHT_FIELDS = ("affectedPersons", "affectedFamilies", "displacedPersons", "displacedFamilies")

# Coarser levels rank higher.
PRECISION_RANK = {
    "SubMunicipality": 0,
    "Barangay": 0,
    "Municipality": 1,
    "City": 1,
    "Province": 2,
    "Region": 3,
}

# Tie-break among levels of equal rank, so the label never depends on input
# order (City and Municipality share a rank; a mix of both is "Municipality").
PRECISION_TIE_ORDER = ("Region", "Province", "Municipality", "City", "SubMunicipality", "Barangay")

METHOD_SINGLE = "single-location"
METHOD_UNWEIGHTED = "unweighted-mean"
METHOD_WEIGHTED = "weighted-mean:{field}"

_EARTH_KM = 6371.0088
_DECIMALS = 6


@dataclass(frozen=True)
class LocationPoint:
    iri: URIRef          # the location as given
    point_iri: URIRef    # the unit whose point was used (itself or an ancestor)
    lat: float
    lon: float
    precision: str


@dataclass
class EventCentroid:
    lat: float
    lon: float
    precision: str
    method: str
    location_count: int
    unweighted_lat: float
    unweighted_lon: float
    spread_km: float
    weight_field: str | None = None
    unresolved: list[str] = field(default_factory=list)

    @property
    def weighting_shift_km(self) -> float:
        return round(haversine_km(self.lat, self.lon, self.unweighted_lat, self.unweighted_lon), 3)


class GeoIndex:
    """PSGC IRI -> point, with :isPartOf fallback to the nearest ancestor."""

    def __init__(self, points: Mapping[URIRef, tuple[float, float, str]], parents: Mapping[URIRef, URIRef]):
        self._points = dict(points)
        self._parents = dict(parents)

    @classmethod
    def from_graphs(cls, psgc: Graph, psgc_geo: Graph) -> "GeoIndex":
        geo_ns = Namespace("http://www.opengis.net/ont/geosparql#")
        points: dict[URIRef, tuple[float, float, str]] = {}
        for s, _p, lat in psgc_geo.triples((None, SKG.latitude, None)):
            lon = psgc_geo.value(s, SKG.longitude)
            geom = psgc_geo.value(s, geo_ns.hasGeometry)
            precision = psgc_geo.value(geom, SKG.geoPrecision) if geom is not None else None
            if lon is None:
                continue
            points[s] = (float(lat), float(lon), str(precision or ""))
        parents = {
            s: o for s, _p, o in psgc.triples((None, SKG.isPartOf, None))
            if isinstance(o, URIRef)
        }
        log.info("GeoIndex: %d points, %d parent links", len(points), len(parents))
        return cls(points, parents)

    def resolve(self, iri: URIRef) -> LocationPoint | None:
        current: URIRef | None = iri
        seen: set[URIRef] = set()
        while current is not None and current not in seen:
            seen.add(current)
            hit = self._points.get(current)
            if hit is not None:
                lat, lon, precision = hit
                return LocationPoint(iri, current, lat, lon, precision)
            current = self._parents.get(current)
        return None


def split_location_iris(value) -> list[URIRef]:
    """A location value may hold several IRIs joined by '|' (DROMIC metadata)."""
    if value is None:
        return []
    return [URIRef(part.strip()) for part in str(value).split("|") if part.strip()]


def compute_event_centroid(
    index: GeoIndex,
    locations: Iterable[tuple[URIRef, Mapping[str, float | int | None]]],
) -> EventCentroid | None:
    """Aggregate (location IRI, counts) pairs into one event point.

    `counts` maps weight field names (see WEIGHT_FIELDS) to numbers or None.
    Returns None when no location resolves to a point.
    """
    merged: dict[URIRef, LocationPoint] = {}
    weights: dict[URIRef, dict[str, float]] = {}
    present: dict[URIRef, set[str]] = {}
    unresolved: list[str] = []

    for iri, counts in locations:
        point = index.resolve(iri)
        if point is None:
            unresolved.append(str(iri))
            continue
        merged.setdefault(iri, point)
        w = weights.setdefault(iri, {f: 0.0 for f in WEIGHT_FIELDS})
        has = present.setdefault(iri, set())
        for f in WEIGHT_FIELDS:
            value = counts.get(f) if counts else None
            if isinstance(value, (int, float)) and value > 0:
                w[f] += float(value)
                has.add(f)

    if not merged:
        return None

    pts = list(merged.values())
    u_lat, u_lon = spherical_mean([(p.lat, p.lon) for p in pts])

    weight_field = None
    if len(pts) > 1:
        weight_field = next(
            (f for f in WEIGHT_FIELDS if all(f in present[p.iri] for p in pts)),
            None,
        )

    if len(pts) == 1:
        lat, lon, method = pts[0].lat, pts[0].lon, METHOD_SINGLE
    elif weight_field:
        lat, lon = spherical_mean(
            [(p.lat, p.lon) for p in pts],
            [weights[p.iri][weight_field] for p in pts],
        )
        method = METHOD_WEIGHTED.format(field=weight_field)
    else:
        lat, lon, method = u_lat, u_lon, METHOD_UNWEIGHTED

    precision = coarsest_precision(p.precision for p in pts)
    spread = max(haversine_km(lat, lon, p.lat, p.lon) for p in pts)

    return EventCentroid(
        lat=round(lat, _DECIMALS),
        lon=round(lon, _DECIMALS),
        precision=precision,
        method=method,
        location_count=len(pts),
        unweighted_lat=round(u_lat, _DECIMALS),
        unweighted_lon=round(u_lon, _DECIMALS),
        spread_km=round(spread, 3),
        weight_field=weight_field,
        unresolved=unresolved,
    )


def coarsest_precision(levels: Iterable[str]) -> str:
    """Coarsest level among `levels`, deterministic regardless of order.

    Previously `max()` returned whichever tied level came first, so an event
    mixing cities and municipalities flipped between "City" and
    "Municipality" across identical runs (notes.md §8.15).
    """
    levels = set(levels)
    top = max(PRECISION_RANK.get(level, 99) for level in levels)
    tied = [level for level in levels if PRECISION_RANK.get(level, 99) == top]
    return min(
        tied,
        key=lambda level: PRECISION_TIE_ORDER.index(level) if level in PRECISION_TIE_ORDER else len(PRECISION_TIE_ORDER),
    )


def spherical_mean(
    points: list[tuple[float, float]],
    weights: list[float] | None = None,
) -> tuple[float, float]:
    """(Weighted) geographic mean via 3-D unit vectors. Points are (lat, lon)."""
    if weights is None:
        weights = [1.0] * len(points)
    x = y = z = 0.0
    for (lat, lon), w in zip(points, weights):
        la, lo = radians(lat), radians(lon)
        x += w * cos(la) * cos(lo)
        y += w * cos(la) * sin(lo)
        z += w * sin(la)
    return degrees(atan2(z, hypot(x, y))), degrees(atan2(y, x))


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * _EARTH_KM * asin(sqrt(a))


_INDEX: GeoIndex | None = None


def get_geo_index(psgc_graph: Graph | None = None, geo_path: Path = DEFAULT_PSGC_GEO) -> GeoIndex:
    """Process-wide singleton. Reuses LOCATION_MATCHER's PSGC graph when given."""
    global _INDEX
    if _INDEX is None:
        if psgc_graph is None:
            from semantic_processing.location_matcher_v2 import LOCATION_MATCHER
            psgc_graph = LOCATION_MATCHER.g
        geo = Graph()
        geo.parse(geo_path, format="turtle")
        _INDEX = GeoIndex.from_graphs(psgc_graph, geo)
    return _INDEX
