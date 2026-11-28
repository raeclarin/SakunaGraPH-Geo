"""Hands-off DROMIC update: RSS -> parse -> map/validate -> GraphDB (Goal 4).

One run = one poll. Stages (notes.md §8.16):
  1. fetch      fetch.dromic_rss: new report files from the RSS feed
  2. stage      copy new PDFs to raw/dromic-new/{year}-pdf (DOCX -> PDF via
                parse/dromic-docx-to-pdf.py, Word COM, only if a DOCX arrived)
  3. parse      parse/dromic.py process_file() on each new PDF only
  4. supersede  keep the newest report folder per event; older ones move to
                parsed/dromic/{year}/_superseded/
  5. map        pipeline.run_dromic.run(validate, quarantine): rebuilds
                dromic-{year}.ttl; SHACL-failing events are skipped and listed
                in _needs_rerun.txt instead of blocking the run
  6. load       pipeline.load_graphdb --replace on the events/dromic graph
                (all dromic-*.ttl files, so other years stay)
  7. record     one row in logs/update_runs.csv (+ logs/update_dromic.log)

Stages 2-6 run only when stage 1 downloaded something (or --rebuild).
Nothing is loaded if stage 5 fails. --repo has no default on purpose.

Run from anywhere (it changes into etl/ itself):
    python -m pipeline.update_dromic --repo SakunaGraPH-dev
    python -m pipeline.update_dromic --repo SakunaGraPH-dev --dry-run
    python -m pipeline.update_dromic --repo SakunaGraPH-dev --rebuild   # no fetch needed
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ETL_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = ETL_DIR.parent
DATA_DIR = ROOT_DIR / "data"
LOG_DIR = ROOT_DIR / "logs"
RUN_LOG = LOG_DIR / "update_dromic.log"
RUNS_CSV = LOG_DIR / "update_runs.csv"
LOCK_FILE = LOG_DIR / "update_dromic.lock"
LOCK_STALE_SECONDS = 6 * 3600
EVENTS_DROMIC_DIR = DATA_DIR / "rdf" / "events" / "dromic"
SUPERSEDED = "_superseded"
UNCONVERTED_DOCX: list[str] = []  # filled by stage_files, reported in the run record

RUN_FIELDS = [
    "started_at", "finished_at", "duration_s", "year", "status", "feed_not_modified",
    "new_files", "fetch_failures", "parsed", "parse_failures", "superseded",
    "events", "quarantined", "triples", "loaded", "repo",
    "newest_report_pub", "latency_min", "message",
]

log = logging.getLogger("update_dromic")


@dataclass
class RunRecord:
    year: int
    repo: str
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    status: str = "running"
    feed_not_modified: bool = False
    new_files: int = 0
    fetch_failures: int = 0
    parsed: int = 0
    parse_failures: int = 0
    superseded: int = 0
    events: int = 0
    quarantined: int = 0
    triples: int = 0
    loaded: bool = False
    newest_report_pub: datetime | None = None
    message: str = ""

    def row(self) -> dict:
        finished = datetime.now(timezone.utc)
        latency = ""
        if self.loaded and self.newest_report_pub:
            latency = round((finished - self.newest_report_pub).total_seconds() / 60, 1)
        return {
            "started_at": self.started_at.isoformat(timespec="seconds"),
            "finished_at": finished.isoformat(timespec="seconds"),
            "duration_s": round((finished - self.started_at).total_seconds(), 1),
            "year": self.year, "status": self.status,
            "feed_not_modified": self.feed_not_modified,
            "new_files": self.new_files, "fetch_failures": self.fetch_failures,
            "parsed": self.parsed, "parse_failures": self.parse_failures,
            "superseded": self.superseded, "events": self.events,
            "quarantined": self.quarantined, "triples": self.triples,
            "loaded": self.loaded, "repo": self.repo,
            "newest_report_pub": self.newest_report_pub.isoformat() if self.newest_report_pub else "",
            "latency_min": latency, "message": self.message,
        }


# =============================================================================
# Lock (no overlapping runs when a slow run meets the next hourly trigger)
# =============================================================================

def acquire_lock() -> bool:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if LOCK_FILE.exists():
        age = time.time() - LOCK_FILE.stat().st_mtime
        if age < LOCK_STALE_SECONDS:
            return False
        log.warning("Removing stale lock (%.1f h old)", age / 3600)
        LOCK_FILE.unlink(missing_ok=True)
    try:
        fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w") as f:
        f.write(f"{os.getpid()} {datetime.now(timezone.utc).isoformat()}\n")
    return True


def release_lock() -> None:
    LOCK_FILE.unlink(missing_ok=True)


# =============================================================================
# Stage 2: stage files for the parser
# =============================================================================

def stage_files(new_files: list[Path], raw_dir: Path, pdf_dir: Path, year: int) -> list[Path]:
    """Copy new PDFs to {year}-pdf; convert DOCX there. Returns PDFs to parse."""
    pdf_dir.mkdir(parents=True, exist_ok=True)
    pdfs: list[Path] = []
    docs = [f for f in new_files if f.suffix.lower() in (".docx", ".doc")]
    for f in new_files:
        if f.suffix.lower() == ".pdf":
            target = pdf_dir / f.name
            shutil.copy2(f, target)
            pdfs.append(target)
    if docs:
        log.info("Converting %d Word file(s) to PDF (Word COM)", len(docs))
        # Non-fatal: without Microsoft Word (e.g. a Linux server) the conversion
        # fails; the Word reports are then skipped and counted, and every PDF in
        # this run is still processed. See notes.md §8.17 (DOCX advice).
        try:
            subprocess.run(
                [sys.executable, str(ETL_DIR / "parse" / "dromic-docx-to-pdf.py"), "--year", str(year)],
                cwd=ETL_DIR, check=True, timeout=1800,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            log.error("DOCX conversion failed (%r); %d Word report(s) not parsed this run", exc, len(docs))
        for f in docs:
            pdf = pdf_dir / f.with_suffix(".pdf").name
            if pdf.exists():
                pdfs.append(pdf)
            else:
                log.error("DOCX conversion produced no PDF: %s", f.name)
                UNCONVERTED_DOCX.append(f.name)
    # The parser reads provenance (post URL, download URL, date) from the
    # manifest next to the PDFs; refresh it every run (copy_pdfs_jsons in the
    # DOCX script never overwrites, so its copy goes stale).
    manifest = raw_dir / "manifest.json"
    if manifest.exists():
        shutil.copy2(manifest, pdf_dir / "manifest.json")
    return pdfs


# =============================================================================
# Stage 3: parse only the new PDFs
# =============================================================================

def _load_parser():
    # Loaded by path: "parse" has no __init__.py and a PyPI package of the
    # same name would shadow the namespace package.
    spec = importlib.util.spec_from_file_location("dromic_pdf_parser", ETL_DIR / "parse" / "dromic.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # loads docling once per run
    return module


def _report_number(folder: Path) -> int:
    try:
        meta = json.loads((folder / "metadata.json").read_text(encoding="utf-8", errors="replace"))
        return int(meta.get("reportNumber") or 0)
    except (OSError, ValueError, json.JSONDecodeError):
        return 0


def _archive(folder: Path, parsed_dir: Path) -> Path:
    archive = parsed_dir / SUPERSEDED
    archive.mkdir(exist_ok=True)
    target, n = archive / folder.name, 1
    while target.exists():
        target, n = archive / f"{folder.name}_{n}", n + 1
    shutil.move(str(folder), str(target))
    return target


def install_parsed_folder(new: Path, parsed_dir: Path) -> Path:
    """Move a freshly parsed event folder into parsed_dir.

    The parser names folders "<event> <YYYYMMDD>" (last-update date only), so
    the 6AM and 6PM reports of one day get the SAME name. Parsing straight into
    parsed_dir would write the second report's tables next to the first's
    (as "..._1.csv") and mix their figures. Instead each report is parsed into
    a staging folder and swapped in here: the report with the higher report
    number stays active, the other goes to _superseded/ (notes.md §8.18).
    """
    target = parsed_dir / new.name
    if target.exists():
        if _report_number(new) >= _report_number(target):
            log.info("Same-day report replaces %s (report %d -> %d)",
                     target.name, _report_number(target), _report_number(new))
            _archive(target, parsed_dir)
        else:
            log.info("Older report for %s archived directly", new.name)
            return _archive(new, parsed_dir)
    shutil.move(str(new), str(target))
    return target


def parse_files(pdfs: list[Path], parsed_dir: Path) -> tuple[int, int]:
    if not pdfs:
        return 0, 0
    parser = _load_parser()
    parsed_list = parsed_dir / "_parsed.txt"
    parsed_dir.mkdir(parents=True, exist_ok=True)
    staging_root = parsed_dir / "_staging"
    ok = failed = 0
    for i, pdf in enumerate(pdfs):
        staging = staging_root / str(i)  # short: Windows paths are limited to 260 chars
        shutil.rmtree(staging, ignore_errors=True)
        try:
            parser.process_file(pdf, staging)
            produced = [d for d in staging.iterdir() if d.is_dir()]
            if len(produced) != 1:
                raise RuntimeError(f"parser produced {len(produced)} folders")
            install_parsed_folder(produced[0], parsed_dir)
        except Exception as exc:
            log.error("Parse failed: %s (%r)", pdf.name, exc)
            failed += 1
            continue
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        ok += 1
        with parsed_list.open("a", encoding="utf-8") as f:
            f.write(pdf.name + "\n")
    return ok, failed


# =============================================================================
# Stage 4: one report per event
# =============================================================================

_DATE_SUFFIX = re.compile(r"\s(\d{8})$")


def _event_key(folder: Path) -> tuple[str, str]:
    """Same identity as transform/dromic.py:_event_id (name + start date)."""
    meta_path = folder / "metadata.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8", errors="replace"))
            name = (meta.get("eventName") or "").strip().lower()
            if name:
                return name, meta.get("startDate") or ""
        except json.JSONDecodeError:
            pass
    return _DATE_SUFFIX.sub("", folder.name).strip().lower(), "?"


def _recency(folder: Path) -> tuple[str, int, float]:
    m = _DATE_SUFFIX.search(folder.name)
    report_no = 0
    try:
        report_no = int(json.loads((folder / "metadata.json").read_text(
            encoding="utf-8", errors="replace")).get("reportNumber") or 0)
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    return (m.group(1) if m else "", report_no, folder.stat().st_mtime)


def supersede_old_reports(parsed_year_dir: Path, *, dry_run: bool = False) -> list[str]:
    """Keep the newest report folder of each event; move older ones aside.

    Event IRIs are minted from event name + start date, so two report folders
    of the same event map onto the same IRI and would mix old and new figures.
    Newest = latest folder date suffix (report's last-update date), then
    report number, then modification time.
    """
    groups: dict[tuple[str, str], list[Path]] = defaultdict(list)
    for d in sorted(parsed_year_dir.iterdir()):
        if d.is_dir() and not d.name.startswith("_"):
            groups[_event_key(d)].append(d)

    moved: list[str] = []
    for key, folders in groups.items():
        if len(folders) < 2:
            continue
        folders.sort(key=_recency, reverse=True)
        keep, older = folders[0], folders[1:]
        for d in older:
            log.info("Superseded: %s (kept %s)", d.name, keep.name)
            moved.append(d.name)
            if not dry_run:
                _archive(d, parsed_year_dir)
    return moved


# =============================================================================
# Stage 6: load
# =============================================================================

def load_events(repo: str, host: str, timeout: int) -> None:
    ttls = sorted(EVENTS_DROMIC_DIR.glob("*.ttl"))
    if not ttls:
        raise RuntimeError(f"No TTL files in {EVENTS_DROMIC_DIR}")
    cmd = [sys.executable, "-m", "pipeline.load_graphdb", "--repo", repo, "--host", host,
           "--replace", "--timeout", str(timeout)]
    for ttl in ttls:
        cmd += ["--file", str(ttl)]
    log.info("Loading %d file(s) into <%s> repo %s", len(ttls), "events/dromic", repo)
    proc = subprocess.run(cmd, cwd=ETL_DIR, capture_output=True, text=True)
    for line in (proc.stdout + proc.stderr).splitlines():
        log.info("  loader: %s", line)
    if proc.returncode != 0:
        raise RuntimeError(f"load_graphdb exited with {proc.returncode}")


# =============================================================================
# Orchestration
# =============================================================================

def append_run(record: RunRecord) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    new = not RUNS_CSV.exists()
    with RUNS_CSV.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=RUN_FIELDS)
        if new:
            w.writeheader()
        w.writerow(record.row())


def previous_run_failed() -> bool:
    """True when the last recorded run failed after fetching, so its files
    are in the manifest but never reached GraphDB; the next poll retries."""
    if not RUNS_CSV.exists():
        return False
    with RUNS_CSV.open(encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r.get("status") != "dry-run"]
    return bool(rows) and rows[-1].get("status") == "failed"


def run(args: argparse.Namespace, rec: RunRecord) -> RunRecord:
    from fetch.dromic_rss import fetch  # after chdir/sys.path set-up

    year = args.year
    raw_dir = DATA_DIR / "raw" / "dromic-new" / str(year)
    pdf_dir = DATA_DIR / "raw" / "dromic-new" / f"{year}-pdf"
    parsed_dir = DATA_DIR / "parsed" / "dromic" / str(year)

    # 1. fetch
    new_files: list[Path] = []
    if not args.rebuild:
        log.info("Stage 1/6: fetch (RSS, year %d)", year)
        res = fetch(year, dry_run=args.dry_run, max_pages=args.max_pages, backfill=args.backfill)
        rec.feed_not_modified = res.not_modified
        rec.fetch_failures = len(res.failures)
        if args.dry_run:
            rec.new_files = len(res.planned)
            rec.status = "dry-run"
            rec.message = "; ".join(m.filename for m in res.planned)[:500]
            return rec
        new_files = res.new_files
        rec.new_files = len(new_files)
        pubs = [datetime.fromisoformat(m.pub_date) for m in res.downloaded]
        rec.newest_report_pub = max(pubs) if pubs else None
        if not new_files:
            if previous_run_failed():
                log.warning("Previous run failed; retrying stages 4-6 without new files")
                rec.message = "retry after failed run"
            else:
                rec.status = "no-change" if not res.failures else "fetch-failed"
                return rec

    # 2-3. stage + parse
    log.info("Stage 2/6: stage %d file(s)", len(new_files))
    pdfs = stage_files(new_files, raw_dir, pdf_dir, year)
    if UNCONVERTED_DOCX:
        rec.parse_failures += len(UNCONVERTED_DOCX)
        rec.message = (rec.message + f" unconverted DOCX: {', '.join(UNCONVERTED_DOCX)}").strip()[:500]
    log.info("Stage 3/6: parse %d PDF(s)", len(pdfs))
    rec.parsed, rec.parse_failures = parse_files(pdfs, parsed_dir)

    # 4. supersede
    log.info("Stage 4/6: supersede older reports")
    rec.superseded = len(supersede_old_reports(parsed_dir)) if parsed_dir.exists() else 0

    # 5. map + validate
    log.info("Stage 5/6: map + SHACL (quarantine mode)")
    from pipeline import run_dromic
    summary = run_dromic.run(
        sub_data_dir=os.path.join(run_dromic.DATA_DIR, str(year)),
        out_file=os.path.join(run_dromic.OUT_DIR, f"dromic-{year}.ttl"),
        validate_output=True,
        quarantine=True,
        batch_size=args.batch_size,
    )
    rec.events = summary["processed"]
    rec.quarantined = len(summary["quarantined"])
    rec.triples = summary["triples"]

    # 6. load
    if args.no_load:
        rec.status = "ok-not-loaded"
        return rec
    log.info("Stage 6/6: load into GraphDB")
    load_events(args.repo, args.host, args.timeout)
    rec.loaded = True
    rec.status = "ok" if not (rec.quarantined or rec.parse_failures or rec.fetch_failures) else "ok-with-warnings"
    return rec


def setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    file_handler = logging.FileHandler(RUN_LOG, encoding="utf-8")
    file_handler.setFormatter(fmt)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.handlers[:] = [file_handler, console]


def main() -> int:
    parser = argparse.ArgumentParser(description="Poll DROMIC RSS and update the knowledge graph")
    parser.add_argument("--repo", required=True, help="GraphDB repository ID (no default, for safety)")
    parser.add_argument("--host", default="http://localhost:7200")
    parser.add_argument("--year", type=int, default=datetime.now().year)
    parser.add_argument("--dry-run", action="store_true", help="Only list what would be fetched")
    parser.add_argument("--rebuild", action="store_true",
                        help="Skip fetching; re-run supersede, map and load on existing parsed data")
    parser.add_argument("--no-load", action="store_true", help="Stop before loading into GraphDB")
    parser.add_argument("--max-pages", type=int, default=5)
    parser.add_argument("--backfill", action="store_true",
                        help="Fetch older posts too: read all --max-pages feed pages (see fetch.dromic_rss)")
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("--timeout", type=int, default=600, help="GraphDB request timeout (s)")
    args = parser.parse_args()

    # Redirected output (log file, Task Scheduler) uses the Windows code page; a stray
    # character such as U+25CF in a debug print must not abort a report (notes 8.20).
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(errors="backslashreplace")
    os.chdir(ETL_DIR)  # pipeline modules use ../data relative paths
    if str(ETL_DIR) not in sys.path:
        sys.path.insert(0, str(ETL_DIR))
    setup_logging()

    if not acquire_lock():
        log.warning("Another update is running (%s); exiting", LOCK_FILE)
        return 0
    rec = RunRecord(year=args.year, repo=args.repo)
    code = 0
    try:
        log.info("=== DROMIC update start (year %d, repo %s) ===", args.year, args.repo)
        run(args, rec)
    except Exception as exc:
        log.exception("DROMIC update failed")
        rec.status = "failed"
        rec.message = repr(exc)[:500]
        code = 1
    finally:
        if not args.dry_run:
            append_run(rec)
        release_lock()
    log.info("=== DROMIC update %s: %d new file(s), %d event(s), %d quarantined, loaded=%s ===",
             rec.status, rec.new_files, rec.events, rec.quarantined, rec.loaded)
    return code


if __name__ == "__main__":
    sys.exit(main())
