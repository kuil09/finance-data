# Source eligibility research

Status: research guidance only. This document does not mean a source is implemented.

`finance-data` stores raw and normalized economic data in Git. Therefore source selection cannot be based only on whether an API is free to call. A source must also be suitable for preservation, reconstruction, and redistribution under the project's storage model.

The machine-readable companion is [`source-candidates.json`](source-candidates.json).

## Decision model

Every candidate source is evaluated on separate axes:

1. **Authority** — primary official publisher, official aggregator, or secondary/commercial source.
2. **Machine access** — REST, SDMX, bulk file, parameterized download, or non-machine interface.
3. **Access cost** — free, registration/key required, quota-limited, mixed, or paid.
4. **Revision semantics** — whether corrections, vintages, amendments, or revisions can be represented.
5. **Preservation rights** — whether raw responses may be cached/archived in this repository.
6. **Redistribution rights** — whether checked-in raw/normalized data may be redistributed.
7. **Third-party rights** — whether an official dataset embeds data whose rights belong to another provider.

A technically accessible source can still be rejected if its rights conflict with immutable raw-data preservation.

## Grades

| Grade | Meaning |
| --- | --- |
| **A** | Strong default candidate: official/reliable, free machine access, and no known project-level blocker. Dataset-level license checks still apply. |
| **B** | Official free API/interface, but authentication, quota, redistribution, commercial-use, or dataset-specific rights need explicit review before ingestion. |
| **C** | Official machine access exists, but it is not a clean general API or access/licensing varies materially by product. |
| **D** | Do not use as a default canonical source: commercial/private access, redistribution restrictions, or terms incompatible with Git-backed preservation. |

These grades describe **source eligibility**, not data quality and not implementation status.

## Strong free official candidates

The research identified the following as the main pool for future source work.

### United States

- **U.S. Treasury Fiscal Data — A.** Official REST source for federal fiscal datasets. Already used by `us.fiscal.debt.outstanding`.
- **Bureau of Economic Analysis — A.** Free official API for GDP, PCE, personal income, industry and international accounts.
- **Census Bureau — A.** Free official API for retail, trade, construction, housing and economic census data; current access policy uses a free API key.
- **Energy Information Administration — A.** Free official API v2 for oil, petroleum inventory, natural gas and electricity. This remains the best second-source candidate because it exercises different frequencies and dimensions from Treasury.
- **SEC EDGAR — A.** Free official machine interfaces for submissions, company facts and filings.
- **Bureau of Labor Statistics — B.** Free official API for CPI/PPI/employment/wages; registration changes request limits and dataset/revision behavior must be modeled explicitly.
- **New York Fed Markets Data APIs — B.** Preferred source for SOFR/EFFR/OBFR and related money-market/reference-rate series when those values appear through FRED or commercial terminals.

### Central banks and international institutions

- **ECB Data Portal — A.** SDMX/REST access for euro-area monetary and financial statistics.
- **World Bank Indicators — A.** Free public API; check third-party rights per dataset.
- **OECD Data Explorer — A.** Free SDMX API for CLI, GDP, debt, trade, labour and production series.
- **Eurostat — A.** Free REST/SDMX access for EU statistics.
- **UK ONS — A.** Free official API for UK macro and labour statistics.
- **BIS — B.** Official SDMX statistics, but reuse/redistribution terms should be confirmed per series before raw mirroring.
- **Bank of Japan — B.** Official machine API; confirm reuse conditions before repository preservation.
- **IMF Data API — B.** Free SDMX access with broad reuse, but individual datasets can contain separate rights or attribution obligations.
- **WTO API — B.** Free official developer API with subscription key; review the service's reuse terms before mirroring.

### Korea

- **Bank of Korea ECOS — A.** Primary Korean monetary/macro source and a high-priority candidate.
- **KOSIS — A.** Broad official statistics API. Domestic-statistics reuse is generally permissive, while international/North-Korea datasets need separate review.
- **Korea Customs Service open APIs — A.** Strong primary source for commodity/country trade statistics; evaluate each Public Data Portal service license.
- **MOLIT real-estate transaction APIs — A.** Official transaction data; evaluate each Public Data Portal service license.
- **OpenDART — B.** Free official filings API, but repository-wide mirroring should be preceded by a terms review.
- **KRX Open API — B.** Official market source; redistribution and commercial-use rights are an explicit gate.
- **KSD/SEIBro — B.** Official data is available through open-data services, but some services contain non-commercial or third-party restrictions.

## Official but conditional machine sources

These are worth retaining in the research registry but should not be treated as ordinary API implementations yet.

- **Bank of England IADB — C.** Parameterized official CSV downloads are machine-readable, but this is not the same contract as a stable general REST/SDMX API.
- **FHFA HPI — C.** Excellent official bulk source in CSV/JSON/XML/SQL forms; model as a bulk source rather than pretending it is a REST API.
- **IEA — C.** Official and high-quality, but API/download access and reuse rights vary by product; important oil-market products may be subscription products.
- **UNCTADstat — C.** Official free data portal, but a stable general developer API was not confirmed in this research pass.
- **OPEC — C.** Official statistics are useful, but a free general developer API was not confirmed.
- **PBOC — C.** Official statistical publication exists, but a stable public developer API suitable for this project was not confirmed.

## Important exclusion: FRED / ALFRED

FRED and ALFRED are extremely useful for discovery and cross-checking, and their API is free. They are nevertheless **D for canonical ingestion under the current project model**.

The project deliberately preserves raw source responses and redistributes checked-in normalized data. Current FRED terms create a conflict with storing/archiving/caching FRED content and redistributing stored copies. Therefore:

- do not build a FRED-backed canonical dataset by default;
- do not use ALFRED vintages as a workaround for source revision tracking;
- use FRED/ALFRED for discovery, identification, and manual cross-checking only unless the terms change;
- follow each series back to its original publisher whenever possible.

Examples:

```text
FRED CPI series
    -> Bureau of Labor Statistics API

FRED GDP / PCE series
    -> Bureau of Economic Analysis API

FRED federal fiscal series
    -> U.S. Treasury / relevant federal publisher

FRED SOFR / EFFR series
    -> Federal Reserve Bank of New York Markets Data API
```

This distinction is important because `official aggregator` does not automatically mean `safe to archive and redistribute`.

## Commercial and secondary sources

The research surveys named many services because economic media frequently cites them. They are still poor defaults for this repository.

**D — commercial data platforms:** Bloomberg Professional, FactSet, LSEG/Refinitiv, Trading Economics and Koyfin. Their data may be excellent, but access and redistribution depend on commercial product terms. Prefer the original government, central-bank or international-organization source.

**D — investment-bank research:** Goldman Sachs, JPMorgan, Morgan Stanley, Bank of America, Citi and UBS reports. These are analysis layers, not open canonical raw-data sources.

**D — commercial industry research:** Gartner, IDC, TrendForce, Clarksons Research, Wood Mackenzie and S&P Global. Do not ingest a headline series merely because it appears in public media; only reconsider a specifically identified open dataset whose rights are independently verified.

**D — news organizations:** Bloomberg News, Reuters, Financial Times, Wall Street Journal, CNBC, Nikkei and Semafor are reporting/interpretation layers, not canonical numeric data sources for `finance-data`.

## Aggregator-to-primary-source rule

When an economic channel cites an aggregator or terminal, `finance-data` should trace the value upstream before selecting a source.

```text
YouTube / media
    -> terminal, chart or aggregator
        -> primary official producer
            -> finance-data
```

Typical substitutions:

| Cited layer | Prefer |
| --- | --- |
| FRED employment/inflation | BLS |
| FRED GDP/PCE | BEA |
| FRED SOFR/EFFR | New York Fed |
| Bloomberg Treasury/fiscal chart | U.S. Treasury |
| Bloomberg/terminal company filing | SEC EDGAR or OpenDART |
| Macro dashboard using Korean public statistics | BOK ECOS, KOSIS, Customs or MOLIT as appropriate |

If the commercial or aggregate layer adds a proprietary derived value that does not exist at the primary publisher, that value is not equivalent to the raw official series and should not be silently recreated.

## Pre-implementation license gate

Before any candidate is promoted into `sources/`, the implementing issue must record:

- official API/interface documentation;
- authentication and quota requirements;
- exact dataset/table identifiers;
- source update and revision semantics;
- raw-response retention permission;
- normalized-data redistribution permission;
- attribution requirements;
- third-party fields or embedded licensed content;
- commercial-use restrictions if relevant;
- known source anomalies that affect deterministic validation.

Unknown rights are not interpreted as permission.

## Priority for future work

The research changes the order in which new sources should be explored.

1. **EIA** — validates multidimensional, daily/weekly/hourly and commodity/energy ingestion against the Treasury reference implementation.
2. **BLS + BEA** — core U.S. inflation, labour, GDP and consumption data while avoiding FRED as canonical storage.
3. **New York Fed** — rates and money-market plumbing directly from the primary publisher.
4. **BOK ECOS + KOSIS + Korea Customs** — Korean macro and trade coverage.
5. **ECB / OECD / World Bank / Eurostat** — reusable SDMX and cross-country data.
6. **SEC EDGAR / OpenDART** — event/filing-shaped datasets, which test a substantially different temporal model.

This priority is a research roadmap only. Each concrete source or dataset still requires its own issue, manifest design, validation rules and implementation review.
