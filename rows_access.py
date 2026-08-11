from __future__ import annotations

import csv
import json
import tempfile
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

import index
import file_access

router = APIRouter()
MAX_SOURCE_FILE_BYTES = 10_000_000
PreferredFormat = Literal["auto", "csv", "tsv", "json"]
Cell = str | int | float | bool | None


class DatasetRowsResponse(BaseModel):
    dataset: str
    filename: str
    format: str
    columns: list[str]
    rows: list[list[Cell]]
    offset: int
    returnedRows: int
    hasMore: bool
    nextOffset: int | None = None
    provenance: str


def _to_cell(value: object) -> Cell:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _matrix(records: list[dict[str, object]], columns: list[str]) -> list[list[Cell]]:
    return [[_to_cell(record.get(column)) for column in columns] for record in records]


def _read_csv_page(path: Path, delimiter: str, offset: int, limit: int):
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        columns = list(reader.fieldnames or [])
        records: list[dict[str, object]] = []
        for index_no, row in enumerate(reader):
            if index_no < offset:
                continue
            if len(records) > limit:
                break
            records.append(dict(row))
    has_more = len(records) > limit
    records = records[:limit]
    return columns, _matrix(records, columns), has_more


def _read_jsonl_page(path: Path, offset: int, limit: int):
    records: list[dict[str, object]] = []
    columns: list[str] = []
    seen = 0
    with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
        for line in handle:
            if not line.strip():
                continue
            if seen < offset:
                seen += 1
                continue
            if len(records) > limit:
                break
            value = json.loads(line)
            record = value if isinstance(value, dict) else {"value": value}
            records.append(record)
            for key in record:
                key = str(key)
                if key not in columns:
                    columns.append(key)
            seen += 1
    has_more = len(records) > limit
    records = records[:limit]
    columns = columns or ["value"]
    return columns, _matrix(records, columns), has_more


def _read_json_page(path: Path, offset: int, limit: int):
    with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
        value = json.load(handle)
    values = value if isinstance(value, list) else [value]
    selected = values[offset : offset + limit + 1]
    has_more = len(selected) > limit
    selected = selected[:limit]
    records: list[dict[str, object]] = []
    columns: list[str] = []
    for item in selected:
        record = item if isinstance(item, dict) else {"value": item}
        records.append(record)
        for key in record:
            key = str(key)
            if key not in columns:
                columns.append(key)
    columns = columns or ["value"]
    return columns, _matrix(records, columns), has_more


def _read_page(path: Path, filename: str, offset: int, limit: int):
    lower = filename.lower()
    if lower.endswith(".csv"):
        columns, rows, has_more = _read_csv_page(path, ",", offset, limit)
        return "csv", columns, rows, has_more
    if lower.endswith(".tsv"):
        columns, rows, has_more = _read_csv_page(path, "\t", offset, limit)
        return "tsv", columns, rows, has_more
    if lower.endswith(".jsonl"):
        columns, rows, has_more = _read_jsonl_page(path, offset, limit)
        return "jsonl", columns, rows, has_more
    if lower.endswith(".json"):
        columns, rows, has_more = _read_json_page(path, offset, limit)
        return "json", columns, rows, has_more
    raise HTTPException(status_code=415, detail="Paged row retrieval supports CSV, TSV, JSON, and JSONL files.")


@router.get(
    "/api/datasets/{owner}/{slug}/rows",
    operation_id="readKaggleDatasetRows",
    dependencies=[Depends(index.require_bridge_auth)],
    response_model=DatasetRowsResponse,
    summary="Read exact Kaggle rows in pages",
    description=(
        "Fallback for full-data analysis when file attachment is unavailable. Read exact Kaggle rows in pages. "
        "Repeat with nextOffset until hasMore is false, then analyse the collected rows with Code Interpreter."
    ),
)
def dataset_rows(
    owner: str,
    slug: str,
    filename: str | None = Query(default=None, max_length=500),
    prefer: PreferredFormat = Query(default="auto"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=100),
) -> DatasetRowsResponse:
    owner = file_access._safe_ref(owner, "owner")
    slug = file_access._safe_ref(slug, "slug")
    selected, _ = file_access._select(
        file_access._list_files(owner, slug), filename, prefer, MAX_SOURCE_FILE_BYTES
    )

    with tempfile.TemporaryDirectory(prefix="kaggle-row-page-") as directory:
        path = file_access._download(owner, slug, selected, directory)
        actual_size = path.stat().st_size
        if actual_size > MAX_SOURCE_FILE_BYTES:
            raise HTTPException(
                status_code=413,
                detail="Paged row retrieval is limited to Kaggle files at or below 10 MB.",
            )
        fmt, columns, rows, has_more = _read_page(path, selected, offset, limit)

    returned = len(rows)
    return DatasetRowsResponse(
        dataset=f"{owner}/{slug}",
        filename=selected,
        format=fmt,
        columns=columns,
        rows=rows,
        offset=offset,
        returnedRows=returned,
        hasMore=has_more,
        nextOffset=(offset + returned) if has_more else None,
        provenance="Rows read directly from the exact Kaggle file; not reconstructed or invented.",
    )
