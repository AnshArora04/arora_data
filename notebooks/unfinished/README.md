# Unfinished notebooks

This folder holds the notebooks of functions that are not finished. The function code and its tests are in the
library, but the data or the notebook is not complete. Do not use these notebooks as a reference for the data.

## function_eia.ipynb: eia() and eia_series()

- The 14 weekly series are not loaded: 8 from the Weekly Petroleum Status Report and 6 for natural gas storage. On
  2026-11-26, each request to the EIA data API took 60 seconds or more, and the backfill failed.
- The names of 3 natural gas storage regions are wrong. The B3 fix in the main repo corrects them.
- The notebook shows only the 7 monthly series. Its 3 weekly cells print "The table holds no weekly series yet."

These steps finish it:

1. Get a clean EIA load of all the series, with the correct region names.
2. Run the notebook again.
3. Move the notebook back to `notebooks/`, and remove the UNFINISHED marks from the main README.
