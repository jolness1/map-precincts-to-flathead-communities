#!/usr/bin/env python3
"""Explore geographically comparable precinct groups and U.S. House vote changes.

Candidates are selected from geometry, not from their election outcomes.
Requires GDAL's Python bindings. Outputs go to output/precinct-comparisons/.
"""

import csv
import importlib.util
import json
from pathlib import Path
import sys

from osgeo import ogr, osr


ROOT = Path(__file__).resolve().parent


def import_script(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


matching = import_script("comparison_matching", "match-precincts.py")
elections = import_script("comparison_elections", "aggregate-election-results.py")

# Three strongest individual analogs plus combinations found by geographic
# overlap. These are exploratory alternatives, not a disjoint partition.
GROUPS = {
    "old-07_new-15": ([7], [15]),
    "old-19_new-16": ([19], [16]),
    "old-89_new-41": ([89], [41]),
    "old-24-26_new-01-35": ([24, 26], [1, 35]),
    "old-07_new-15-31": ([7], [15, 31]),
    "old-13-29-34-37_new-36-37-38-39-40-47-48": ([13, 29, 34, 37], [36, 37, 38, 39, 40, 47, 48]),
    "old-06-07-15-24-26-36_new-01-15-19-20-21-23-31-35-45": (
        [6, 7, 15, 24, 26, 36], [1, 15, 19, 20, 21, 23, 31, 35, 45],
    ),
}


def union_geometries(geometries):
    result = geometries[0].Clone()
    for geometry in geometries[1:]:
        result = result.Union(geometry)
    return result


def group_geometry(features, numbers):
    return union_geometries([features[f"PRECINCT {number:02d}"] for number in numbers])


def change(before, after):
    return 100 * (after / before - 1) if before else None


def comparison(group, first, last, before, after):
    total_change = change(before.total, after.total)
    rep_change = change(before.rep, after.rep)
    return {
        "group": group, "from_year": first, "to_year": last,
        "from_totalVotes": before.total, "to_totalVotes": after.total,
        "from_repVotes": before.rep, "to_repVotes": after.rep,
        "total_vote_change_pct": total_change, "rep_vote_change_pct": rep_change,
        "rep_minus_total_change_pp": rep_change - total_change if total_change is not None and rep_change is not None else None,
        "rep_share_change_pp": 100 * (after.rep / after.total - before.rep / before.total) if before.total and after.total else None,
    }


def write_csv(path, rows):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows({key: round(value, 6) if isinstance(value, float) else value
                         for key, value in row.items()} for row in rows)


def review_map(path, old, new, transform):
    features = []
    for name, geometry, color, opacity in (
        ("2014-2023 combined footprint", old, "#2563eb", 0.08),
        ("2024 combined footprint", new, "#d97706", 0.08),
        ("Older footprint only", old.Difference(new), "#9333ea", 0.65),
        ("Newer footprint only", new.Difference(old), "#dc2626", 0.65),
    ):
        if geometry.IsEmpty():
            continue
        geometry = geometry.Clone()
        geometry.Transform(transform)
        features.append({
            "type": "Feature", "geometry": json.loads(geometry.ExportToJson()),
            "properties": {"title": name, "name": name, "stroke": color,
                           "stroke-width": 2, "fill": color, "fill-opacity": opacity},
        })
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}, indent=2) + "\n", encoding="utf-8")


def main():
    ogr.UseExceptions()
    osr.UseExceptions()
    projected = osr.SpatialReference()
    projected.ImportFromEPSG(6514)
    projected.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    geographic = osr.SpatialReference()
    geographic.ImportFromEPSG(4326)
    geographic.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    to_geojson = osr.CoordinateTransformation(projected, geographic)
    old = matching.numbered_features(matching.spatial.PRECINCTS["2014-2023"], projected)
    new = matching.numbered_features(matching.spatial.PRECINCTS["2024"], projected)
    municipalities = matching.spatial.load_features(matching.spatial.MUNICIPALITIES, "COUNTY = 7", projected)
    municipal_geometry = union_geometries([geometry for _, geometry in municipalities])
    votes = {int(path.name[:4]): elections.read_results(path)
             for path in sorted((ROOT / "results-data").glob("*.csv"))}
    output = ROOT / "output/precinct-comparisons"
    output.mkdir(parents=True, exist_ok=True)

    quality, series, changes = [], [], []
    for group, (old_numbers, new_numbers) in GROUPS.items():
        old_geometry = group_geometry(old, old_numbers)
        new_geometry = group_geometry(new, new_numbers)
        old_area, new_area = old_geometry.GetArea(), new_geometry.GetArea()
        common = old_geometry.Intersection(new_geometry).GetArea()
        quality.append({
            "group": group, "old_precincts": ";".join(map(str, old_numbers)),
            "new_precincts": ";".join(map(str, new_numbers)),
            "iou_pct": 100 * common / (old_area + new_area - common),
            "pct_old_covered": 100 * common / old_area,
            "pct_new_covered": 100 * common / new_area,
            "old_area_km2": old_area / 1e6, "new_area_km2": new_area / 1e6,
            "old_only_area_km2": (old_area - common) / 1e6,
            "new_only_area_km2": (new_area - common) / 1e6,
            "pct_old_area_inside_municipalities": 100 * old_geometry.Intersection(municipal_geometry).GetArea() / old_area,
            "pct_new_area_inside_municipalities": 100 * new_geometry.Intersection(municipal_geometry).GetArea() / new_area,
        })
        totals = {year: elections.sum_votes(precincts, new_numbers if year == 2024 else old_numbers)
                  for year, precincts in votes.items()}
        for year, total in totals.items():
            series.append({"group": group, "year": year, **total.as_dict()})
        for first, last in ((2016, 2018), (2020, 2022), (2020, 2024), (2022, 2024)):
            changes.append(comparison(group, first, last, totals[first], totals[last]))
        review_map(output / f"{group}.geojson", old_geometry, new_geometry, to_geojson)

    write_csv(output / "candidate-geography.csv", quality)
    write_csv(output / "candidate-votes.csv", series)
    write_csv(output / "candidate-vote-changes.csv", changes)

    # Use the existing municipality assignments as a proxy only, not as a
    # Census rural classification. The stable-number analysis does not use 2024.
    mapping = elections.read_mapping(ROOT / "output/flathead-2014-2023-precincts.txt")
    associated = set().union(*mapping.values())
    precinct_changes, category_changes = [], []
    for first, last in ((2016, 2018), (2020, 2022)):
        for number in sorted(votes[first]):
            row = comparison(f"PRECINCT {number:02d}", first, last, votes[first][number], votes[last][number])
            row["municipality_associated"] = number in associated
            precinct_changes.append(row)
        for category, numbers in (
            ("county", set(votes[first])),
            ("municipality_associated", associated),
            ("not_municipality_associated", set(votes[first]) - associated),
        ):
            category_changes.append(comparison(
                category, first, last,
                elections.sum_votes(votes[first], numbers), elections.sum_votes(votes[last], numbers),
            ))
    write_csv(output / "older-precinct-midterm-changes.csv", precinct_changes)
    write_csv(output / "municipality-association-midterm-changes.csv", category_changes)
    print(f"Wrote candidate geography, vote series, changes, and {len(GROUPS)} boundary-difference maps to {output}")
    for row in quality:
        print(f"{row['group']}: {row['iou_pct']:.2f}% IoU")


if __name__ == "__main__":
    main()
