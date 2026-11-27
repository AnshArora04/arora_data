# Unfinished notebooks

This folder holds the notebooks of functions that are not finished. The function code and its tests are in the
library, but the data or the notebook is not complete. Do not use these notebooks as a reference for the data.

## function_usda.ipynb: usda() and usda_series()

The table holds only the calendar-year form (NASS reference period "YEAR") of each series: 19 series, 1,570 rows.
NASS has much more. The Coverage check of 2026-11-27 gives these gaps (national NASS rows):

- Monthly prices received for the 7 price series: 8,916 rows, and 642 marketing-year averages. The calendar-year
  prices start only in 1996 (hogs in 1988).
- Acreage planted and harvested for corn, soybeans, wheat and cotton: 1,016 rows.
- Grain stocks on 4 dates each year for corn, soybeans and wheat: 1,105 rows.
- Livestock inventory on given dates: 1,100 rows. This replaces the 2-row "HOGS - INVENTORY" series.
- Cattle price for 500 lb or more: 30 calendar-year rows and 2,291 monthly rows.
- Monthly milk production: 1,228 rows.
- Slaughter: 238 calendar-year rows and 986 monthly rows.
- Soybean crush and corn for ethanol: 281 monthly rows and 21 calendar-year rows.
- Crop production forecasts for corn and wheat: 356 rows.

These steps finish it:

1. Load the USDA-ADD series in the main repo.
2. Get a Coverage proof of the new load.
3. Update the docstring and the tests for the new data, and run the notebook again.
4. Move the notebook back to `notebooks/`, and remove the UNFINISHED marks from the main README.
