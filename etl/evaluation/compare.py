"""Compare the pipeline with a hand-built reference (Geography pilot).

Reference sheets (one folder per annotator, see geog_partners_comparison_guide.md):
    evaluation/reference/<annotator>/reports.csv
    evaluation/reference/<annotator>/rows.csv
    evaluation/reference/<annotator>/centroids.csv
Report list: evaluation/pilot_reports.csv (report_id -> PDF file name).

Pipeline side is recomputed from the parsed folders (data/parsed/dromic/...)
with the same transform + centroid code that run_dromic uses, so no GraphDB
is needed and the numbers match what would be loaded.

Run from etl/:
    python -m evaluation.compare --annotator JDC
    python -m evaluation.compare --annotator JDC --reports R01 R06
Writes evaluation/results/<annotator>/{summary.md, per_report.csv,
location_errors.csv, figure_errors.csv}.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from evaluation.metrics import (
    COUNT_FIELDS,
    HOUSING_FIELDS,
    aggregate_rows,
    fmt_rate,
    haversine_km,
    municipal_code,
    names_agree,
    normalise_code,
    parse_count,
    score_fields,
    score_locations,
)

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
EVAL_DIR = ROOT / "evaluation"
PARSED_ROOT = ROOT / "data" / "parsed" / "dromic"

# reference column -> pipeline dataclass attribute
AFF_MAP = {
    "affected_barangays": "affectedBarangays",
    "affected_families": "affectedFamilies",
    "affected_persons": "affectedPersons",
    "displaced_families": "displacedFamilies",
    "displaced_persons": "displacedPersons",
}
HOUSING_MAP = {
    "houses_totally_damaged": "totallyDamagedHouses",
    "houses_partially_damaged": "partiallyDamagedHouses",
}
WEIGHT_MAP = {  # reference column -> event_geo weight field
    "affected_persons": "affectedPersons",
    "affected_families": "affectedFamilies",
    "displaced_persons": "displacedPersons",
    "displaced_families": "displacedFamilies",
}


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:  # Excel adds a BOM
        return [{k.strip(): (v or "").strip() for k, v in r.items() if k} for r in csv.DictReader(f)]


# =============================================================================
# Pipeline side
# =============================================================================

@dataclass
class PipelineReport:
    folder: Path | None
    event_name: str = ""
    start_date: str = ""
    report_number: str = ""
    rows: dict[str, dict[str, int | None]] = field(default_factory=dict)       # affected + displaced
    housing: dict[str, dict[str, int | None]] = field(default_factory=dict)
    title_codes: set[str] = field(default_factory=set)
    centroid: object | None = None
    error: str = ""


def find_parsed_folder(file_name: str) -> Path | None:
    """The parsed event folder whose source.json names this PDF (incl. _superseded)."""
    for source in PARSED_ROOT.glob("*/**/source.json"):
        try:
            name = json.loads(source.read_text(encoding="utf-8", errors="replace")).get("reportName", "")
        except json.JSONDecodeError:
            continue
        if name == file_name or name.replace(".docx", ".pdf") == file_name:
            return source.parent
    return None


def _date_str(value) -> str:
    if isinstance(value, (date, datetime)):
        return value.strftime("%Y-%m-%d")
    return str(value or "")[:10]


def _codes(value) -> list[str]:
    from semantic_processing.event_geo import split_location_iris
    return [c for c in (municipal_code(str(i)) for i in split_location_iris(value)) if c]


def load_pipeline_report(file_name: str) -> PipelineReport:
    folder = find_parsed_folder(file_name)
    rep = PipelineReport(folder=folder)
    if folder is None:
        rep.error = "not parsed (no folder with this reportName)"
        return rep

    from pipeline.run_dromic import _event_locations
    from semantic_processing.event_geo import compute_event_centroid, get_geo_index
    from transform.dromic import load_aff_pop, load_event, load_housing

    try:
        ev = load_event(str(folder / "metadata.json"))
        meta = json.loads((folder / "metadata.json").read_text(encoding="utf-8", errors="replace"))
        rep.event_name = ev.eventName
        rep.start_date = _date_str(ev.startDate)
        rep.report_number = str(meta.get("reportNumber") or "")
        rep.title_codes = set(_codes(ev.hasLocation)) if ev.hasLocation else set()

        aps, _ = load_aff_pop(str(folder))
        hs = load_housing(str(folder))
        for ap in aps or []:
            for code in _codes(ap.hasLocation):
                acc = rep.rows.setdefault(code, {k: None for k in AFF_MAP})
                for ref_col, attr in AFF_MAP.items():
                    v = parse_count(getattr(ap, attr, None))
                    if v is not None:
                        acc[ref_col] = (acc[ref_col] or 0) + v
        for h in hs or []:
            for code in _codes(h.hasLocation):
                acc = rep.housing.setdefault(code, {k: None for k in HOUSING_MAP})
                for ref_col, attr in HOUSING_MAP.items():
                    v = parse_count(getattr(h, attr, None))
                    if v is not None:
                        acc[ref_col] = (acc[ref_col] or 0) + v

        locations, _src = _event_locations(ev, aps, hs)
        rep.centroid = compute_event_centroid(get_geo_index(), locations) if locations else None
    except Exception as exc:  # report it, keep comparing other reports
        log.exception("Pipeline load failed for %s", folder)
        rep.error = repr(exc)
    return rep


# =============================================================================
# Reference side
# =============================================================================

def reference_centroid(rows: list[dict], report: dict):
    """The pipeline's own centroid formula applied to the reference rows.

    Same location priority as run_dromic._event_locations: affected rows,
    else the title location, else housing rows.
    """
    from rdflib import URIRef
    from semantic_processing.event_geo import compute_event_centroid, get_geo_index

    def iri(code: str) -> URIRef:
        return URIRef(f"https://sakuna.ph/{code}")

    weights = aggregate_rows([r for r in rows if r.get("table") in ("affected", "displaced")])
    affected_codes = {municipal_code(r.get("psgc_code")) for r in rows if r.get("table") == "affected"} - {None}
    if affected_codes:
        locs = [(iri(c), {WEIGHT_MAP[k]: weights.get(c, {}).get(k) for k in WEIGHT_MAP}) for c in sorted(affected_codes)]
    elif normalise_code(report.get("title_psgc_code")):
        locs = [(iri(municipal_code(report["title_psgc_code"])), {})]
    else:
        housing = sorted({municipal_code(r.get("psgc_code")) for r in rows if r.get("table") == "housing"} - {None})
        locs = [(iri(c), {}) for c in housing]
    return compute_event_centroid(get_geo_index(), locs) if locs else None


# =============================================================================
# Comparison
# =============================================================================

def compare_report(rid: str, file_name: str, gold_report: dict, gold_rows: list[dict],
                   gold_centroid: dict, location_errors: list, figure_errors: list) -> dict:
    pipe = load_pipeline_report(file_name)
    out: dict = {"report_id": rid, "pipeline_folder": pipe.folder.name if pipe.folder else "",
                 "pipeline_error": pipe.error}

    # Event metadata
    out["name_match"] = names_agree(gold_report.get("event_name"), pipe.event_name)
    out["start_date_match"] = (gold_report.get("start_date") or "") == pipe.start_date if gold_report.get("start_date") else ""
    gold_no = (gold_report.get("report_number") or "").lower()
    out["report_number_match"] = (gold_no == pipe.report_number) if gold_no and gold_no != "terminal" else ""

    # Locations (affected/displaced table rows)
    gold_aff = aggregate_rows([r for r in gold_rows if r.get("table") in ("affected", "displaced")])
    loc = score_locations(gold_aff, pipe.rows)
    out.update(gold_locations=len(loc.gold), pipeline_locations=len(loc.pipeline),
               matched_locations=len(loc.matched), location_recall=loc.recall,
               location_precision=loc.precision)
    names = {municipal_code(r.get("psgc_code")): r.get("psgc_name") or r.get("municipality_text") for r in gold_rows}
    for c in sorted(loc.missed):
        location_errors.append({"report_id": rid, "kind": "missed (in reference, not in pipeline)", "psgc_code": c, "name": names.get(c, "")})
    for c in sorted(loc.spurious):
        location_errors.append({"report_id": rid, "kind": "spurious (in pipeline, not in reference)", "psgc_code": c, "name": ""})

    # Figures on matched locations
    fields = score_fields(gold_aff, pipe.rows, COUNT_FIELDS)
    gold_h = aggregate_rows([r for r in gold_rows if r.get("table") == "housing"], HOUSING_FIELDS)
    fields.update(score_fields(gold_h, pipe.housing, HOUSING_FIELDS))
    for name, s in fields.items():
        out[f"{name}_exact"] = s.exact_rate
        out[f"{name}_n"] = s.compared
        for code, g, p in s.mismatches:
            figure_errors.append({"report_id": rid, "field": name, "psgc_code": code, "reference": g, "pipeline": p})

    # Centroids
    c = pipe.centroid
    out["pipeline_lat"] = getattr(c, "lat", "")
    out["pipeline_lon"] = getattr(c, "lon", "")
    out["pipeline_precision"] = getattr(c, "precision", "")
    out["pipeline_method"] = getattr(c, "method", "")
    try:
        g_lat, g_lon = float(gold_centroid["lat"]), float(gold_centroid["lon"])
    except (KeyError, TypeError, ValueError):
        g_lat = g_lon = None
    out["hand_lat"], out["hand_lon"] = g_lat or "", g_lon or ""
    out["hand_precision"] = gold_centroid.get("precision", "")
    ref = reference_centroid(gold_rows, gold_report) if gold_rows or gold_report else None
    out["ref_formula_lat"] = getattr(ref, "lat", "")
    out["ref_formula_lon"] = getattr(ref, "lon", "")
    # total error: pipeline vs the annotator's hand point
    out["km_pipeline_vs_hand"] = round(haversine_km(c.lat, c.lon, g_lat, g_lon), 3) if c and g_lat is not None else ""
    # upstream error: pipeline vs pipeline formula on reference rows (parsing + matching)
    out["km_pipeline_vs_ref_formula"] = round(haversine_km(c.lat, c.lon, ref.lat, ref.lon), 3) if c and ref else ""
    # arithmetic check: hand point vs pipeline formula on the same reference rows (should be ~0)
    out["km_hand_vs_ref_formula"] = round(haversine_km(g_lat, g_lon, ref.lat, ref.lon), 3) if ref and g_lat is not None else ""
    out["precision_match"] = (out["hand_precision"] == out["pipeline_precision"]) if out["hand_precision"] and c else ""
    out["minutes_spent"] = gold_report.get("minutes_spent", "")
    return out


def summarise(per_report: list[dict], annotator: str) -> str:
    def mean(key):
        vals = [r[key] for r in per_report if isinstance(r.get(key), (int, float)) and not isinstance(r.get(key), bool)]
        return sum(vals) / len(vals) if vals else None

    def share(key):
        vals = [r[key] for r in per_report if isinstance(r.get(key), bool)]
        return sum(vals) / len(vals) if vals else None

    gold = sum(r.get("gold_locations") or 0 for r in per_report)
    pipe = sum(r.get("pipeline_locations") or 0 for r in per_report)
    matched = sum(r.get("matched_locations") or 0 for r in per_report)
    lines = [
        f"# Accuracy pilot: pipeline vs reference ({annotator})",
        "",
        f"Reports compared: {len(per_report)}; not parsed / errors: "
        f"{sum(1 for r in per_report if r['pipeline_error'])}",
        "",
        "| Stage | Measure | Value |",
        "|---|---|---|",
        f"| Metadata | event name agrees | {fmt_rate(share('name_match'))} |",
        f"| Metadata | start date agrees | {fmt_rate(share('start_date_match'))} |",
        f"| Metadata | report number agrees | {fmt_rate(share('report_number_match'))} |",
        f"| Locations | recall (pooled: {matched}/{gold}) | {fmt_rate(matched / gold if gold else None)} |",
        f"| Locations | precision (pooled: {matched}/{pipe}) | {fmt_rate(matched / pipe if pipe else None)} |",
    ]
    for f in COUNT_FIELDS + HOUSING_FIELDS:
        n = sum(r.get(f"{f}_n") or 0 for r in per_report)
        ex = sum((r.get(f"{f}_exact") or 0) * (r.get(f"{f}_n") or 0) for r in per_report)
        if n:
            lines.append(f"| Figures | {f} exact ({round(ex)}/{n}) | {fmt_rate(ex / n)} |")
    for key, label in (("km_pipeline_vs_hand", "pipeline vs hand point"),
                       ("km_pipeline_vs_ref_formula", "pipeline vs formula on reference rows (upstream error)"),
                       ("km_hand_vs_ref_formula", "hand point vs formula on reference rows (arithmetic check)")):
        m = mean(key)
        lines.append(f"| Centroid | mean km, {label} | {'n/a' if m is None else f'{m:.3f}'} |")
    lines.append(f"| Centroid | precision label agrees | {fmt_rate(share('precision_match'))} |")
    m = mean("minutes_spent")
    lines.append(f"| Effort | mean minutes per report (by hand) | {'n/a' if m is None else f'{m:.0f}'} |")
    lines += ["", "Every disagreement must be checked by hand before it is counted as a pipeline error:",
              "see location_errors.csv and figure_errors.csv."]
    return "\n".join(lines) + "\n"


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    keys = list(dict.fromkeys(k for r in rows for k in r))
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


def run(annotator: str, reports: list[str] | None = None, reference_dir: Path | None = None,
        out_dir: Path | None = None) -> list[dict]:
    reference_dir = reference_dir or EVAL_DIR / "reference" / annotator
    out_dir = out_dir or EVAL_DIR / "results" / annotator
    pilot = {r["report_id"]: r["file_name"] for r in read_csv(EVAL_DIR / "pilot_reports.csv")}
    g_reports = {r["report_id"]: r for r in read_csv(reference_dir / "reports.csv")}
    g_rows = read_csv(reference_dir / "rows.csv")
    g_cent = {r["report_id"]: r for r in read_csv(reference_dir / "centroids.csv")}
    ids = reports or sorted(g_reports or pilot)

    per_report, loc_err, fig_err = [], [], []
    for rid in ids:
        if rid not in pilot:
            log.warning("%s is not in pilot_reports.csv; skipped", rid)
            continue
        log.info("Comparing %s", rid)
        per_report.append(compare_report(
            rid, pilot[rid], g_reports.get(rid, {}),
            [r for r in g_rows if r.get("report_id") == rid],
            g_cent.get(rid, {}), loc_err, fig_err,
        ))

    out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(out_dir / "per_report.csv", per_report)
    write_csv(out_dir / "location_errors.csv", loc_err)
    write_csv(out_dir / "figure_errors.csv", fig_err)
    (out_dir / "summary.md").write_text(summarise(per_report, annotator), encoding="utf-8")
    log.info("Wrote %s", out_dir)
    return per_report


def main() -> int:
    ap = argparse.ArgumentParser(description="Compare pipeline output with a hand-built reference")
    ap.add_argument("--annotator", required=True, help="folder name under evaluation/reference/")
    ap.add_argument("--reports", nargs="*", help="report IDs (default: all in the reference)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    run(args.annotator, args.reports)
    print((EVAL_DIR / "results" / args.annotator / "summary.md").read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
