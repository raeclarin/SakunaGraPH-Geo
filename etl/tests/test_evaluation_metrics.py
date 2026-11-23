"""Accuracy measures used for the Geography comparison (fast, pure)."""

import pytest

from evaluation.metrics import (
    aggregate_rows,
    municipal_code,
    names_agree,
    normalise_code,
    parse_count,
    score_fields,
    score_locations,
)


@pytest.mark.parametrize("raw, expected", [
    ("0500503000", "0500503000"),
    ("500503000", "0500503000"),             # Excel dropped the leading zero
    ("https://sakuna.ph/0500503000", "0500503000"),
    ("", None), (None, None),
])
def test_normalise_code(raw, expected):
    assert normalise_code(raw) == expected


@pytest.mark.parametrize("code, expected", [
    ("0500503002", "0500503000"),            # barangay -> municipality
    ("1381300001", "1381300000"),            # QC barangay -> Quezon City
    ("1380601001", "1380601000"),            # Tondo barangay -> Tondo (SubMun)
    ("0500500000", "0500500000"),            # province unchanged
])
def test_municipal_code(code, expected):
    assert municipal_code(code) == expected


@pytest.mark.parametrize("raw, expected", [
    ("102,147", 102147), ("293 , 976", 293976), ("1,053¹", 1053), ("-", None), ("", None),
    (477, 477), ("111 ", 111),
])
def test_parse_count(raw, expected):
    assert parse_count(raw) == expected


def test_aggregate_merges_barangays_into_municipality():
    rows = [
        {"psgc_code": "0500503001", "affected_persons": "10"},
        {"psgc_code": "0500503002", "affected_persons": "5", "affected_families": "2"},
        {"psgc_code": "0500517000", "affected_persons": "-"},
    ]
    agg = aggregate_rows(rows, ("affected_persons", "affected_families"))
    assert agg["0500503000"] == {"affected_persons": 15, "affected_families": 2}
    assert agg["0500517000"] == {"affected_persons": None, "affected_families": None}


def test_location_scores():
    s = score_locations({"A", "B", "C", "D"}, {"A", "B", "C", "X"})
    assert (s.recall, s.precision) == (0.75, 0.75)
    assert s.missed == {"D"} and s.spurious == {"X"}
    assert score_locations(set(), {"A"}).recall is None


def test_field_scores_exact_and_relative_error():
    gold = {"A": {"affected_persons": 100}, "B": {"affected_persons": 50}, "C": {"affected_persons": None}}
    pipe = {"A": {"affected_persons": 100}, "B": {"affected_persons": 40}, "C": {"affected_persons": 7}}
    s = score_fields(gold, pipe, ["affected_persons"])["affected_persons"]
    assert s.compared == 2                   # C has no reference value: not compared
    assert s.exact_rate == 0.5
    assert s.median_relative_error == pytest.approx(0.1)
    assert s.mismatches == [("B", 50, 40)]


def test_missing_pipeline_value_is_full_error():
    s = score_fields({"A": {"f": 10}}, {"A": {"f": None}}, ["f"])["f"]
    assert s.relative_errors == [1.0] and s.exact == 0


def test_names_agree():
    assert names_agree("Mayon Volcano Seismic Activities", "mayon volcano seismic activities")
    assert not names_agree("Effects of El Nino", "Fire Incident in Brgy. Labo")
