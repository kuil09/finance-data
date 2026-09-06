# U.S. Current-Dollar GDP

Canonical dataset id: `us.national_accounts.gdp.current_dollars`

This dataset preserves the quarterly BEA NIPA current-dollar GDP level identified by Table 1.1.5 (`T10105`), line 1, series `A191RC`.

The production collector reads BEA's official NIPA flat files directly: `SeriesRegister.txt`, `TablesRegister.txt`, and `nipadataQ.txt`. It verifies the series-to-table/line mapping before accepting observations. A registered BEA API adapter is also available through `BEA_API_KEY`, but no credential is required to reproduce this dataset.

Quarterly values are kept at the source convention: millions of current dollars, seasonally adjusted at annual rates. The repository does not calculate GDP growth or convert the series to real dollars.

Every sync fetches the complete selected quarterly series because annual and comprehensive NIPA revisions can change historical observations. Older raw snapshots remain preserved; the latest retrieval is canonical.

```bash
./bin/finance-data validate us.national_accounts.gdp.current_dollars
./bin/finance-data rebuild us.national_accounts.gdp.current_dollars
./bin/finance-data sync us.national_accounts.gdp.current_dollars
```
