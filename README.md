# map-precincts-to-flathead-communities

Map Flathead County precincts to Columbia Falls, Kalispell, and Whitefish using
the supplied municipality polygons and each of the two precinct shapefiles.

Run with Python and GDAL's Python bindings (`from osgeo import ogr, osr`):

```sh
python3 map-precincts-to-munipalities.py
```

This writes `output/flathead-2014-2023-precincts.txt` and
`output/flathead-2024-precincts.txt`. Paths resolve relative to the script.
Each line lists a municipality and its precincts in numeric order, for example
`Whitefish: PRECINCT 01, PRECINCT 04` (format example only).

The same run creates six review maps in `output/`, named
`{municipality}-precincts-{period}.geojson`, for example
`whitefish-precincts-2024.geojson` and
`columbia-falls-precincts-2014-2023.geojson`.
Each map contains the full matching precinct polygons plus the municipality
polygon, in WGS84 longitude/latitude coordinates. Precincts are not clipped to
municipal limits, so you can see how far they extend beyond the community.

Open these files in a GeoJSON viewer or GIS application. SimpleStyle properties
give precincts translucent colors and the municipality a thick black outline
with no fill in compatible viewers. If the viewer ignores these styles, style
features using `feature_type` (`precinct` or `municipality`). Each precinct has
its name, source file, overlap area in square meters, percentage of the precinct
inside the municipality, and percentage of the municipality inside the precinct
to help assess marginal overlaps. The municipality feature uses the supplied
boundary for both periods; it is not a historical boundary for that period.

These maps support editorial review; editing a map does not change the text
lists or future runs. Rerunning the script regenerates both the lists and maps.

The script selects `COUNTY = 7` in the municipality GeoJSON and
`COUNTYCODE = '7'` in the precinct shapefiles, where `COUNTY` is the county name.
It transforms geometries to EPSG:6514 (meters), repairs invalid polygons in
memory, and includes each precinct with positive area inside a municipality.
Boundary touches alone do not count. A precinct can appear under multiple
municipalities, and precincts outside all three municipalities are omitted.

The default includes tiny overlaps that may reflect differences in boundary
detail. To exclude overlaps of 1,000 square meters or less, regenerate with:

```sh
python3 map-precincts-to-munipalities.py --min-overlap-m2 1000
```

Both periods use the same supplied municipality boundaries, which include
updates after 2024. These are spatial comparisons against those boundaries,
not reconstructions of historical municipal limits. Period labels follow the
input folder names; the older shapefile is named `MontanaVotingPrecincts2021`.
The older data also has two features named `NA`; neither overlaps a supplied
Flathead municipality, so neither appears in the default output.

## Election vote totals

Run the aggregation separately after generating the precinct lists:

```sh
python3 aggregate-election-results.py
```

This uses only Python's standard library and reads the election CSVs in
`results-data/`. It handles `Party`/`PartyCode`, `Precinct`/`PrecinctName`,
`Race Name`/`RaceName`, and `PCT 01`/`PRECINCT 1` variations. It selects
`Flathead` or `Flathead County` in `CountyName`, ignoring capitalization, and
only `UNITED STATES REPRESENTATIVE` or
`UNITED STATES REPRESENTATIVE - 1ST DISTRICT`. When present, the `Area` field
must be `1ST CONGRESSIONAL` (the 2024 file's district field).

Precinct numbers are matched as integers in both the election results and
municipality lists: `PRECINCT 1`, `PRECINCT 01`, and `PCT 001` all identify
precinct 1. Any number of leading zeros is ignored for matching.

The outputs are `output/columbia-falls.csv`, `output/kalispell.csv`,
`output/whitefish.csv`, and `output/precinct-{number}.csv` for every precinct
with matching election results, including those in municipality lists.
Each CSV has exactly these columns:

```csv
year,totalVotes,repVotes
```

`totalVotes` sums votes for all candidates in the selected contest, including
minor parties and any write-in rows present; `repVotes` sums only `REP` rows.
These are votes cast for candidates, not ballots cast including undervotes.
For example, the supplied 2014 precinct 36 data has 720 REP, 288 DEM, and
44 LIB votes, producing `2014,1052,720`.

The `2014-2023` precinct list applies to elections from 2014 through 2022;
the `2024` list applies to 2024. The program reads the text lists as the
assignment source, so editorial changes to those lists affect the next
aggregation run. Regenerating the spatial maps overwrites those lists.

Municipality totals include the full vote counts of the associated precincts;
votes are not apportioned by overlap area. Shared precincts contribute their
full votes to each municipality that lists them. In the default 2024 mapping,
precinct 08 contributes to both Columbia Falls and Whitefish. Consequently,
the municipality CSVs can overlap with one another. Individual precinct CSVs
also include the votes used in municipality totals, so adding municipality and
precinct CSVs together counts those votes again. To calculate county totals,
sum only the individual precinct CSVs for that year.
`output/vote-totals-audit.json` records
each year's assignments, shared precincts, county totals, unique assigned and
unassigned totals, and `extra_votes_from_shared_municipality_assignments`,
which measures duplication between municipality totals alone.

Individual precinct CSVs contain every available year for that precinct,
regardless of municipality membership. Years with no results are omitted.
For example, `precinct-01.csv` includes `2014,393,213`, even though precinct 1
also contributes to Kalispell's 2014 total. Precinct numbers across the redistricting are
not geographically consistent. Municipality lists referencing precincts absent
from that year's election results cause an error rather than silently losing votes.

Reruns replace generated vote CSVs and remove obsolete vote CSVs recorded in
the previous audit file. The text lists, maps, and source data are not rewritten
by this aggregation script. Alternate directories can be supplied with
`--results-dir`, `--mapping-dir`, and `--output-dir`.

Check the parsing and aggregation with:

```sh
python3 -m unittest discover -s tests -v
```

## Closest precinct analogs across periods

Compare the boundaries directly with:

```sh
python3 match-precincts.py
```

This writes three CSVs in `output/`:

- `precinct-overlap-2014-2023-to-2024.csv`: every pair sharing positive area,
  with overlap area, both precinct areas, both coverage percentages, and IoU.
- `precinct-analogs-2014-2023-to-2024.csv`: one row per older precinct.
- `precinct-analogs-2024-to-2014-2023.csv`: one row per newer precinct.

The analog files rank matches by intersection-over-union (`iou_pct`): the
shared area divided by the area covered by either polygon. A score of 100%
means identical footprints. `pct_source_covered` and `pct_analog_covered`
show how much of each polygon lies inside the other. For example, a newer
precinct wholly inside an older precinct can have 100% source coverage but
only 30% analog coverage, indicating a split rather than identical boundaries.

Each analog file also includes the runner-up, whether the match is each
precinct's best match in both directions (`mutual_best_match`), and a separate
best match by source coverage. That last field answers the question "which
other precinct covers the largest percentage of this precinct?" Ranking by
IoU instead favors similar overall footprints. Matches are many-to-one where
the geometry supports that; no one-to-one correspondence is forced.

Areas are measured in EPSG:6514 (meters). Boundary-only touches are excluded;
tiny positive overlaps appear in the full crosswalk. The two older polygons
named `NA` have no numbered election-result counterpart and are omitted, so
newer precincts overlapping those areas may have incomplete numbered coverage.
The older geometry represents the supplied 2021 shapefile, used as the proxy
for the 2014–2023 period. These scores measure land area, not voters or votes;
using an analog does not establish a geographically identical electorate.

## Exploratory midterm comparisons and combined precincts

```sh
python3 analyze-precinct-comparisons.py
```

This saves seven candidate geographic comparisons in
`output/precinct-comparisons/`, including old 24+26 versus new 01+35 and old 07
versus new 15+31. The candidate list is explicit in the script and was chosen
from spatial overlaps, not vote changes. It is exploratory, not an exhaustive
search or a finding that any match is sufficiently comparable for publication.
Some candidates overlap with others; they are alternatives and must not be
treated as independent units or summed together.

`candidate-geography.csv` reports both coverage percentages, IoU, square
kilometers added/removed, and municipal intersection percentages.
`candidate-votes.csv` reports the complete six-election series for each group.
`candidate-vote-changes.csv` compares 2016→2018 and 2020→2022 (presidential to
midterm), 2020→2024 (presidential to presidential), and 2022→2024 (midterm to
presidential). No votes are apportioned by area: whole precinct totals are
summed. Each group's GeoJSON shows the two combined footprints and highlights
the older-only and newer-only areas for review of populated boundary changes.

`older-precinct-midterm-changes.csv` compares all 42 older precincts within the
older numbering scheme. `municipality-association-midterm-changes.csv` compares
the county, precincts included in the municipality lists, and the remainder.
Association with those lists is not a Census urban/rural classification, and
municipal area percentages do not describe where the voters live.

Change percentages use `(later / earlier - 1) * 100`. A negative
`rep_minus_total_change_pp` means REP candidate votes changed less favorably
than all-candidate votes. `rep_share_change_pp` is the percentage-point change
in REP's share of the selected U.S. House vote. These are descriptive candidate
vote changes, not estimates of turnout among registered Republican voters.
Candidates, party switching, population growth, and boundary changes can also
affect the results. The old 2021 shapefile is only a proxy for boundaries in
earlier elections; equal precinct numbers alone do not verify stable geography.
2024 is a presidential-year benchmark, not an additional midterm observation.
