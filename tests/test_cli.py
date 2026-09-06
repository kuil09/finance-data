from __future__ import annotations

import unittest

from finance_data.cli import build_parser


class CLITests(unittest.TestCase):
    def test_sync_does_not_force_a_day_overlap(self):
        args = build_parser().parse_args(["sync", "us.prices.cpi_u.all_items"])
        self.assertIsNone(args.overlap_days)
        self.assertIsNone(args.overlap_years)

    def test_sync_accepts_explicit_overlap_units(self):
        parser = build_parser()
        day_args = parser.parse_args(
            ["sync", "us.energy.petroleum.crude_oil.commercial_stocks", "--overlap-days", "35"]
        )
        year_args = parser.parse_args(
            ["sync", "us.prices.cpi_u.all_items", "--overlap-years", "2"]
        )
        self.assertEqual(day_args.overlap_days, 35)
        self.assertEqual(year_args.overlap_years, 2)


if __name__ == "__main__":
    unittest.main()
