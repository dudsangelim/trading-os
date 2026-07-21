#!/usr/bin/env python3
"""Build a future-aware WIN/WDO contract chain.

This module deliberately keeps the real B3 ticker in every row.  It does not
create a back-adjusted synthetic price series.  Nominal expiry dates are useful
for discovering candidate contracts; the actual roll is chosen later from
observed liquidity, using only information available before the session.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path


MONTH_CODES = {
    1: "F",
    2: "G",
    3: "H",
    4: "J",
    5: "K",
    6: "M",
    7: "N",
    8: "Q",
    9: "U",
    10: "V",
    11: "X",
    12: "Z",
}


@dataclass(frozen=True)
class Contract:
    root: str
    year: int
    month: int
    nominal_expiry: date

    @property
    def ticker(self) -> str:
        return f"{self.root}{MONTH_CODES[self.month]}{self.year % 100:02d}"


def first_weekday(year: int, month: int) -> date:
    """First Monday-Friday date; a B3 holiday can move actual expiry later."""
    current = date(year, month, 1)
    while current.weekday() >= 5:
        current += timedelta(days=1)
    return current


def nearest_wednesday_to_15(year: int, month: int) -> date:
    """Nominal WIN expiry; a B3 holiday can move actual expiry later."""
    anchor = date(year, month, 15)
    candidates = [
        anchor + timedelta(days=offset)
        for offset in range(-3, 4)
        if (anchor + timedelta(days=offset)).weekday() == 2
    ]
    return min(candidates, key=lambda candidate: abs((candidate - anchor).days))


def iter_months(start: date, end: date):
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        yield year, month
        month += 1
        if month == 13:
            year, month = year + 1, 1


def build_chain(root: str, start: date, end: date) -> list[Contract]:
    root = root.upper()
    if root not in {"WIN", "WDO"}:
        raise ValueError("root must be WIN or WDO")

    contracts = []
    for year, month in iter_months(start, end):
        if root == "WIN" and month % 2:
            continue
        expiry = (
            nearest_wednesday_to_15(year, month)
            if root == "WIN"
            else first_weekday(year, month)
        )
        contracts.append(Contract(root, year, month, expiry))
    return contracts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parent / "futures_chain.csv",
    )
    args = parser.parse_args()
    if args.end < args.start:
        parser.error("--end must not precede --start")

    # Include one contract before and two after the requested range.  Those
    # adjacent expiries are needed to measure migration rather than assuming
    # that the calendar date alone determines the roll.
    padded_start = args.start - timedelta(days=62)
    padded_end = args.end + timedelta(days=93)
    records = []
    for root in ("WIN", "WDO"):
        for contract in build_chain(root, padded_start, padded_end):
            records.append(
                {
                    "root": root,
                    "ticker": contract.ticker,
                    "contract_year": contract.year,
                    "contract_month": contract.month,
                    "nominal_expiry": contract.nominal_expiry.isoformat(),
                    "expiry_caveat": (
                        "move to next B3 session if nominal date is not a session"
                    ),
                }
            )

    records.sort(key=lambda row: (row["nominal_expiry"], row["root"]))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)
    print(f"Wrote {args.output} with {len(records)} real contracts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
