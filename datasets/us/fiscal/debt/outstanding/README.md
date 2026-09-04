# U.S. Federal Debt Outstanding

Dataset ID: `us.fiscal.debt.outstanding`

Source: U.S. Department of the Treasury, Bureau of the Fiscal Service — **Debt to the Penny**.

This dataset preserves the three monetary values reported for each Treasury record date:

- debt held by the public;
- intragovernmental holdings;
- total public debt outstanding.

The source reports business-daily observations beginning on 1993-04-01. The repository does not fill weekends or holidays and does not resample the source frequency.

## Source fields

| Source field | Canonical field | Unit |
| --- | --- | --- |
| `debt_held_public_amt` | `debt_held_by_public` | USD |
| `intragov_hold_amt` | `intragovernmental_holdings` | USD |
| `tot_pub_debt_out_amt` | `total_public_debt_outstanding` | USD |

`src_line_nbr` is preserved as provenance rather than as an economic measure.

## Normalization

Normalization changes representation only. Currency strings are validated with exact decimal arithmetic and serialized without floating-point conversion. `record_date` is preserved as an ISO date.

Every canonical row includes `source_record_sha256`, which hashes the exact preserved source record used to produce it.

## Validation

For every record the collector checks:

```text
total_public_debt_outstanding
=
debt_held_by_public + intragovernmental_holdings
```

The collector also verifies source schema, unique periods, ordering, record counts, decimal validity, and canonical-to-raw provenance.

## Commands

```bash
./bin/finance-data sync us.fiscal.debt.outstanding --full
./bin/finance-data sync us.fiscal.debt.outstanding
./bin/finance-data rebuild us.fiscal.debt.outstanding
./bin/finance-data validate us.fiscal.debt.outstanding
```

A normal incremental sync overlaps the latest canonical date by 10 calendar days so source corrections can be detected without requiring a full-history request.
