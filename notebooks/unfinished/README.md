# Unfinished notebooks

This folder holds the notebooks of functions that are not finished. The function code and its tests are in the
library, but the data or the notebook is not complete. Do not use these notebooks as a reference for the data.

## The graduation rule

A function leaves this folder only when all of these conditions are true:

1. **Desired coverage.** Each gap that the Coverage report of the function lists as desired and available from the
   source is loaded in prod. The operator can drop a gap, but only in writing.
2. **Backend clean for the table.** Each open code finding in that Coverage report is fixed and merged in the main
   repo. A finding of low or info severity can also close when the operator drops it in writing.
3. **A new Coverage proof.** A new Coverage proof of the table passes after conditions 1 and 2.
4. **Notebook, docstring and tests.** The notebook runs again with 0 errors. The docstring and the tests agree with
   the new data.

Then do these steps:

1. Move the notebook back to `notebooks/`.
2. Remove the UNFINISHED marks from the main README.
3. Send the commit of the move through QA.

## usda() and usda_series(): function_usda.ipynb

Coverage report: `reports/coverage_2026-11-27/usda.md`. Plan row: USDA-ADD.

The table holds only the calendar-year form (NASS reference period "YEAR") of each series: 19 series, 1,570 rows.

**1. Desired coverage** (national NASS rows):

- [ ] Monthly prices received for the 7 price series: 8,916 rows, and 642 marketing-year averages. The
      calendar-year prices start only in 1996 (hogs in 1988).
- [ ] Acreage planted and harvested for corn, soybeans, wheat and cotton: 1,016 rows.
- [ ] Grain stocks on 4 dates each year for corn, soybeans and wheat: 1,105 rows.
- [ ] Livestock inventory on given dates: 1,100 rows. This replaces the 2-row "HOGS - INVENTORY" series.
- [ ] Cattle price for 500 lb or more: 30 calendar-year rows and 2,291 monthly rows.
- [ ] Monthly milk production: 1,228 rows.
- [ ] Slaughter: 238 calendar-year rows and 986 monthly rows.
- [ ] Soybean crush and corn for ethanol: 281 monthly rows and 21 calendar-year rows.
- [ ] Crop production forecasts for corn and wheat: 356 rows. The report calls this gap optional: load it, or get
      the operator's decision in writing.

**2. Backend clean** (findings of the report):

- [ ] A (high: it blocks gaps 2, 3, 4, 6, 8 and 9). The parser keys one row for each year. Monthly, point-in-time
      and marketing-year rows of one series get the same date, and the last row wins.
- [ ] B (medium). A fetch of a full commodity can reach the 50,000-row cap of Quick Stats. Fetch each new series by its
      `short_desc`.
- [x] C (low). No USDA timeout or time budget. Done in #317.
- [ ] D (info). NASS revises the current-year estimates, and the upsert writes over them with no history. Fix it, or
      get the operator's decision in writing.

**3. Coverage proof:** [ ] a new proof after USDA-ADD.

**4. Notebook, docstring and tests:** [ ] the rerun and the update for the new data.

## dbnomics() and dbnomics_series(): function_dbnomics.ipynb

Coverage report: `reports/coverage_2026-11-27/dbnomics.md`. Plan row: DBN-ADD.

The table holds 62 series and 36,602 rows. They are equal to DBnomics. But on 2026-11-27, 53 of the 62 series are
stale, because DBnomics stopped its reads of the OECD, Eurostat, the BIS and the World Bank. `dbnomics_series()`
gives a `stale` flag for each series.

**1. Desired coverage** (the report's gaps that DBnomics has now, ranks 1 to 6):

- [ ] 1. Euro-area core HICP, year on year (ECB): 1 series, 357 values.
- [ ] 2. HICP, year on year, for DE, FR, IT, ES and NL (ECB): 5 series, 1,785 values.
- [ ] 3. UK CPI and UK core CPI, year on year (ONS): 2 series, 908 values.
- [ ] 4. UK unemployment rate and UK GDP, quarter on quarter (ONS): 2 series, 953 values.
- [ ] 5. Japan CPI: headline, core and core-core (Statistics Japan): 3 series, 2,046 values.
- [ ] 6. RBA cash rate target, and Australia CPI, trimmed-mean CPI and unemployment (RBA): 4 series, 5,200 values.
      This gap needs finding B first.

The report also asks for a decision on the 53 stale series: keep them with the stale flag, or move them to direct
sources. This decision is for Control and the operator. If the series move to other tables, they leave
`dbnomics()`.

**2. Backend clean** (findings of the report):

- [ ] A (high). The weekly upsert sets `ingested_at` on all the rows, thus the freshness check cannot see a stopped
      series. The check must be done for each series.
- [ ] B (medium). RBA dates are the end of the period, and RBA series have future periods with no value. The parser
      must use the start of the period.
- [ ] C (low). The parser stores each "NA" as a row with a NULL value (185 rows on 2026-11-27).
- [ ] D (low). The AMECO series mix European Commission forecasts with outturns, with no flag and no history.
- [ ] E (info). The upsert writes over revised values, and the table keeps no history.
- [x] F (none). No row cap: the single-series fetch gives the full series.
- [ ] G (low). DBnomics has no request timeout or time budget of its own.
- [x] H (none). One date for each period: no duplicate dates.
- [ ] I (info). The `geo` column is never set. `dbnomics()` takes the country from the series code.
- [ ] J (low). One `commercial_ok` flag for all of DBnomics cannot show the BIS condition or a non-commercial source.

The low and info findings (C, D, E, G, I, J) close with a fix, or when the operator drops them in writing.

**3. Coverage proof:** [ ] a new proof after DBN-ADD.

**4. Notebook, docstring and tests:** [ ] the rerun and the update for the new data. A new dataset (for example ONS)
needs a country rule in `dbnomics._AREA`; the prod test fails until it has one.
