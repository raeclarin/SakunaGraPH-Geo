from __future__ import annotations

import argparse
import csv
import logging
import os
from collections.abc import Iterable
from pathlib import Path

from mappings.dromic import (
    aff_pop_mapping,
    assistance_mapping,
    event_geometry_mapping,
    event_mapping,
    housing_mapping,
    pevac_mapping,
    prov_mapping,
)
from mappings.graph import Graph, create_graph
from semantic_processing.event_geo import (
    WEIGHT_FIELDS,
    compute_event_centroid,
    get_geo_index,
    split_location_iris,
)
from transform.dromic import (
    load_aff_pop,
    load_assistance,
    load_event,
    load_housing,
    load_provenance,
)
from validate.validate import (
    DEFAULT_CONTEXT_GRAPH_PATHS,
    ShaclValidationError,
    ShaclValidator,
    validation_focus_nodes,
)


log = logging.getLogger(__name__)

DATA_DIR = "../data/parsed/dromic"
OUT_DIR = "../data/rdf/events/dromic"
DEFAULT_BATCH_SIZE = 100
CENTROID_REPORT = "../logs/dromic_event_centroids.csv"
CENTROID_REPORT_FIELDS = [
    "folder", "event_iri", "event_name", "lat", "lon", "precision", "method",
    "location_count", "unweighted_lat", "unweighted_lon", "weighting_shift_km",
    "spread_km", "location_source", "unresolved",
]


def _record_assistance_failure(folder_path: str, needs_rerun_path: str, exc: Exception) -> None:
    log.warning("Skipping assistance for %s: %s", folder_path, exc)

    path = Path(needs_rerun_path)
    existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    # .name, not .stem: event folders contain dots ("Brgy. E. Rodriguez ..."), and
    # .stem cut them at the first one, so the skip list never matched (notes.md §8.13).
    folder = Path(folder_path).name.strip()
    if folder not in existing:
        with path.open("a", encoding="utf-8") as f:
            f.write("\n" + folder)


def _event_locations(ev, aps, hs):
    """Locations feeding the event centroid, best source first.

    1. affected-population rows (municipality level, carry weights)
    2. the event's own metadata location (e.g. a fire in one town)
    3. housing-damage rows (no weights)
    """
    if aps:
        rows = [
            (iri, {f: getattr(ap, f, None) for f in WEIGHT_FIELDS})
            for ap in aps if ap.hasLocation
            for iri in split_location_iris(ap.hasLocation)
        ]
        if rows:
            return rows, "affected-population"
    if ev.hasLocation:
        return [(iri, {}) for iri in split_location_iris(ev.hasLocation)], "event-metadata"
    if hs:
        rows = [(iri, {}) for h in hs if h.hasLocation for iri in split_location_iris(h.hasLocation)]
        if rows:
            return rows, "housing-damage"
    return [], ""


def _map_centroid(folder_path: str, g: Graph, event_iri, ev, aps, hs, centroid_rows) -> None:
    locations, location_source = _event_locations(ev, aps, hs)
    centroid = compute_event_centroid(get_geo_index(), locations) if locations else None
    if centroid is None:
        log.warning("No event centroid for %s (no resolvable locations)", ev.eventName)
        return
    event_geometry_mapping(g, event_iri, centroid)
    log.info(
        "Event centroid %.4f,%.4f (%s, %s, %d locations, spread %.1f km)",
        centroid.lat, centroid.lon, centroid.precision, centroid.method,
        centroid.location_count, centroid.spread_km,
    )
    if centroid.unresolved:
        log.warning("Unresolved locations for %s: %s", ev.eventName, centroid.unresolved)
    if centroid_rows is not None:
        centroid_rows.append({
            "folder": Path(folder_path).name,
            "event_iri": str(event_iri),
            "event_name": ev.eventName,
            "lat": centroid.lat,
            "lon": centroid.lon,
            "precision": centroid.precision,
            "method": centroid.method,
            "location_count": centroid.location_count,
            "unweighted_lat": centroid.unweighted_lat,
            "unweighted_lon": centroid.unweighted_lon,
            "weighting_shift_km": centroid.weighting_shift_km,
            "spread_km": centroid.spread_km,
            "location_source": location_source,
            "unresolved": "|".join(centroid.unresolved),
        })


def _write_centroid_report(rows: list[dict], path: str = CENTROID_REPORT) -> None:
    if not rows:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CENTROID_REPORT_FIELDS)
        w.writeheader()
        w.writerows(rows)
    log.info("Wrote event centroid report: %s (%d events)", path, len(rows))


def _map_event(
    folder_path: str,
    g: Graph,
    needs_rerun_path: str,
    centroid_rows: list[dict] | None = None,
) -> None:
    log.info("Processing DROMIC event folder: %s", folder_path)

    ev = load_event(os.path.join(folder_path, "metadata.json"))
    event_iri = event_mapping(g, ev)
    log.info("Event IRI: %s", event_iri)

    prov = load_provenance(os.path.join(folder_path, "source.json"))
    prov_mapping(g, prov, event_iri)

    aps, pevacs = load_aff_pop(folder_path)
    if aps:
        aff_pop_mapping(g, aps, event_iri)
    if pevacs:
        pevac_mapping(g, pevacs, event_iri)

    hs = load_housing(folder_path)
    if hs:
        housing_mapping(g, hs, event_iri)

    _map_centroid(folder_path, g, event_iri, ev, aps, hs, centroid_rows)

    try:
        assistance = load_assistance(folder_path)
        if assistance:
            assistance_mapping(g, assistance, event_iri)
    except Exception as exc:
        _record_assistance_failure(folder_path, needs_rerun_path, exc)

    log.info("Finished DROMIC event %s; graph now has %d triples", ev.eventName, len(g))


def process_event(folder_path: str, needs_rerun_path: str) -> Graph:
    graph = create_graph()
    _map_event(folder_path, graph, needs_rerun_path)
    return graph


def load_needs_rerun(needs_rerun_path: str) -> set[str]:
    """Load folder names that need rerunning from _needs_rerun.txt."""
    if not os.path.exists(needs_rerun_path):
        return set()
    with open(needs_rerun_path, encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip()}


def _event_folders(sub_data_dir: str, needs_rerun: set[str]) -> tuple[list[str], int]:
    if not os.path.isdir(sub_data_dir):
        raise FileNotFoundError(f"DROMIC parsed data directory not found: {sub_data_dir}")

    folders: list[str] = []
    skipped = 0
    for folder in next(os.walk(sub_data_dir))[1]:
        if folder in needs_rerun:
            skipped += 1
            continue

        folder_path = os.path.join(sub_data_dir, folder)
        if os.path.exists(os.path.join(folder_path, "metadata.json")):
            folders.append(folder)

    return folders, skipped


def _validate_batch_graph(
    g: Graph,
    *,
    validator: ShaclValidator,
    label: str,
    folders: Iterable[str],
) -> None:
    try:
        result = validator.validate_graph(
            g,
            label=label,
            focus_nodes=validation_focus_nodes(g),
        )
    except ShaclValidationError as exc:
        log.error("SHACL validation failed for %s", label)
        log.error("Batch folders: %s", ", ".join(folders))
        log.error(exc.result.results_text)
        raise

    log.info(
        "SHACL validation passed for %s (%d data triples, %d validation triples)",
        result.label,
        result.data_triples,
        result.validation_triples,
    )


def process_event_batches(
    folders: Iterable[str],
    g: Graph,
    *,
    sub_data_dir: str,
    needs_rerun_path: str,
    validator: ShaclValidator | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    fail_fast: bool = True,
    centroid_rows: list[dict] | None = None,
) -> int:
    if batch_size <= 0:
        raise ValueError("batch_size must be greater than zero")

    folder_names = list(folders)
    if not folder_names:
        log.warning("No DROMIC events found")
        return 0

    failures: list[ShaclValidationError] = []
    processed = 0
    batches = list(_batched(folder_names, batch_size))
    for batch_no, batch_folders in enumerate(batches, start=1):
        label = f"DROMIC batch {batch_no}/{len(batches)}"
        log.info("Processing %s (%d event folders)", label, len(batch_folders))

        batch_graph = create_graph()
        for folder in batch_folders:
            folder_path = os.path.join(sub_data_dir, folder)
            _map_event(folder_path, batch_graph, needs_rerun_path, centroid_rows)

        if validator is not None:
            try:
                _validate_batch_graph(
                    batch_graph,
                    validator=validator,
                    label=label,
                    folders=batch_folders,
                )
            except ShaclValidationError as exc:
                failures.append(exc)
                if fail_fast:
                    raise
                continue

        g += batch_graph
        processed += len(batch_folders)
        log.info(
            "Finished %s: %d events, %d triples",
            label,
            len(batch_folders),
            len(batch_graph),
        )

    if failures:
        raise RuntimeError(
            f"SHACL validation failed for {len(failures)} DROMIC batch(es); see logs."
        )

    return processed


def _batched(values: list[str], batch_size: int) -> Iterable[list[str]]:
    for start in range(0, len(values), batch_size):
        yield values[start : start + batch_size]


def run(
    sub_data_dir: str,
    out_file: str,
    *,
    start: int = 0,
    count: int | None = None,
    limit: int | None = None,
    batch_size: int | None = None,
    validate_output: bool = False,
    fail_fast: bool = True,
    include_context_graphs: bool = True,
) -> None:
    if start < 0:
        raise ValueError("start must be greater than or equal to zero")
    if limit is not None and limit < 0:
        raise ValueError("limit must be greater than or equal to zero")

    if batch_size is not None:
        effective_batch_size = batch_size
    elif count is not None:
        log.warning("--count is deprecated; use --batch-size instead")
        effective_batch_size = count
    else:
        effective_batch_size = DEFAULT_BATCH_SIZE

    if effective_batch_size <= 0:
        raise ValueError("batch_size must be greater than zero")

    log.info("=== DROMIC pipeline start: %s ===", sub_data_dir)
    needs_rerun_path = os.path.join(sub_data_dir, "_needs_rerun.txt")
    needs_rerun = load_needs_rerun(needs_rerun_path)

    log.info("Step 1/4: Discovering DROMIC event folders")
    folders, skipped = _event_folders(sub_data_dir, needs_rerun)
    stop = start + limit if limit is not None else None
    selected_folders = folders[start:stop]
    log.info(
        "Selected %d of %d event folders starting at offset %d (skipped %d)",
        len(selected_folders),
        len(folders),
        start,
        skipped,
    )

    validator = None
    if validate_output:
        log.info("Step 2/4: Loading SHACL validation context")
        if not include_context_graphs:
            log.info("SHACL validation context graphs are disabled")
        else:
            # Missing context files are skipped by the validator; say so, because
            # shapes that check classes (e.g. contributingOrg -> prov:Organization)
            # then fail on otherwise valid data (notes.md §8.14).
            for path in DEFAULT_CONTEXT_GRAPH_PATHS:
                if not path.exists():
                    log.warning("SHACL context graph missing, not loaded: %s", path)
        validator = ShaclValidator.from_paths(
            include_context_graphs=include_context_graphs,
        )
    else:
        log.info("Step 2/4: SHACL validation disabled")

    log.info(
        "Step 3/4: Processing DROMIC events in batches of %d",
        effective_batch_size,
    )
    main_graph = create_graph()
    centroid_rows: list[dict] = []
    processed = process_event_batches(
        selected_folders,
        main_graph,
        sub_data_dir=sub_data_dir,
        needs_rerun_path=needs_rerun_path,
        validator=validator,
        batch_size=effective_batch_size,
        fail_fast=fail_fast,
        centroid_rows=centroid_rows,
    )
    _write_centroid_report(centroid_rows)

    out_dir = os.path.dirname(out_file)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    log.info("Step 4/4: Serializing graph to %s", out_file)
    main_graph.serialize(destination=out_file, format="turtle")
    log.info(
        "=== DROMIC pipeline complete: %d events -> %s (skipped %d) ===",
        processed,
        out_file,
        skipped,
    )


def _year_dirs(data_dir: str) -> list[str]:
    if not os.path.isdir(data_dir):
        raise FileNotFoundError(f"DROMIC parsed data root not found: {data_dir}")
    return list(next(os.walk(data_dir))[1])


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    )

    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=str, default="2026")
    parser.add_argument("--out", type=str, default="dromic")
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument(
        "--count",
        type=int,
        default=None,
        help="Deprecated alias for --batch-size.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of DROMIC event folders to process after --start.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=None,
        help="Number of DROMIC event folders per validation batch. Defaults to 100.",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Run batch SHACL validation before merging each DROMIC batch.",
    )
    parser.add_argument(
        "--no-fail-fast",
        action="store_true",
        help="Validate all batches before failing on SHACL errors.",
    )
    parser.add_argument(
        "--no-context",
        action="store_true",
        help="Run SHACL validation without PSGC, disaster-type, organization, or provenance context graphs.",
    )
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()

    if args.all:
        for folder in _year_dirs(DATA_DIR):
            run(
                sub_data_dir=os.path.join(DATA_DIR, folder),
                out_file=os.path.join(OUT_DIR, f"{args.out}-{folder}.ttl"),
                start=args.start,
                count=args.count,
                limit=args.limit,
                batch_size=args.batch_size,
                validate_output=args.validate,
                fail_fast=not args.no_fail_fast,
                include_context_graphs=not args.no_context,
            )
    else:
        run(
            sub_data_dir=os.path.join(DATA_DIR, str(args.year)),
            out_file=os.path.join(OUT_DIR, f"{args.out}-{args.year}.ttl"),
            start=args.start,
            count=args.count,
            limit=args.limit,
            batch_size=args.batch_size,
            validate_output=args.validate,
            fail_fast=not args.no_fail_fast,
            include_context_graphs=not args.no_context,
        )
