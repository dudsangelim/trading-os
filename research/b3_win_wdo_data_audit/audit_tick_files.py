#!/usr/bin/env python3
"""Streaming quality audit for B3 tick-by-tick ZIP exports."""

from __future__ import annotations

import csv
import json
import sys
import zipfile
from collections import Counter
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path


RAW_DIR = Path(__file__).resolve().parent / "raw"
EXPECTED_COLUMNS = [
    "DataReferencia",
    "CodigoInstrumento",
    "AcaoAtualizacao",
    "PrecoNegocio",
    "QuantidadeNegociada",
    "HoraFechamento",
    "CodigoIdentificadorNegocio",
    "TipoSessaoPregao",
    "DataNegocio",
    "CodigoParticipanteComprador",
    "CodigoParticipanteVendedor",
]


def parse_time(value: str) -> tuple[int, str]:
    value = value.strip().zfill(9)
    if len(value) != 9 or not value.isdigit():
        raise ValueError(value)
    hour, minute, second, millis = (
        int(value[:2]),
        int(value[2:4]),
        int(value[4:6]),
        int(value[6:]),
    )
    if hour > 23 or minute > 59 or second > 59 or millis > 999:
        raise ValueError(value)
    return ((hour * 3600 + minute * 60 + second) * 1000 + millis), value


def audit(path: Path) -> dict:
    result = {
        "file": path.name,
        "compressed_bytes": path.stat().st_size,
        "valid_zip": False,
        "rows": 0,
        "malformed_rows": 0,
        "bad_timestamp_rows": 0,
        "bad_numeric_rows": 0,
        "tick_misaligned_rows": 0,
        "time_backwards_rows": 0,
        "duplicate_consecutive_rows": 0,
        "duplicate_consecutive_trade_ids": 0,
        "zero_or_negative_quantity_rows": 0,
    }
    if path.stat().st_size == 0:
        result["error"] = "empty_http_200_response"
        return result

    try:
        archive = zipfile.ZipFile(path)
        result["valid_zip"] = archive.testzip() is None
        members = archive.infolist()
        result["members"] = [m.filename for m in members]
        result["uncompressed_bytes"] = sum(m.file_size for m in members)
        if len(members) != 1:
            result["error"] = "unexpected_member_count"
            return result
    except zipfile.BadZipFile:
        result["error"] = "bad_zip"
        return result

    dates = Counter()
    instruments = Counter()
    actions = Counter()
    sessions = Counter()
    buyer_codes = Counter()
    seller_codes = Counter()
    minute_counts = Counter()
    first_time = None
    last_time = None
    min_price = None
    max_price = None
    total_quantity = 0
    prev_time = None
    prev_row = None
    prev_trade_id = None
    delete_ids: set[str] = set()
    delete_rows = 0
    delete_quantity = 0

    with archive.open(members[0]) as raw:
        text = (line.decode("utf-8-sig", "strict") for line in raw)
        reader = csv.DictReader(text, delimiter=";")
        result["columns"] = reader.fieldnames
        result["schema_matches"] = reader.fieldnames == EXPECTED_COLUMNS
        for row in reader:
            result["rows"] += 1
            if None in row or any(row.get(c) is None for c in EXPECTED_COLUMNS):
                result["malformed_rows"] += 1
                continue
            dates[row["DataNegocio"]] += 1
            instruments[row["CodigoInstrumento"]] += 1
            actions[row["AcaoAtualizacao"]] += 1
            if row["AcaoAtualizacao"] == "2":
                delete_ids.add(row["CodigoIdentificadorNegocio"])
                delete_rows += 1
            sessions[row["TipoSessaoPregao"]] += 1
            buyer_codes[row["CodigoParticipanteComprador"]] += 1
            seller_codes[row["CodigoParticipanteVendedor"]] += 1

            try:
                time_ms, normalized_time = parse_time(row["HoraFechamento"])
                minute_counts[normalized_time[:4]] += 1
                first_time = normalized_time if first_time is None else first_time
                last_time = normalized_time
                if prev_time is not None and time_ms < prev_time:
                    result["time_backwards_rows"] += 1
                prev_time = time_ms
            except ValueError:
                result["bad_timestamp_rows"] += 1

            try:
                price = Decimal(row["PrecoNegocio"].replace(".", "").replace(",", "."))
                quantity = int(row["QuantidadeNegociada"])
                total_quantity += quantity
                if row["AcaoAtualizacao"] == "2":
                    delete_quantity += quantity
                if quantity <= 0:
                    result["zero_or_negative_quantity_rows"] += 1
                min_price = price if min_price is None else min(min_price, price)
                max_price = price if max_price is None else max(max_price, price)
                tick = Decimal("5") if row["CodigoInstrumento"].startswith("WIN") else Decimal("0.5")
                if price % tick != 0:
                    result["tick_misaligned_rows"] += 1
            except (InvalidOperation, ValueError):
                result["bad_numeric_rows"] += 1

            row_tuple = tuple(row[c] for c in EXPECTED_COLUMNS)
            if row_tuple == prev_row:
                result["duplicate_consecutive_rows"] += 1
            trade_id = row["CodigoIdentificadorNegocio"]
            if trade_id == prev_trade_id:
                result["duplicate_consecutive_trade_ids"] += 1
            prev_row = row_tuple
            prev_trade_id = trade_id

    # A delete record repeats the identifier and quantity of an earlier new
    # record. Both the original and the delete line must disappear from the
    # effective trade tape.
    original_deleted_rows = 0
    original_deleted_quantity = 0
    if delete_ids:
        with archive.open(members[0]) as raw:
            text = (line.decode("utf-8-sig", "strict") for line in raw)
            reader = csv.DictReader(text, delimiter=";")
            for row in reader:
                if (
                    row["AcaoAtualizacao"] == "0"
                    and row["CodigoIdentificadorNegocio"] in delete_ids
                ):
                    original_deleted_rows += 1
                    original_deleted_quantity += int(row["QuantidadeNegociada"])

    result.update(
        {
            "dates": dict(dates),
            "instruments": dict(instruments),
            "actions": dict(actions),
            "sessions": dict(sessions),
            "buyer_codes": dict(buyer_codes),
            "seller_codes": dict(seller_codes),
            "first_time": first_time,
            "last_time": last_time,
            "minutes_with_trades": len(minute_counts),
            "empty_minutes_between_first_last": (
                None
                if first_time is None or last_time is None
                else ((parse_time(last_time)[0] - parse_time(first_time)[0]) // 60000 + 1 - len(minute_counts))
            ),
            "min_price": str(min_price) if min_price is not None else None,
            "max_price": str(max_price) if max_price is not None else None,
            "total_quantity": total_quantity,
            "delete_ids": sorted(delete_ids),
            "effective_rows": result["rows"] - delete_rows - original_deleted_rows,
            "effective_quantity": total_quantity - delete_quantity - original_deleted_quantity,
        }
    )
    return result


def main() -> int:
    files = sorted(RAW_DIR.glob("*.zip"))
    if not files:
        print("No ZIP files found", file=sys.stderr)
        return 1
    results = []
    for path in files:
        print(f"Auditing {path.name}...", file=sys.stderr, flush=True)
        results.append(audit(path))
    rendered = json.dumps(results, ensure_ascii=False, indent=2)
    output_path = Path(__file__).resolve().parent / "audit_results.json"
    output_path.write_text(rendered + "\n", encoding="utf-8")
    print(f"Wrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
