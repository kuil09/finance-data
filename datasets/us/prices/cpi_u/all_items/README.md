# U.S. CPI-U All Items

Canonical dataset id: `us.prices.cpi_u.all_items`

This dataset preserves the monthly U.S. Bureau of Labor Statistics CPI-U All Items series `CUUR0000SA0` at its native monthly frequency. Values are the source index (1982–84 = 100); this repository does not calculate inflation rates or seasonally adjust the series.

The BLS Public Data API is queried directly. A registration key is optional through `BLS_REGISTRATION_KEY`; a clean checkout can use the unregistered endpoint. Historical collection is divided into at most 10-year windows to stay within the unregistered API contract.

BLS observations marked unavailable are retained. In particular, October 2025 is stored with `index_value=null`, `observation_status=unavailable`, and the source footnote explaining the lapse in appropriations. Missing source observations are never replaced with estimates.

Use:

```bash
./bin/finance-data validate us.prices.cpi_u.all_items
./bin/finance-data rebuild us.prices.cpi_u.all_items
./bin/finance-data sync us.prices.cpi_u.all_items
```
