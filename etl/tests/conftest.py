"""Shared set-up for the SakunaGraPH test suite.

Run from etl/:
    python -m pytest tests                    # fast tests only (default)
    python -m pytest tests -m pipeline        # loads PSGC / matcher / classifier (~1-3 min)
    python -m pytest tests -m slow            # full PSGC geodata checks (>10 min)
    python -m pytest tests -m "not network"   # everything except tests that need the internet
    python -m pytest tests --all              # every marker

Markers are declared in pytest.ini. Tests that need generated data
(psgc.ttl, psgc_geo.ttl, parsed DROMIC folders) skip with a reason when the
file is missing instead of failing.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ETL_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = ETL_DIR.parent
DATA_DIR = ROOT_DIR / "data"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

if str(ETL_DIR) not in sys.path:
    sys.path.insert(0, str(ETL_DIR))
# Pipeline modules use paths relative to etl/ (../data/...).
os.chdir(ETL_DIR)

PSGC_TTL = DATA_DIR / "rdf" / "psgc" / "psgc.ttl"
PSGC_GEO_TTL = DATA_DIR / "rdf" / "psgc" / "psgc_geo.ttl"
PARSED_2026 = DATA_DIR / "parsed" / "dromic" / "2026"
SAMPLE_EVENTS = (
    "Effects of El Nino in Western Visayas 20261117",
    "Fire Incident in Brgy. E. Rodriguez Quezon City 20261115",
    "Mayon Volcano Seismic Activities 20261117",
)

HEAVY_MARKERS = ("pipeline", "slow", "network", "graphdb")


def pytest_addoption(parser):
    parser.addoption("--all", action="store_true", help="also run pipeline, slow, network and graphdb tests")


def pytest_collection_modifyitems(config, items):
    """Heavy tests run only when selected with -m or --all."""
    if config.getoption("--all") or config.getoption("-m"):
        return
    skip = pytest.mark.skip(reason="heavy test: select with -m <marker> or --all")
    for item in items:
        if any(m in item.keywords for m in HEAVY_MARKERS):
            item.add_marker(skip)


def require(path: Path, how: str):
    if not path.exists():
        pytest.skip(f"{path.relative_to(ROOT_DIR)} missing; build it with: {how}")


@pytest.fixture(scope="session")
def psgc_files():
    require(PSGC_TTL, "python -m pipeline.run_psgc --barangay")
    require(PSGC_GEO_TTL, "python -m pipeline.run_psgc_geo")
    return PSGC_TTL, PSGC_GEO_TTL


@pytest.fixture(scope="session")
def sample_event_dirs():
    dirs = [PARSED_2026 / name for name in SAMPLE_EVENTS]
    missing = [d.name for d in dirs if not (d / "metadata.json").exists()]
    if missing:
        pytest.skip(f"parsed sample events missing: {missing}")
    return dirs


@pytest.fixture
def feed_xml() -> bytes:
    return (FIXTURES / "dromic_feed_2026.xml").read_bytes()
