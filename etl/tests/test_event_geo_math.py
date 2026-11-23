"""Event-centroid arithmetic on synthetic points (fast; no PSGC graph needed).

The real-PSGC version of these checks is validate/check_event_centroid.py,
run by test_pipeline_stages.py (marker: pipeline).
"""

from math import isclose

import pytest
from rdflib import URIRef

from semantic_processing.event_geo import (
    GeoIndex,
    coarsest_precision,
    compute_event_centroid,
    haversine_km,
    spherical_mean,
    split_location_iris,
)

B = "https://sakuna.ph/"
TOWN_A = URIRef(B + "0500503000")   # Daraga
TOWN_B = URIRef(B + "0500517000")   # Tabaco (City)
PROV = URIRef(B + "0500500000")     # Albay
BGY = URIRef(B + "0500503002")      # barangay of Daraga (no own point)
ORPHAN = URIRef(B + "9999999999")


@pytest.fixture
def index():
    points = {
        TOWN_A: (13.114813, 123.688861, "Municipality"),
        TOWN_B: (13.323441, 123.670091, "City"),
        PROV: (13.20, 123.60, "Province"),
    }
    parents = {BGY: TOWN_A, TOWN_A: PROV, TOWN_B: PROV}
    return GeoIndex(points, parents)


def test_haversine_known_distance():
    # Manila (14.5995, 120.9842) to Cebu City (10.3157, 123.8854): ~572 km
    assert isclose(haversine_km(14.5995, 120.9842, 10.3157, 123.8854), 572, abs_tol=5)
    assert haversine_km(13.1, 123.6, 13.1, 123.6) == 0


def test_spherical_mean_matches_plain_mean_at_ph_scale():
    pts = [(13.114813, 123.688861), (13.172739, 123.576793), (13.099321, 123.753188), (13.323441, 123.670091)]
    w = [4000, 3000, 2500, 1500]
    lat, lon = spherical_mean(pts, w)
    plain = (sum(a * x for (a, _), x in zip(pts, w)) / sum(w), sum(b * x for (_, b), x in zip(pts, w)) / sum(w))
    # For neighbouring towns the plain mean is ~1 m off (guide, Step 5d table).
    assert haversine_km(lat, lon, *plain) < 0.002
    # Worked example published in the paper and the partners' guide
    assert (round(lat, 6), round(lon, 6)) == (13.155547, 123.670364)


def test_single_location(index):
    c = compute_event_centroid(index, [(TOWN_A, {"affectedPersons": 5})])
    assert (c.lat, c.lon, c.method, c.spread_km) == (13.114813, 123.688861, "single-location", 0)


def test_barangay_walks_up_to_municipality(index):
    c = compute_event_centroid(index, [(BGY, {})])
    assert (c.lat, c.lon, c.precision) == (13.114813, 123.688861, "Municipality")


def test_three_to_one_weights(index):
    c = compute_event_centroid(index, [(TOWN_A, {"affectedPersons": 3}), (TOWN_B, {"affectedPersons": 1})])
    total = haversine_km(13.114813, 123.688861, 13.323441, 123.670091)
    assert isclose(haversine_km(c.lat, c.lon, 13.114813, 123.688861) / total, 0.25, abs_tol=0.005)
    assert c.method == "weighted-mean:affectedPersons"


def test_weight_field_must_cover_every_location(index):
    c = compute_event_centroid(index, [
        (TOWN_A, {"affectedPersons": 9, "affectedFamilies": 3}),
        (TOWN_B, {"affectedFamilies": 1}),
    ])
    assert c.weight_field == "affectedFamilies"
    c = compute_event_centroid(index, [(TOWN_A, {"affectedPersons": 9}), (TOWN_B, {"affectedFamilies": 1})])
    assert c.method == "unweighted-mean"


def test_zero_weight_does_not_count_as_present(index):
    c = compute_event_centroid(index, [(TOWN_A, {"affectedPersons": 0}), (TOWN_B, {"affectedPersons": 5})])
    assert c.method == "unweighted-mean"


def test_duplicate_locations_merge(index):
    c = compute_event_centroid(index, [
        (TOWN_A, {"affectedPersons": 1}), (TOWN_A, {"affectedPersons": 2}), (TOWN_B, {"affectedPersons": 1}),
    ])
    assert c.location_count == 2
    total = haversine_km(13.114813, 123.688861, 13.323441, 123.670091)
    assert isclose(haversine_km(c.lat, c.lon, 13.114813, 123.688861) / total, 0.25, abs_tol=0.005)


def test_unresolved_reported_and_all_unresolved_is_none(index):
    c = compute_event_centroid(index, [(TOWN_A, {}), (ORPHAN, {})])
    assert c.unresolved == [str(ORPHAN)]
    assert compute_event_centroid(index, [(ORPHAN, {})]) is None


def test_spread_is_farthest_point(index):
    c = compute_event_centroid(index, [(TOWN_A, {}), (TOWN_B, {})])
    assert isclose(c.spread_km, haversine_km(c.lat, c.lon, 13.114813, 123.688861), abs_tol=0.002)


@pytest.mark.parametrize("levels, expected", [
    (["City", "Municipality"], "Municipality"),
    (["Municipality", "City"], "Municipality"),
    (["City"], "City"),
    (["City", "Province"], "Province"),
    (["Region", "Province", "City"], "Region"),
    (["SubMunicipality", "Barangay"], "SubMunicipality"),
])
def test_coarsest_precision_is_order_independent(levels, expected):
    assert coarsest_precision(levels) == expected
    assert coarsest_precision(reversed(levels)) == expected


def test_cycle_in_parents_does_not_hang():
    a, b = URIRef(B + "1"), URIRef(B + "2")
    assert GeoIndex({}, {a: b, b: a}).resolve(a) is None


def test_split_location_iris():
    assert split_location_iris(f"{B}1| {B}2 |") == [URIRef(B + "1"), URIRef(B + "2")]
    assert split_location_iris(None) == []


def test_plain_mean_drifts_for_country_wide_events():
    """Why the guide asks annotators for the spherical method (Step 5d)."""
    pts = [(20.45, 121.97), (5.05, 119.77), (7.07, 125.61), (14.6, 120.98), (10.3, 123.9), (18.2, 120.6)]
    lat, lon = spherical_mean(pts)
    plain = (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
    assert 1.0 < haversine_km(lat, lon, *plain) < 2.0
