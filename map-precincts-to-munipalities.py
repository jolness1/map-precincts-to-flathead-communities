#!/usr/bin/env python3
"""Write Flathead municipality/precinct text lists and GeoJSON review maps.

Requires GDAL's Python bindings (osgeo). Run with --help for options.
All paths are relative to this script, so it can run from any directory.
"""

import argparse
import json
import math
from pathlib import Path
import re
import sys

from osgeo import ogr, osr


ROOT = Path(__file__).resolve().parent
MUNICIPALITIES = ROOT / "mt-municipalities-data/mt-municipalities-1m.geojson"
PRECINCTS = {
    "2014-2023": ROOT / "raw-precinct-data/voting-precincts-2014-2023-shapefile/MontanaVotingPrecincts2021.shp",
    "2024": ROOT / "raw-precinct-data/voting-precincts-2024-shapefile/VotingPrecincts.shp",
}


def load_features(path, county_filter, target_srs):
    """Read county-filtered features and return owned, valid projected geometries."""
    dataset = ogr.Open(str(path))
    if dataset is None:
        raise ValueError(f"Cannot open {path}")
    layer = dataset.GetLayer()
    layer.SetAttributeFilter(county_filter)
    source_srs = layer.GetSpatialRef()
    if source_srs is None:
        raise ValueError(f"Missing coordinate system: {path}")
    source_srs = source_srs.Clone()
    source_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    transform = osr.CoordinateTransformation(source_srs, target_srs)
    features = []
    for feature in layer:
        name = str(feature["NAME"] or "").strip()
        original = feature.GetGeometryRef()
        if original is None or original.IsEmpty():
            raise ValueError(f"Missing geometry: {path}, feature {feature.GetFID()}")
        geometry = original.Clone()
        geometry.Transform(transform)
        if not geometry.IsValid():
            geometry = geometry.MakeValid()
            print(f"Repaired geometry in memory: {path.name}, {name}", file=sys.stderr)
        if geometry.IsEmpty() or not geometry.IsValid() or geometry.GetArea() <= 0:
            raise ValueError(f"Unusable polygon: {path}, feature {feature.GetFID()}")
        features.append((name, geometry))
    if not features:
        raise ValueError(f"No features matching {county_filter}: {path}")
    return features


def precinct_label(name):
    """Keep precinct numbers from NAME, padding single digits as requested."""
    match = re.fullmatch(r"PRECINCT\s+(\d+)", name, flags=re.IGNORECASE)
    if match is None:
        raise ValueError(f"Overlapping feature has no recognized precinct name: {name!r}")
    return f"PRECINCT {int(match[1]):02d}"


def map_precincts(municipalities, precincts, min_overlap_m2=0):
    """Include every overlap exceeding the threshold; boundary touches do not count."""
    matches = {name: set() for name, _ in municipalities}
    overlaps = {name: [] for name, _ in municipalities}
    matched_indices = set()
    for community, municipality in municipalities:
        for index, (name, precinct) in enumerate(precincts):
            if not municipality.Intersects(precinct):
                continue
            overlap = municipality.Intersection(precinct)
            area = overlap.GetArea()
            if area > min_overlap_m2:
                matches[community].add(precinct_label(name))
                overlaps[community].append((index, area))
                matched_indices.add(index)
    return matches, matched_indices, overlaps


def review_geojson(community, municipality, precincts, overlaps, period,
                   transform, min_overlap_m2):
    """Export full polygons, with a municipality outline drawn last on top.

    Area statistics are calculated in the projected CRS before conversion to
    longitude/latitude. SimpleStyle properties are hints for compatible viewers.
    """
    def feature(geometry, properties):
        projected = geometry.Clone()
        projected.Transform(transform)
        return {
            "type": "Feature",
            "properties": {
                "municipality": community,
                "precinct_period": period,
                **properties,
            },
            "geometry": json.loads(projected.ExportToJson()),
        }

    colors = ("#2563eb", "#d97706", "#16a34a", "#9333ea", "#dc2626", "#0891b2")
    features = []
    ordered = sorted(overlaps, key=lambda item: int(precinct_label(precincts[item[0]][0]).split()[-1]))
    for order, (index, area) in enumerate(ordered):
        name, geometry = precincts[index]
        label = precinct_label(name)
        color = colors[order % len(colors)]
        features.append(feature(geometry, {
            "feature_type": "precinct",
            "name": label,
            "title": label,
            "source_name": name,
            "source_file": str(PRECINCTS[period].relative_to(ROOT)),
            "overlap_area_m2": round(area, 3),
            "precinct_area_m2": round(geometry.GetArea(), 3),
            "percent_precinct_in_municipality": round(100 * area / geometry.GetArea(), 6),
            "percent_municipality_in_precinct": round(100 * area / municipality.GetArea(), 6),
            "stroke": color,
            "stroke-width": 2,
            "fill": color,
            "fill-opacity": 0.2,
        }))

    features.append(feature(municipality, {
        "feature_type": "municipality",
        "name": community,
        "title": f"{community} municipal boundary",
        "source_file": str(MUNICIPALITIES.relative_to(ROOT)),
        "municipality_area_m2": round(municipality.GetArea(), 3),
        "stroke": "#111111",
        "stroke-width": 4,
        "stroke-opacity": 1,
        "fill-opacity": 0,
    }))
    return {
        "type": "FeatureCollection",
        "name": f"{community} precincts {period}",
        "min_overlap_m2": min_overlap_m2,
        "features": features,
    }


def format_mapping(matches):
    lines = []
    for community in sorted(matches):
        labels = sorted(matches[community], key=lambda label: int(label.split()[-1]))
        lines.append(f"{community}: {', '.join(labels) if labels else '(none)'}")
    return "\n".join(lines) + "\n"


def nonnegative_area(value):
    area = float(value)
    if not math.isfinite(area) or area < 0:
        raise argparse.ArgumentTypeError("overlap area must be finite and nonnegative")
    return area


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--min-overlap-m2", type=nonnegative_area, default=0,
        help="require overlap greater than this area in square meters (default: 0)",
    )
    args = parser.parse_args()
    ogr.UseExceptions()
    osr.UseExceptions()
    target_srs = osr.SpatialReference()
    target_srs.ImportFromEPSG(6514)  # NAD83(2011) / Montana, meters.
    target_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    geojson_srs = osr.SpatialReference()
    geojson_srs.ImportFromEPSG(4326)
    geojson_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    to_geojson = osr.CoordinateTransformation(target_srs, geojson_srs)
    municipalities = load_features(MUNICIPALITIES, "COUNTY = 7", target_srs)

    # Finish both spatial comparisons before writing any outputs.
    results = {}
    for period, path in PRECINCTS.items():
        precincts = load_features(path, "COUNTYCODE = '7'", target_srs)
        matches, matched, overlaps = map_precincts(municipalities, precincts, args.min_overlap_m2)
        results[f"flathead-{period}-precincts.txt"] = format_mapping(matches)
        for community, municipality in municipalities:
            slug = re.sub(r"[^a-z0-9]+", "-", community.lower()).strip("-")
            collection = review_geojson(
                community, municipality, precincts, overlaps[community], period,
                to_geojson, args.min_overlap_m2,
            )
            results[f"{slug}-precincts-{period}.geojson"] = json.dumps(collection, indent=2) + "\n"
        print(
            f"{period}: {len(precincts)} Flathead features; {len(matched)} overlap "
            f"the {len(matches)} municipalities; {len(precincts) - len(matched)} "
            "have no qualifying overlap."
        )

    output = ROOT / "output"
    output.mkdir(exist_ok=True)
    for filename, contents in results.items():
        path = output / filename
        path.write_text(contents, encoding="utf-8")
        print(f"Wrote {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
