"""Check schema differences, vote denominators, assignment, and source totals."""

import csv
import contextlib
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("election_results", ROOT / "aggregate-election-results.py")
results = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = results
SPEC.loader.exec_module(results)


class ElectionResultsTests(unittest.TestCase):
    def read_csv(self, headers, rows):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "results.csv"
            with path.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(headers)
                writer.writerows(rows)
            return results.read_results(path)

    def test_aliases_county_race_and_all_candidates(self):
        for party_column in ("Party", "partyCode"):
            for precinct_column in ("Precinct", "PrecinctName"):
                with self.subTest(party=party_column, precinct=precinct_column):
                    totals = self.read_csv(
                        ["CountyName", precinct_column, "Race Name", party_column, "Votes"],
                        [
                            ["Flathead County", "PRECINCT 01", "UNITED STATES REPRESENTATIVE", "REP", "1,200"],
                            ["FLATHEAD", "PCT 01", "UNITED STATES REPRESENTATIVE", "DEM", "400"],
                            [" Flathead ", "PRECINCT 1", "UNITED STATES REPRESENTATIVE", "LIB", "30"],
                            ["Flathead", "PRECINCT 1", "UNITED STATES REPRESENTATIVE", "", "2"],
                            ["Flathead", "PRECINCT 1", "UNITED STATES SENATOR", "REP", "9999"],
                            ["Lake", "PRECINCT 1", "UNITED STATES REPRESENTATIVE", "REP", "9999"],
                            ["Flathead", "PRECINCT 1", "UNITED STATES REPRESENTATIVE - 2ND DISTRICT", "REP", "9999"],
                        ],
                    )
                    self.assertEqual(totals, {1: results.Votes(1632, 1200)})

    def test_district_in_race_or_separate_area(self):
        totals = self.read_csv(
            ["CountyName", "PrecinctName", "RaceName", "Party", "Votes", "Area"],
            [
                ["Flathead", "PCT 02", "UNITED STATES REPRESENTATIVE", "REP", "50", "1ST CONGRESSIONAL"],
                ["Flathead", "PCT 02", "UNITED STATES REPRESENTATIVE", "DEM", "20", "1ST CONGRESSIONAL"],
                ["Flathead", "PCT 02", "UNITED STATES REPRESENTATIVE", "REP", "999", "2ND CONGRESSIONAL"],
            ],
        )
        self.assertEqual(totals[2], results.Votes(70, 50))
        totals = self.read_csv(
            ["CountyName", "Precinct", "Race Name", "Party", "Votes"],
            [["Flathead", "PRECINCT 2", "UNITED STATES REPRESENTATIVE - 1ST DISTRICT", "REP", "50"]],
        )
        self.assertEqual(totals[2], results.Votes(50, 50))

    def test_shared_precincts_and_unassigned_reconciliation(self):
        precincts = {1: results.Votes(100, 60), 2: results.Votes(50, 20), 3: results.Votes(80, 70)}
        municipalities, unassigned, audit = results.aggregate_year(
            precincts, {"Alpha": {1, 2}, "Beta": {2}},
        )
        self.assertEqual(municipalities, {"Alpha": results.Votes(150, 80), "Beta": results.Votes(50, 20)})
        self.assertEqual(unassigned, {3: results.Votes(80, 70)})
        self.assertEqual(audit["county"], {"totalVotes": 230, "repVotes": 150})
        self.assertEqual(audit["shared_precincts"], {"2": ["Alpha", "Beta"]})
        self.assertEqual(audit["extra_votes_from_shared_municipality_assignments"], {"totalVotes": 50, "repVotes": 20})

    def test_precinct_csvs_include_years_assigned_to_municipalities(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            with contextlib.redirect_stdout(io.StringIO()):
                results.generate(ROOT / "results-data", ROOT / "output", output)
            with (output / "precinct-01.csv").open(newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual([int(row["year"]) for row in rows], [2014, 2016, 2018, 2020, 2022, 2024])
            self.assertEqual(rows[0], {"year": "2014", "totalVotes": "393", "repVotes": "213"})
            # Every precinct/year from the sources must survive export, regardless of assignment.
            for source in sorted((ROOT / "results-data").glob("*.csv")):
                year = source.name[:4]
                for number, votes in results.read_results(source).items():
                    with (output / f"precinct-{number:02d}.csv").open(newline="") as stream:
                        matching = [row for row in csv.DictReader(stream) if row["year"] == year]
                    self.assertEqual(matching, [{
                        "year": year, "totalVotes": str(votes.total), "repVotes": str(votes.rep),
                    }])

    def test_missing_results_are_not_zero_filled(self):
        with self.assertRaisesRegex(ValueError, "missing election results"):
            results.aggregate_year({1: results.Votes(0, 0)}, {"Alpha": {1, 2}})

    def test_duplicate_and_invalid_vote_rows_fail(self):
        headers = ["CountyName", "Precinct", "RaceName", "Party", "Votes"]
        row = ["Flathead", "PRECINCT 1", "UNITED STATES REPRESENTATIVE", "REP", "10"]
        with self.assertRaisesRegex(ValueError, "Duplicate selected"):
            self.read_csv(headers, [row, row])
        for invalid in ("", "-1", "1.5", "1,2", "NULL"):
            with self.subTest(votes=invalid), self.assertRaisesRegex(ValueError, "Invalid vote count"):
                self.read_csv(headers, [row[:-1] + [invalid]])

    def test_supplied_files_and_precinct_36_example(self):
        expected_totals = {2014: 31932, 2016: 47441, 2018: 47778, 2020: 59697, 2022: 47725, 2024: 63246}
        for path in sorted((ROOT / "results-data").glob("*.csv")):
            year = int(path.name[:4])
            precincts = results.read_results(path)
            self.assertEqual(sum(v.total for v in precincts.values()), expected_totals[year])
            self.assertEqual(len(precincts), 56 if year == 2024 else 42)
            if year == 2014:
                self.assertEqual(precincts[36], results.Votes(1052, 720))


if __name__ == "__main__":
    unittest.main()
