#!/usr/bin/env python3
"""Compare Flathead precinct boundaries across periods using area overlap.

Writes all overlapping pairs and closest analogs in both directions.
Requires GDAL's Python bindings; paths resolve relative to this script.
"""

import argparse
import csv
import importlib.util
from pathlib import Path

from osgeo import ogr, osr


ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("spatial_mapping", ROOT / "map-precincts-to-munipalities.py")
spatial = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(spatial)

PAIR_FIELDS = [
    "old_precinct", "new_precinct", "overlap_m2", "old_area_m2", "new_area_m2",
    "pct_old_covered", "pct_new_covered", "iou_pct",
]
BEST_FIELDS = [
    "source_precinct", "closest_analog", "iou_pct", "pct_source_covered",
    "pct_analog_covered", "mutual_best_match", "runner_up", "runner_up_iou_pct",
    "best_by_source_coverage", "best_source_coverage_pct", "overlapping_precinct_count",
]


def number(label):
    return int(label.split()[-1])


def numbered_features(path, target_srs):
    features = {}
    for name, geometry in spatial.load_features(path, "COUNTYCODE = '7'", target_srs):
        if name.upper() == "NA":
            print(f"Omitting unnamed NA polygon from {path.name} ({geometry.GetArea():,.0f} m²)")
            continue
        label = spatial.precinct_label(name)
        if label in features:
            raise ValueError(f"Duplicate precinct {label} in {path}")
        features[label] = geometry
    return features


def compare(old_precincts, new_precincts):
    """Calculate bidirectional coverage and IoU without rounding for ranking."""
    pairs = []
    for old_name, old in sorted(old_precincts.items(), key=lambda item: number(item[0])):
        for new_name, new in sorted(new_precincts.items(), key=lambda item: number(item[0])):
            if not old.Intersects(new):
                continue
            area = old.Intersection(new).GetArea()
            if area <= 0:
                continue
            old_area, new_area = old.GetArea(), new.GetArea()
            pairs.append({
                "old_precinct": old_name, "new_precinct": new_name,
                "overlap_m2": area, "old_area_m2": old_area, "new_area_m2": new_area,
                "pct_old_covered": 100 * area / old_area,
                "pct_new_covered": 100 * area / new_area,
                "iou_pct": 100 * area / (old_area + new_area - area),
            })
    return pairs


def best_matches(pairs, source_names, direction):
    """Rank by IoU; also report the candidate covering most of the source."""
    source = f"{direction}_precinct"
    other_direction = "new" if direction == "old" else "old"
    analog = f"{other_direction}_precinct"
    source_pct, analog_pct = f"pct_{direction}_covered", f"pct_{other_direction}_covered"

    def ranked(candidates, tie_field):
        return sorted(candidates, key=lambda pair: (-pair["iou_pct"], -pair["overlap_m2"], number(pair[tie_field])))

    by_source, by_analog = {}, {}
    for pair in pairs:
        by_source.setdefault(pair[source], []).append(pair)
        by_analog.setdefault(pair[analog], []).append(pair)
    rows = []
    for label in sorted(source_names, key=number):
        candidates = ranked(by_source.get(label, []), analog)
        row = {field: "" for field in BEST_FIELDS}
        row.update(source_precinct=label, overlapping_precinct_count=len(candidates))
        if candidates:
            best = candidates[0]
            coverage = sorted(candidates, key=lambda pair: (-pair[source_pct], -pair["iou_pct"], number(pair[analog])))[0]
            reverse_best = ranked(by_analog[best[analog]], source)[0]
            row.update({
                "closest_analog": best[analog], "iou_pct": best["iou_pct"],
                "pct_source_covered": best[source_pct], "pct_analog_covered": best[analog_pct],
                "mutual_best_match": reverse_best[source] == label,
                "best_by_source_coverage": coverage[analog],
                "best_source_coverage_pct": coverage[source_pct],
            })
            if len(candidates) > 1:
                row.update(runner_up=candidates[1][analog], runner_up_iou_pct=candidates[1]["iou_pct"])
        rows.append(row)
    return rows


def write_csv(path, fields, rows):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows({key: round(value, 6) if isinstance(value, float) else value
                         for key, value in row.items()} for row in rows)
    print(f"Wrote {path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "output")
    args = parser.parse_args()
    ogr.UseExceptions()
    osr.UseExceptions()
    target_srs = osr.SpatialReference()
    target_srs.ImportFromEPSG(6514)
    target_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    old = numbered_features(spatial.PRECINCTS["2014-2023"], target_srs)
    new = numbered_features(spatial.PRECINCTS["2024"], target_srs)
    pairs = compare(old, new)
    old_matches = best_matches(pairs, old, "old")
    new_matches = best_matches(pairs, new, "new")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.output_dir / "precinct-overlap-2014-2023-to-2024.csv", PAIR_FIELDS, pairs)
    write_csv(args.output_dir / "precinct-analogs-2014-2023-to-2024.csv", BEST_FIELDS, old_matches)
    write_csv(args.output_dir / "precinct-analogs-2024-to-2014-2023.csv", BEST_FIELDS, new_matches)
    print(f"Compared {len(old)} older and {len(new)} newer precincts: {len(pairs)} overlapping pairs.")
    for period, matches in (("2014-2023", old_matches), ("2024", new_matches)):
        close = sum(isinstance(row["iou_pct"], float) and row["iou_pct"] >= 90 for row in matches)
        unmatched = sum(not row["closest_analog"] for row in matches)
        print(f"{period}: {close} closest analogs have at least 90% IoU; {unmatched} have no numbered analog.")


if __name__ == "__main__":
    main()
