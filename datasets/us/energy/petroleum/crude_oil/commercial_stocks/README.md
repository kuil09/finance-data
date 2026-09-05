# U.S. Commercial Crude Oil Stocks

Dataset id: `us.energy.petroleum.crude_oil.commercial_stocks`

Primary source: U.S. Energy Information Administration, Petroleum bulk dataset, series `PET.WCESTUS1.W`.

The source publishes weekly U.S. ending stocks excluding the Strategic Petroleum Reserve in **thousand barrels**. `finance-data` preserves the weekly period and source value without creating changes, averages, daily interpolations, or market signals.

## Source access

The reproducible default collector uses EIA's official keyless Petroleum bulk package:

`https://www.eia.gov/opendata/bulk/PET.zip`

The selected series was verified to carry `copyright: None` and `source: EIA, U.S. Energy Information Administration`. Series with third-party copyright metadata are not eligible for repository mirroring under this source contract.

EIA API v2 also supports legacy series identifiers through `/v2/seriesid/{series_id}` with a free API key, but an API key is not required to rebuild this dataset from the official bulk package.

## Normalized data

```text
data/normalized/us.energy.petroleum.crude_oil.commercial_stocks/
  year=1982/data.csv
  ...
```

Columns:

- `period`: ISO observation date
- `stock_thousand_barrels`: source-published stock value
- `source_record_sha256`: hash of the preserved source observation record

## Commands

```bash
./bin/finance-data sync us.energy.petroleum.crude_oil.commercial_stocks --full
./bin/finance-data validate us.energy.petroleum.crude_oil.commercial_stocks
./bin/finance-data rebuild us.energy.petroleum.crude_oil.commercial_stocks
```
