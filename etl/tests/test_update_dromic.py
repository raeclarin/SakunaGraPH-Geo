"""Automation wrapper: supersede rule, lock, retry-after-failure (fast)."""

import csv
import json
import os
import time

import pytest

from pipeline import update_dromic as up


def make_event(root, folder, name="Mayon Volcano Seismic Activities", start="2026-10-30", report=0):
    d = root / folder
    d.mkdir(parents=True)
    (d / "metadata.json").write_text(json.dumps({"eventName": name, "startDate": start, "reportNumber": report}))
    return d


def active(root):
    return sorted(p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith("_"))


def test_supersede_keeps_newest_report_per_event(tmp_path):
    for day in ("20261115", "20261117", "20261121"):
        make_event(tmp_path, f"Mayon Volcano Seismic Activities {day}")
    make_event(tmp_path, "Fire Incident in Brgy. X 20261115", name="Fire Incident in Brgy. X", start="2026-11-14")
    moved = up.supersede_old_reports(tmp_path)
    assert sorted(moved) == ["Mayon Volcano Seismic Activities 20261115", "Mayon Volcano Seismic Activities 20261117"]
    assert active(tmp_path) == ["Fire Incident in Brgy. X 20261115", "Mayon Volcano Seismic Activities 20261121"]
    assert len(list((tmp_path / up.SUPERSEDED).iterdir())) == 2
    assert up.supersede_old_reports(tmp_path) == []           # idempotent


def test_same_name_different_start_date_are_different_events(tmp_path):
    make_event(tmp_path, "Flooding 20260110", name="Flooding", start="2026-01-08")
    make_event(tmp_path, "Flooding 20260720", name="Flooding", start="2026-07-15")
    assert up.supersede_old_reports(tmp_path) == []


def test_report_number_breaks_tie_without_dates(tmp_path):
    # Folders without a date suffix (report had no last-update date): report number decides.
    make_event(tmp_path, "Mayon A", report=110)
    make_event(tmp_path, "Mayon B", report=111)
    assert up.supersede_old_reports(tmp_path) == ["Mayon A"]


def test_dated_folder_beats_undated(tmp_path):
    make_event(tmp_path, "Mayon 20261117", report=0)
    make_event(tmp_path, "Mayon X", report=112)
    assert up.supersede_old_reports(tmp_path) == ["Mayon X"]


def test_superseded_name_clash_gets_suffix(tmp_path):
    (tmp_path / up.SUPERSEDED / "Mayon 20261115").mkdir(parents=True)
    make_event(tmp_path, "Mayon 20261115")
    make_event(tmp_path, "Mayon 20261117")
    up.supersede_old_reports(tmp_path)
    assert sorted(p.name for p in (tmp_path / up.SUPERSEDED).iterdir()) == ["Mayon 20261115", "Mayon 20261115_1"]


def test_dry_run_moves_nothing(tmp_path):
    make_event(tmp_path, "Mayon 20261115")
    make_event(tmp_path, "Mayon 20261117")
    assert up.supersede_old_reports(tmp_path, dry_run=True) == ["Mayon 20261115"]
    assert len(active(tmp_path)) == 2


@pytest.fixture
def logdir(tmp_path, monkeypatch):
    monkeypatch.setattr(up, "LOG_DIR", tmp_path)
    monkeypatch.setattr(up, "LOCK_FILE", tmp_path / "update_dromic.lock")
    monkeypatch.setattr(up, "RUNS_CSV", tmp_path / "update_runs.csv")
    return tmp_path


def test_lock_prevents_overlap_and_expires(logdir):
    assert up.acquire_lock()
    assert not up.acquire_lock()                               # second run exits
    old = time.time() - up.LOCK_STALE_SECONDS - 60
    os.utime(up.LOCK_FILE, (old, old))
    assert up.acquire_lock()                                   # stale lock taken over
    up.release_lock()
    assert not up.LOCK_FILE.exists()


def test_previous_run_failed_reads_last_real_run(logdir):
    assert not up.previous_run_failed()
    for status in ("ok", "failed"):
        rec = up.RunRecord(year=2026, repo="SakunaGraPH-dev", status=status)
        up.append_run(rec)
    assert up.previous_run_failed()
    up.append_run(up.RunRecord(year=2026, repo="SakunaGraPH-dev", status="ok"))
    assert not up.previous_run_failed()


def test_run_row_has_every_column(logdir):
    up.append_run(up.RunRecord(year=2026, repo="r", status="no-change"))
    with up.RUNS_CSV.open(encoding="utf-8") as f:
        row = next(csv.DictReader(f))
    assert list(row) == up.RUN_FIELDS and row["status"] == "no-change"


def test_repo_is_required():
    import sys
    from unittest import mock
    with mock.patch.object(sys, "argv", ["update_dromic"]), pytest.raises(SystemExit):
        up.main()


# ----------------------------------------------------------------------------
# Same-day reports (6AM and 6PM share a folder name) - notes.md §8.18
# ----------------------------------------------------------------------------

def test_same_day_report_replaces_older_one(tmp_path):
    parsed = tmp_path / "parsed"
    old = make_event(parsed, "Mayon 20261117", report=111)
    (old / "number_of_affected_population.csv").write_text("old")
    new = make_event(tmp_path / "staging", "Mayon 20261117", report=112)
    (new / "number_of_affected_population.csv").write_text("new")

    installed = up.install_parsed_folder(new, parsed)
    assert installed == parsed / "Mayon 20261117"
    assert (installed / "number_of_affected_population.csv").read_text() == "new"
    assert not (installed / "number_of_affected_population_1.csv").exists()   # no mixing
    assert (parsed / up.SUPERSEDED / "Mayon 20261117" / "number_of_affected_population.csv").read_text() == "old"


def test_older_same_day_report_goes_straight_to_archive(tmp_path):
    parsed = tmp_path / "parsed"
    make_event(parsed, "Mayon 20261117", report=112)
    late = make_event(tmp_path / "staging", "Mayon 20261117", report=111)   # e.g. a backfill
    up.install_parsed_folder(late, parsed)
    assert up._report_number(parsed / "Mayon 20261117") == 112
    assert up._report_number(parsed / up.SUPERSEDED / "Mayon 20261117") == 111


def test_assistance_failure_does_not_skip_event_next_run(tmp_path, monkeypatch):
    import importlib, sys, types
    # run_dromic imports the heavy transform/matcher modules; stub them for this unit test.
    for name in ("mappings.dromic", "mappings.graph", "transform.dromic", "validate.validate",
                 "semantic_processing.event_geo"):
        monkeypatch.setitem(sys.modules, name, types.SimpleNamespace(
            **{k: object for k in ("aff_pop_mapping", "assistance_mapping", "event_geometry_mapping",
                                   "event_mapping", "housing_mapping", "pevac_mapping", "prov_mapping",
                                   "Graph", "create_graph", "WEIGHT_FIELDS", "compute_event_centroid",
                                   "get_geo_index", "split_location_iris", "load_aff_pop",
                                   "load_assistance", "load_event", "load_housing", "load_provenance",
                                   "ShaclValidationError", "ShaclValidator", "validation_focus_nodes")},
            DEFAULT_CONTEXT_GRAPH_PATHS=[]))
    monkeypatch.delitem(sys.modules, "pipeline.run_dromic", raising=False)
    rd = importlib.import_module("pipeline.run_dromic")
    rerun = tmp_path / "_needs_rerun.txt"
    rd._record_assistance_failure(str(tmp_path / "Fire Incident in Brgy. E. Rodriguez 20261115"), str(rerun), ValueError("x"))
    assert not rerun.exists()
    assert "Fire Incident in Brgy. E. Rodriguez 20261115" in (tmp_path / "_assistance_failures.txt").read_text(encoding="utf-8")
    monkeypatch.delitem(sys.modules, "pipeline.run_dromic", raising=False)   # don't leak the stubbed module
