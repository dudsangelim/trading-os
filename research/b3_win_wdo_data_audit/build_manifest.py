#!/usr/bin/env python3
"""Build a compact provenance/integrity manifest for archived B3 ZIP files."""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
RAW = ROOT / "raw"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


records = []
for path in sorted(RAW.glob("*.zip")):
    record = {
        "file": path.name,
        "source_url": "https://drp.b3.com.br/rapinegocios/tickercsv/"
        + path.stem.split("_")[0]
        + "/"
        + path.stem.split("_")[1],
        "compressed_bytes": path.stat().st_size,
    }
    if path.stat().st_size == 0:
        record["status"] = "empty_http_200_response"
    else:
        record["sha256"] = sha256(path)
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            record["status"] = "zip_opened"
            record["members"] = [x.filename for x in infos]
            record["uncompressed_bytes"] = sum(x.file_size for x in infos)
            if len(infos) == 1:
                with archive.open(infos[0]) as stream:
                    record["header"] = stream.readline().decode("utf-8-sig").rstrip()
    records.append(record)

(ROOT / "MANIFEST.json").write_text(
    json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
print(f"Wrote {ROOT / 'MANIFEST.json'} with {len(records)} records")
