# U.S. Federal Debt Outstanding

Dataset ID: `us.fiscal.debt.outstanding`

Source: U.S. Department of the Treasury, Bureau of the Fiscal Service — **Debt to the Penny**.

This dataset preserves the three monetary fields reported for each Treasury record date:

- debt held by the public;
- intragovernmental holdings;
- total public debt outstanding.

The source does not populate all three fields for the complete historical range. From 1993-04-01 through 2005-03-30, the API returns the literal value `null` for both component amounts while total public debt outstanding remains populated. The first observed record with both component amounts is 2005-03-31. These source nulls are preserved as `null`; they are never treated as zero or inferred from another series.

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

When both component amounts are present, the collector checks:

```text
total_public_debt_outstanding
=
debt_held_by_public + intragovernmental_holdings
```

For the historical total-only range, both component fields must be null together. On and after 2005-03-31 both components are required. The collector also verifies source schema, unique periods, ordering, record counts, decimal validity, and canonical-to-raw provenance.

## Commands

```bash
./bin/finance-data sync us.fiscal.debt.outstanding --full
./bin/finance-data sync us.fiscal.debt.outstanding
./bin/finance-data rebuild us.fiscal.debt.outstanding
./bin/finance-data validate us.fiscal.debt.outstanding
```

A normal incremental sync overlaps the latest canonical date by 10 calendar days so source corrections can be detected without requiring a full-history request.
