"""Check overlap scores and analog selection for identical, split, and merged shapes."""

import importlib.util
from pathlib import Path
import unittest

from osgeo import ogr


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("precinct_matches", ROOT / "match-precincts.py")
matches = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(matches)
ogr.UseExceptions()


def box(x, y, width, height):
    return ogr.CreateGeometryFromWkt(
        f"POLYGON (({x} {y}, {x + width} {y}, {x + width} {y + height}, "
        f"{x} {y + height}, {x} {y}))"
    )


class PrecinctMatchTests(unittest.TestCase):
    def test_identical_footprints_match_despite_different_numbers(self):
        pairs = matches.compare({"PRECINCT 01": box(0, 0, 10, 10)}, {"PRECINCT 26": box(0, 0, 10, 10)})
        self.assertEqual(len(pairs), 1)
        for field in ("pct_old_covered", "pct_new_covered", "iou_pct"):
            self.assertEqual(pairs[0][field], 100)
        best = matches.best_matches(pairs, ["PRECINCT 26"], "new")[0]
        self.assertEqual(best["closest_analog"], "PRECINCT 01")
        self.assertTrue(best["mutual_best_match"])

    def test_split_has_full_source_coverage_but_partial_analog_coverage(self):
        old = {"PRECINCT 01": box(0, 0, 10, 10)}
        new = {"PRECINCT 02": box(0, 0, 4, 10), "PRECINCT 03": box(4, 0, 6, 10)}
        pairs = matches.compare(old, new)
        best = matches.best_matches(pairs, new, "new")
        self.assertEqual([row["closest_analog"] for row in best], ["PRECINCT 01", "PRECINCT 01"])
        self.assertEqual([row["pct_source_covered"] for row in best], [100, 100])
        self.assertEqual([row["pct_analog_covered"] for row in best], [40, 60])
        self.assertEqual([row["mutual_best_match"] for row in best], [False, True])
        reverse = matches.best_matches(pairs, old, "old")[0]
        self.assertEqual(reverse["closest_analog"], "PRECINCT 03")
        self.assertEqual(reverse["runner_up"], "PRECINCT 02")

    def test_coverage_and_iou_can_choose_different_analogs(self):
        old = {"PRECINCT 01": box(0, 0, 4, 10), "PRECINCT 02": box(4, 0, 100, 10)}
        new = {"PRECINCT 03": box(0, 0, 10, 10)}
        row = matches.best_matches(matches.compare(old, new), new, "new")[0]
        self.assertEqual(row["closest_analog"], "PRECINCT 01")
        self.assertEqual(row["iou_pct"], 40)
        self.assertEqual(row["best_by_source_coverage"], "PRECINCT 02")
        self.assertEqual(row["best_source_coverage_pct"], 60)

    def test_touching_or_disjoint_precincts_have_no_analog(self):
        old = {"PRECINCT 01": box(0, 0, 10, 10)}
        new = {"PRECINCT 02": box(10, 0, 10, 10), "PRECINCT 03": box(30, 0, 10, 10)}
        pairs = matches.compare(old, new)
        self.assertEqual(pairs, [])
        for row in matches.best_matches(pairs, new, "new"):
            self.assertEqual(row["closest_analog"], "")
            self.assertEqual(row["overlapping_precinct_count"], 0)


if __name__ == "__main__":
    unittest.main()
