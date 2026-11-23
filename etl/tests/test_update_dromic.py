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
