"""Pure accuracy measures: pipeline output vs a hand-built reference.

No pipeline imports here, so these functions are cheap to unit-test
(etl/tests/test_evaluation_metrics.py). compare.py feeds them real data.

Unit of comparison for locations: the municipality/city-level PSGC code
(`municipal_code`). A barangay row is compared through its municipality,
because that is the precision of the event centroid (notes.md §8.16-8.17).
Rows at province or region level keep their own code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from math import asin, cos, radians, sin, sqrt
from statistics import median
from typing import Iterable, Mapping

COUNT_FIELDS = (
    "affected_barangays", "affected_families", "affected_persons",
    "displaced_families", "displaced_persons",
)
HOUSING_FIELDS = ("houses_totally_damaged", "houses_partially_damaged")
_EARTH_KM = 6371.0088


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * _EARTH_KM * asin(sqrt(a))


def normalise_code(code: str | None) -> str | None:
    """'PSGC 0500503000', '500503000', 'https://sakuna.ph/0500503000' -> '0500503000'."""
    if code is None:
        return None
    digits = re.sub(r"\D", "", str(code).rsplit("/", 1)[-1])
    if not digits:
        return None
    return digits.zfill(10)[:10]


def municipal_code(code: str | None) -> str | None:
    """Barangay code -> its city/municipality code; other levels unchanged.

    PSGC 10 digits: RR PPP MM BBB (region 2, province 3, municipality 2,
    barangay 3). A barangay has a non-zero last-3 group.
    """
    code = normalise_code(code)
    if code is None:
        return None
    if code[-3:] != "000":
        return code[:7] + "000"
    return code


def parse_count(value) -> int | None:
    """'102,147' / '102 , 147' / '1,053¹' / '' / '-' -> int or None."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if text in ("", "-", "–", "—"):
        return None
    # [0-9], not \d: \d also matches superscript footnote marks ("1,053¹").
    digits = re.sub(r"[^0-9]", "", text.split(".")[0])
    return int(digits) if digits else None


def aggregate_rows(rows: Iterable[Mapping], fields: Iterable[str] = COUNT_FIELDS + HOUSING_FIELDS) -> dict[str, dict[str, int | None]]:
    """Merge rows by municipal_code; sum each field (None if no row has it)."""
    fields = tuple(fields)
    out: dict[str, dict[str, int | None]] = {}
    for row in rows:
        key = municipal_code(row.get("psgc_code"))
        if key is None:
            continue
        acc = out.setdefault(key, {f: None for f in fields})
        for f in fields:
            v = parse_count(row.get(f))
            if v is not None:
                acc[f] = (acc[f] or 0) + v
    return out


@dataclass
class LocationScore:
    gold: set[str]
    pipeline: set[str]

    @property
    def matched(self) -> set[str]:
        return self.gold & self.pipeline

    @property
    def missed(self) -> set[str]:      # in the reference, not found by the pipeline
        return self.gold - self.pipeline

    @property
    def spurious(self) -> set[str]:    # produced by the pipeline, not in the reference
        return self.pipeline - self.gold

    @property
    def recall(self) -> float | None:
        return len(self.matched) / len(self.gold) if self.gold else None

    @property
    def precision(self) -> float | None:
        return len(self.matched) / len(self.pipeline) if self.pipeline else None

    @property
    def f1(self) -> float | None:
        p, r = self.precision, self.recall
        if not p or not r:
            return 0.0 if (p is not None and r is not None) else None
        return 2 * p * r / (p + r)


def score_locations(gold: Iterable[str], pipeline: Iterable[str]) -> LocationScore:
    return LocationScore({c for c in gold if c}, {c for c in pipeline if c})


@dataclass
class FieldScore:
    field: str
    compared: int = 0
    exact: int = 0
    relative_errors: list[float] = field(default_factory=list)
    mismatches: list[tuple[str, int | None, int | None]] = field(default_factory=list)

    @property
    def exact_rate(self) -> float | None:
        return self.exact / self.compared if self.compared else None

    @property
    def median_relative_error(self) -> float | None:
        return median(self.relative_errors) if self.relative_errors else None


def score_fields(
    gold: Mapping[str, Mapping[str, int | None]],
    pipeline: Mapping[str, Mapping[str, int | None]],
    fields: Iterable[str] = COUNT_FIELDS + HOUSING_FIELDS,
) -> dict[str, FieldScore]:
    """Compare counts for locations present on both sides.

    A field is compared when the reference has a value for it. A missing
    pipeline value counts as a mismatch with relative error 1.0.
    """
    scores = {f: FieldScore(f) for f in fields}
    for code in sorted(set(gold) & set(pipeline)):
        for f, s in scores.items():
            g = gold[code].get(f)
            if g is None:
                continue
            p = pipeline[code].get(f)
            s.compared += 1
            if p == g:
                s.exact += 1
                s.relative_errors.append(0.0)
                continue
            s.mismatches.append((code, g, p))
            if p is None:
                s.relative_errors.append(1.0)
            else:
                s.relative_errors.append(abs(p - g) / g if g else 1.0)
    return scores


def names_agree(a: str | None, b: str | None, threshold: int = 90) -> bool:
    if not a or not b:
        return False
    try:
        from rapidfuzz import fuzz
        return fuzz.token_set_ratio(a.lower(), b.lower()) >= threshold
    except ImportError:  # rapidfuzz is a pipeline dependency; fallback for bare envs
        norm = lambda s: " ".join(re.findall(r"[a-z0-9]+", s.lower()))
        return norm(a) == norm(b)


def fmt_rate(x: float | None) -> str:
    return "n/a" if x is None else f"{100 * x:.1f}%"
