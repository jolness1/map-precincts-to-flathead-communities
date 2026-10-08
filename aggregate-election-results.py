#!/usr/bin/env python3
"""Aggregate Flathead U.S. House votes using the municipality precinct lists.

Uses only the Python standard library. Run after map-precincts-to-munipalities.py.
"""

import argparse
import csv
from dataclasses import dataclass
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parent
RACES = {
    "UNITED STATES REPRESENTATIVE",
    "UNITED STATES REPRESENTATIVE - 1ST DISTRICT",
}


@dataclass
class Votes:
    total: int = 0
    rep: int = 0

    def add(self, other):
        self.total += other.total
        self.rep += other.rep

    def as_dict(self):
        return {"totalVotes": self.total, "repVotes": self.rep}


def normalize(value):
    return " ".join(value.split()).upper()


def precinct_number(value):
    """Use an integer identity so PRECINCT 1, PRECINCT 01, and PCT 001 match."""
    match = re.fullmatch(r"(?:PRECINCT|PCT)\s+(\d+)", normalize(value))
    if not match:
        raise ValueError(f"Unrecognized precinct name: {value!r}")
    return int(match[1])


def read_mapping(path):
    mapping = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        community, separator, labels = line.partition(":")
        community = community.strip()
        if not separator or not community or community in mapping:
            raise ValueError(f"Invalid or duplicate municipality in {path}: {line!r}")
        mapping[community] = (
            set() if labels.strip() == "(none)"
            else {precinct_number(label) for label in labels.split(",")}
        )
    if not mapping:
        raise ValueError(f"No municipalities in {path}")
    return mapping


def read_results(path):
    """Normalize varying CSV headers, filter the county/race, then sum candidates."""
    totals = {}
    seen_rows = set()
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        fields = {re.sub(r"\s+", "", name).lower(): name for name in reader.fieldnames or []}
        if len(fields) != len(reader.fieldnames or []):
            raise ValueError(f"Duplicate column names in {path}")

        def column(*aliases):
            for alias in aliases:
                if alias in fields:
                    return fields[alias]
            raise ValueError(f"Missing column {' or '.join(aliases)} in {path}")

        county_key = column("countyname")
        precinct_key = column("precinctname", "precinct")
        race_key = column("racename")
        party_key = column("partycode", "party")
        votes_key = column("votes")
        area_key = fields.get("area")

        for row in reader:
            context = f"{path.name}, line {reader.line_num}"
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"Malformed CSV row: {context}")
            if normalize(row[county_key]) not in {"FLATHEAD", "FLATHEAD COUNTY"}:
                continue
            race = normalize(row[race_key])
            if race not in RACES:
                continue
            # The 2024 file puts the congressional district in a separate column.
            if area_key and normalize(row[area_key]) != "1ST CONGRESSIONAL":
                continue
            fingerprint = tuple(row.items())
            if fingerprint in seen_rows:
                raise ValueError(f"Duplicate selected result row: {context}")
            seen_rows.add(fingerprint)
            number = precinct_number(row[precinct_key])
            raw_votes = row[votes_key].strip()
            if not re.fullmatch(r"(?:\d+|\d{1,3}(?:,\d{3})+)", raw_votes):
                raise ValueError(f"Invalid vote count {raw_votes!r}: {context}")
            votes = int(raw_votes.replace(",", ""))
            precinct = totals.setdefault(number, Votes())
            precinct.total += votes
            if normalize(row[party_key]) == "REP":
                precinct.rep += votes
    if not totals:
        raise ValueError(f"No matching Flathead U.S. House results in {path}")
    return totals


def sum_votes(precincts, numbers):
    result = Votes()
    for number in numbers:
        result.add(precincts[number])
    return result


def aggregate_year(precincts, mapping):
    """Count full precinct votes in each mapped community, once per community."""
    assigned = set().union(*mapping.values())
    missing = assigned - precincts.keys()
    if missing:
        raise ValueError(f"Mapped precincts missing election results: {sorted(missing)}")
    unassigned = precincts.keys() - assigned
    municipalities = {name: sum_votes(precincts, numbers) for name, numbers in mapping.items()}
    other_precincts = {number: precincts[number] for number in sorted(unassigned)}
    shared = {
        str(number): sorted(name for name, numbers in mapping.items() if number in numbers)
        for number in sorted(assigned)
        if sum(number in numbers for numbers in mapping.values()) > 1
    }
    county = sum_votes(precincts, precincts)
    assigned_votes = sum_votes(precincts, assigned)
    unassigned_votes = sum_votes(precincts, unassigned)
    assert county.total == assigned_votes.total + unassigned_votes.total
    assert county.rep == assigned_votes.rep + unassigned_votes.rep
    duplicate_votes = Votes()
    for number, communities in shared.items():
        for _ in range(len(communities) - 1):
            duplicate_votes.add(precincts[int(number)])
    audit = {
        "county": county.as_dict(),
        "precinct_count": len(precincts),
        "municipalities": {
            name: {"precincts": sorted(mapping[name]), **votes.as_dict()}
            for name, votes in sorted(municipalities.items())
        },
        "unique_assigned": assigned_votes.as_dict(),
        "unassigned": {"precincts": sorted(unassigned), **unassigned_votes.as_dict()},
        "shared_precincts": shared,
        "extra_votes_from_shared_municipality_assignments": duplicate_votes.as_dict(),
    }
    return municipalities, other_precincts, audit


def generate(results_dir, mapping_dir, output_dir):
    mappings = {
        period: read_mapping(mapping_dir / f"flathead-{period}-precincts.txt")
        for period in ("2014-2023", "2024")
    }
    files = sorted(results_dir.glob("*.csv"))
    if not files:
        raise ValueError(f"No election CSV files in {results_dir}")
    outputs = {}
    audit = {"years": {}}
    for path in files:
        year_match = re.match(r"(\d{4})-", path.name)
        if year_match is None:
            raise ValueError(f"Expected a year-prefixed filename: {path.name}")
        year = int(year_match[1])
        if not 2014 <= year <= 2024 or str(year) in audit["years"]:
            raise ValueError(f"Unsupported or duplicate election year: {year}")
        period = "2024" if year == 2024 else "2014-2023"
        precincts = read_results(path)
        municipalities, other_precincts, details = aggregate_year(precincts, mappings[period])
        details.update({"source_file": path.name, "mapping_file": f"flathead-{period}-precincts.txt"})
        audit["years"][str(year)] = details
        for name, votes in municipalities.items():
            slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
            outputs.setdefault(f"{slug}.csv", []).append({"year": year, **votes.as_dict()})
        # Keep each precinct's history even when it also contributes to a municipality.
        for number, votes in sorted(precincts.items()):
            outputs.setdefault(f"precinct-{number:02d}.csv", []).append({"year": year, **votes.as_dict()})
        print(f"{year}: {len(precincts)} precincts; {len(municipalities)} municipalities; "
              f"{len(other_precincts)} unassigned precincts; "
              f"{details['county']['totalVotes']:,} total votes; {details['county']['repVotes']:,} REP votes")
        if details["shared_precincts"]:
            print(f"  Full votes counted in each listed municipality: {details['shared_precincts']}")

    # Calculate and validate all years before updating the generated files.
    output_dir.mkdir(parents=True, exist_ok=True)
    audit_path = output_dir / "vote-totals-audit.json"
    previous = json.loads(audit_path.read_text(encoding="utf-8")) if audit_path.exists() else {}
    for filename, rows in sorted(outputs.items()):
        with (output_dir / filename).open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=["year", "totalVotes", "repVotes"])
            writer.writeheader()
            writer.writerows(sorted(rows, key=lambda row: row["year"]))
    # Remove only obsolete CSVs recorded as outputs of a previous run.
    for filename in set(previous.get("generated_files", [])) - outputs.keys():
        if re.fullmatch(r"[a-z0-9-]+\.csv", filename):
            (output_dir / filename).unlink(missing_ok=True)
    audit["generated_files"] = sorted(outputs)
    audit_path.write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(outputs)} CSVs and {audit_path.name} to {output_dir}")
    return audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", type=Path, default=ROOT / "results-data")
    parser.add_argument("--mapping-dir", type=Path, default=ROOT / "output")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "output")
    args = parser.parse_args()
    generate(args.results_dir, args.mapping_dir, args.output_dir)


if __name__ == "__main__":
    main()
