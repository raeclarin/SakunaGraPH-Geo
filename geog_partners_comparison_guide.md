# Hand-annotation guide: DROMIC reports, 10-report pilot

*For the Department of Geography partners, SakunaGraPH / Sakuna.PH.*
*Version 1, 23 November 2026. Questions go to the SakunaGraPH thesis team.*

## 1. Why we are asking for this

SakunaGraPH reads DSWD DROMIC situation reports automatically. For each report it:
1. downloads the PDF;
2. reads the tables;
3. works out which municipality each table row refers to;
4. collects the numbers of affected families and persons;
5. computes one map point (an *event centroid*) for the disaster.

To know how accurate this is, we need the same work **done carefully by hand** for a small set of reports. The result is a reference answer, a "gold standard". We then compare the program's output with yours, stage by stage. This lets us say, for example, "98% of rows were placed in the right municipality, and the event point was on average 0.4 km from the hand-computed point".

Your sheets are the reference, so please:
- **Work from the PDF only.** Do not look at Sakuna.PH, the knowledge graph or any program output for these reports.
- **Follow the rules below literally, even where you would personally do it differently.** The rules are the ones the program follows. If you think a rule is wrong geographically, write that in the `notes` column. Those comments are valuable findings too, but the numbers themselves must follow the rules.
- **Record what the report says, not what you think it should say.** If a total in the report doesn't add up, copy the numbers as printed and write a note.
- **Write down how long each report took** (minutes). Manual effort compared with the automated pipeline is one of our results.

If two people can annotate each report independently, even better: agreement between annotators tells us how hard the task is. If not, one careful annotator per report is fine for the pilot.

Expected effort: about 20–30 min for a single-barangay fire report, and 60–90 min for a multi-province report.

## 2. What you receive

The folder `evaluation/` contains:

| File | What it is |
|---|---|
| `pilot_reports.csv` | The 10 reports: an ID (R01…R10), the PDF file name and the DROMIC link |
| `pdfs/` | The 10 PDFs (identical to the files the program used) |
| `templates/reports.csv` | One row per report: event details |
| `templates/rows.csv` | One row per table row: location, PSGC code, counts |
| `templates/centroids.csv` | One row per report: your hand-computed event point |
| `reference_points.csv` | The coordinates of every region, province, city and municipality that the program uses (see Step 5) |

Fill in copies of the three templates and return them in `evaluation/reference/<your initials>/`. Keep the header rows exactly as they are. Use one row per item, UTF-8 CSV (Excel: *Save As → CSV UTF-8*). Leave a cell empty when there is no value.

## 3. The 10 reports

They are chosen to cover different kinds of events and table sizes:

| ID | Report | Why it is in the sample |
|---|---|---|
| R01 | Mayon Volcano Seismic Activities, Report 111 (17 Nov 2026 6PM) | Volcanic; many municipalities; displacement tables |
| R02 | Effects of El Niño in Western Visayas, Report 1 (17 Nov 2026 6AM) | Drought; two provinces |
| R03 | Effects of El Niño, Report 42 (17 Nov 2026 6AM) | Drought; many regions (largest table) |
| R04 | Dengue Outbreak in Bicol Region, Report 10 (16 Nov 2026 6PM) | Health emergency; regional |
| R05 | Effects of Localized Thunderstorms in Western Visayas (17 Nov 2026 6AM) | Weather; unnumbered report |
| R06 | Fire, Brgy. E. Rodriguez, Quezon City, Report 4 (15 Nov 2026 6PM) | NCR fire, written "Metro Manila" (a known past error) |
| R07 | Fire, Brgy. Manuyo Dos, City of Las Piñas, Report 1 (16 Nov 2026 6PM) | NCR fire; "City of …" naming; "ñ" |
| R08 | Fire, Brgy. Labo, City of Ozamis, Report 2 (17 Nov 2026 6AM) | City outside NCR; report spelling differs from PSGC (Ozamiz) |
| R09 | Fire, Brgy. Buenavista, Alangalang, Leyte, Terminal Report (16 Nov 2026 6AM) | Municipality; terminal (final) report |
| R10 | Fire, Brgy. Ugac Sur, Tuguegarao City, Cagayan (14 Nov 2026 6PM) | City named "X City" in both the report and PSGC |

## 4. Step by step

### Step 1: Event details → `reports.csv`
One row per report:

| Column | What to write | Example |
|---|---|---|
| `report_id` | R01…R10 | R01 |
| `annotator` | your initials | JDC |
| `event_name` | The event as named in the report title, without "DSWD DROMIC Report #… on the" and without the "as of" date | Mayon Volcano Seismic Activities |
| `hazard` | The kind of disaster in your own words (fire, volcanic unrest, drought, flood, dengue outbreak…) | volcanic unrest |
| `start_date` | The date the incident began, **as stated in the report text** (often the first paragraph), YYYY-MM-DD. If the report gives none, leave it empty. | 2026-10-30 |
| `report_number` | The number in the title. Empty if none. Write `terminal` for a Terminal Report. | 111 |
| `as_of` | The "as of" date and time in the title, YYYY-MM-DD HH:MM (24 h) | 2026-11-17 18:00 |
| `title_location` | The place named in the title, written out (barangay, municipality/city, province) | Brgy. E. Rodriguez, Quezon City |
| `title_psgc_code` | PSGC code of the most specific place in the title (Step 4) | 1381300000 |
| `minutes_spent` | Total minutes spent on this report, all steps | 45 |
| `notes` | Anything unclear | |

### Step 2: Find the right table
The program reads the table titled **"Number of Affected Families / Persons"** (sometimes "Affected Population"). It has columns for barangays, families and persons. This is the main table.

It also reads two other tables:
- **"Number of Displaced Families / Persons"**: inside and outside evacuation centres (ECs). Some reports have a "Total Displaced" table instead.
- **"Damaged Houses"**: totally and partially damaged.

If a report has no affected-population table, use whichever of these it has. Write which table you used in the `table` column of Step 3.

### Step 3: One row per place → `rows.csv`
Go down the affected-population table and write **one row for every line that names a single municipality or city**. Lines that are only a region or province heading with a subtotal are **not** rows. Neither are the "GRAND TOTAL" or "TOTAL" lines. *Exception:* if the report gives *only* a region or province line, with no municipalities under it, record that line, because it is the most precise place the report gives.

If the table lists barangays under a municipality, make one row per barangay line.

| Column | What to write |
|---|---|
| `report_id` | R01… |
| `annotator` | your initials |
| `table` | `affected`, `displaced` or `housing` (which table this row is from) |
| `row_no` | 1, 2, 3… in the order the rows appear in the PDF |
| `region_text` | The region exactly as printed in the table, e.g. "REGION V", "NCR" |
| `province_text` | The province as printed, e.g. "Albay", "Metro Manila" |
| `municipality_text` | The municipality or city as printed, e.g. "Guinobatan", "Quezon City" |
| `barangay_text` | The barangay if the row names one, else empty |
| `psgc_code` | The 10-digit PSGC code of the **most specific** place in the row (Step 4) |
| `psgc_name` | The official PSGC name of that place, e.g. "City of Legazpi" |
| `psgc_level` | `Reg`, `Prov`, `City`, `Mun`, `SubMun` or `Bgy` |
| `affected_barangays` | Number of affected barangays |
| `affected_families` | Number of affected families |
| `affected_persons` | Number of affected persons |
| `displaced_families` | Displaced families, inside plus outside ECs (see the rules below) |
| `displaced_persons` | Displaced persons, inside plus outside ECs |
| `houses_totally_damaged` | From the damaged-houses table, if any |
| `houses_partially_damaged` | From the damaged-houses table, if any |
| `notes` | e.g. "total in PDF does not match sum of rows" |

Rules for the numbers:
- Copy numbers **without thousands separators**: 102147, not 102,147.
- A dash "-" or an empty cell means **no value**: leave the cell empty, don't write 0.
- DROMIC tables often have two columns per measure, **CUM** (cumulative since the start of the event) and **NOW** (currently). **Always use CUM.**
- **Displaced** = inside ECs (CUM) + outside ECs (CUM), for families and for persons separately. If the report has a "Total Displaced" table, use its CUM column instead of adding.
- If the same municipality appears in more than one table (affected and housing, say), write a separate row for each table, with the matching `table` value. Put each number only in the row for the table it came from.

### Step 4: The PSGC code
Every place gets its official 10-digit code from the PSA's Philippine Standard Geographic Code, **3rd Quarter 2026** release:
- Use the PSA site: psa.gov.ph → Classification → PSGC.
- Or, for regions, provinces, cities and municipalities only, the `psgc_code` and `label` columns of `reference_points.csv`.

Details that matter:
- **Cities:** PSGC names most cities "City of X" (City of Legazpi, City of Tabaco, City of Ozamiz). Some keep the "X City" form (Quezon City, Tuguegarao City). Spelling can differ from the report ("Ozamis" in the report, "City of Ozamiz" in PSGC): same place. Always copy the PSGC name into `psgc_name`.
- **"Metro Manila" is not a province.** For NCR rows, code the city or municipality in the row (Quezon City `1381300000`, City of Las Piñas `1380200000`, etc.), never the City of Manila unless the row says Manila.
- **Recent changes the report may not reflect:** Negros Occidental, Negros Oriental and Siquijor now belong to the Negros Island Region. Sulu is in Region IX. Maguindanao del Norte and del Sur were renumbered. Use the **current** code, and write in `notes` if the report's grouping differs.
- If you cannot decide between two places (two towns with the same name, for example), pick the one in the province the table row gives, and write both candidates in `notes`.
- If a place cannot be found at all, leave `psgc_code` empty and copy the text into `notes`.

### Step 5: The event centroid → `centroids.csv`
The event centroid is **one point that represents the whole event**: the average position of all the affected places, where places with more affected people pull the point harder. Think of it as the centre of mass of the disaster. A single-town fire is simply that town's point.

**5a. Coordinates of each place.** Use `reference_points.csv`: find the place's `psgc_code` and copy its `lat` and `lon`. These are the points the program uses: one point per municipality or city, computed from the official boundary polygons. For a **barangay row**, use the point of the municipality or city it belongs to. Barangays have no point of their own at this stage, so the program does the same.

> *Optional extra (if you have QGIS and time):* you can check our municipality points independently. Load the OCHA/HDX COD-AB Philippines ADM3 layer (v03). Reproject it to **EPSG:32651** (UTM 51N). Run *Multipart to singleparts* and keep only the **largest part** of each municipality. Then run *Centroids*, or *Point on Surface* if the centroid falls outside the polygon. Reproject the points back to EPSG:4326. Put your own coordinates in the `qgis_lat`/`qgis_lon` columns of a copy of `reference_points.csv` for the places in your reports. Differences of under 1 km are expected rounding; larger ones are interesting.

**5b. Which places count.** Use the first of these that applies:
1. the rows of the **affected-population** table (Step 3, `table = affected`);
2. if that table has no usable rows: the place named in the **report title**;
3. if neither applies: the rows of the **damaged-houses** table.

If the same `psgc_code` appears on several rows, merge them into one place and **add up** their numbers.

**5c. Choose ONE weight for the whole event.** Go down this list and take the **first** measure that has a number above zero for **every** place:
1. affected persons
2. affected families
3. displaced persons
4. displaced families

If none of the four has a value above zero for every place, **do not weight**: every place counts the same. Never mix measures, such as persons for one town and families for another; they are not comparable. One place only means no weighting is needed.

**5d. Weighted average on the sphere.** The program does not average latitudes and longitudes directly. It averages the places as **3-D directions from the Earth's centre** and turns the result back into a latitude and longitude. This is the standard way to average positions on a globe. A plain average of the degrees comes close for small events, but drifts as the event gets larger:

| Event extent | Plain average vs spherical method |
|---|---|
| 4 neighbouring towns (worked example below) | about 1 m |
| one region (e.g. Western Visayas) | about 15 m |
| the whole country (e.g. a national El Niño report) | up to about 1.7 km |

Please **use the spherical method**, so that differences between your point and the program's are real differences and not a method mismatch. In a spreadsheet it is a few extra columns. With one place per row, latitude in B, longitude in C and weight in D (put 1 everywhere if Step 5c says "do not weight"):

| Column | Formula (row 2, fill down) | Meaning |
|---|---|---|
| E `x` | `=COS(RADIANS(B2))*COS(RADIANS(C2))` | direction, x part |
| F `y` | `=COS(RADIANS(B2))*SIN(RADIANS(C2))` | direction, y part |
| G `z` | `=SIN(RADIANS(B2))` | direction, z part |
| H `w·x` | `=D2*E2` | weighted x |
| I `w·y` | `=D2*F2` | weighted y |
| J `w·z` | `=D2*G2` | weighted z |

Below the rows, sum the weighted columns: X = `SUM(H2:H14)`, Y = `SUM(I2:I14)`, Z = `SUM(J2:J14)`. Say these sums are in H16, I16 and J16. Then:

- event **longitude** = `=DEGREES(ATAN2(H16, I16))`
- event **latitude** = `=DEGREES(ATAN2(SQRT(H16^2+I16^2), J16))`

> **Excel and Google Sheets warning:** their `ATAN2(x, y)` takes the arguments in the **opposite order** to most programming languages and calculators (`atan2(y, x)`). The formulas above are already written for Excel/Sheets. Typed the other way round, the result lands in the wrong place without any error. The worked example below catches this.

For the **unweighted** point (5e), repeat with weight 1 for every place, or sum columns E, F and G instead of H, I and J.

**5e. Also compute the unweighted point.** Even when you used weights, compute the point with all weights set to 1 (same spherical method). We report how far weighting moves the point.

**5f. Spread.** The spread is the distance in kilometres from the event point to the **farthest** place used. It describes how dispersed the event is, and is 0 for a single place. Use the great-circle (haversine) distance. In Excel, with the event point in F1 (lat) and G1 (lon) and a place in B2/C2:

`=2*6371.0088*ASIN(SQRT(SIN(RADIANS(B2-$F$1)/2)^2+COS(RADIANS($F$1))*COS(RADIANS(B2))*SIN(RADIANS(C2-$G$1)/2)^2))`

Fill this down for every place and take the `MAX`.

**5g. Precision.** The point is only as precise as the coarsest place used:
- if any place is a **Region**, the precision is `Region`;
- else, if any place is a **Province**, `Province`;
- else, if the places are a mix of cities and municipalities, or all municipalities, `Municipality`;
- if all places are cities, `City`.

Barangay rows count as their city or municipality (see 5a).

**`centroids.csv` columns:** `report_id`, `annotator`, `location_source` (`affected`, `title` or `housing`), `location_count` (number of distinct places after merging), `weight_field` (`affected_persons`, `affected_families`, `displaced_persons`, `displaced_families` or `none`), `lat`, `lon`, `unweighted_lat`, `unweighted_lon`, `spread_km`, `precision`, `notes`. Give coordinates to 6 decimal places and distances to 3.

### Worked example: check your spreadsheet with this first
Four towns near Mayon, with made-up counts of affected persons:

| Place | PSGC code | lat | lon | affected persons |
|---|---|---|---|---|
| Daraga | 0500503000 | 13.114813 | 123.688861 | 4000 |
| Guinobatan | 0500504000 | 13.172739 | 123.576793 | 3000 |
| City of Legazpi | 0500506000 | 13.099321 | 123.753188 | 2500 |
| City of Tabaco | 0500517000 | 13.323441 | 123.670091 | 1500 |

- Weight: affected persons, since all four have a number above zero.
- Intermediate sums: X = −5938.494, Y = 8914.382, Z = 2503.547. For Daraga alone: x = −0.540215, y = 0.810360, z = 0.226903.
- Weighted point: **(13.155547, 123.670364)**. A plain average of the degrees would give (13.155539, 123.670357), about 1 m away. Small here, but see the table in 5d for larger events.
- Unweighted point: (13.177586, 123.672241). Weighting moved the point 2.459 km toward Daraga.
- Distances from the weighted point: Daraga 4.953 km, Guinobatan 10.310 km, Legazpi 10.933 km, Tabaco 18.669 km. **Spread = 18.669 km.**
- Precision: Municipality (a mix of municipalities and cities).

If your spreadsheet reproduces these numbers to the shown decimals (±0.00001° and ±0.005 km), it is set up correctly.

## 5. Common pitfalls
- **Province subtotal rows counted as places.** They double-count people and drag the point toward the province's centre. Only municipality/city (or barangay) rows count.
- **NOW instead of CUM.** Always use the cumulative column.
- **"Metro Manila" coded as the City of Manila.** It is a region, not a city.
- **Thousands separators and footnote marks.** "102,147¹" is 102147. Ignore superscripts and footnote numbers.
- **Merged or wrapped cells in the PDF.** A long name can wrap onto two lines; it is still one row. A number split across a line break belongs to one cell.
- **Typos in the report** (e.g. a misspelled town). Code the place you are sure is meant, and note the spelling.

## 6. What we do with your sheets
A comparison script (`etl/evaluation/compare.py`) lines up your rows with the program's rows by `report_id` and `psgc_code`. It reports, for each report and in total:
- event details: whether the name, start date and report number match;
- rows: how many of your rows the program found (recall), and how many of its rows are in your sheet (precision);
- locations: the share of rows with the same PSGC code, with each mistake listed;
- numbers: the share of exact matches and the typical relative error, per column;
- event point:
  - the distance in km between your point and the program's point;
  - the distance between the program's point and the point the program's own formula gives from **your** rows. A gap here comes from reading the report (tables, locations, numbers). A gap that remains when the rows agree comes from the centroid step itself.

You will receive the comparison report. Every disagreement is checked by hand before it is counted as a program error, because the reference can also be wrong. We will tell you which side was right.

Thank you. This pilot is what lets the thesis state measured accuracy instead of assumed accuracy.
