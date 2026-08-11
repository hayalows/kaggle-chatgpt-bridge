from __future__ import annotations

import csv
import io
import tempfile

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

import index
import file_access

router = APIRouter()
MAX_SOURCE_FILE_BYTES = 3_000_000


class CsvChunkResponse(BaseModel):
    dataset: str
    filename: str
    csvText: str
    offset: int
    returnedRows: int
    hasMore: bool
    nextOffset: int
    provenance: str


def _read_csv_chunk(path, filename: str, offset: int, limit: int):
    lower = filename.lower()
    if lower.endswith(".csv"):
        delimiter = ","
    elif lower.endswith(".tsv"):
        delimiter = "\t"
    else:
        raise HTTPException(status_code=415, detail="This action supports CSV and TSV files only.")

    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        reader = csv.reader(handle, delimiter=delimiter)
        try:
            header = next(reader)
        except StopIteration:
            return "", 0, False

        rows = []
        for row_index, row in enumerate(reader):
            if row_index < offset:
                continue
            rows.append(row)
            if len(rows) > limit:
                break

    has_more = len(rows) > limit
    rows = rows[:limit]

    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(header)
    writer.writerows(rows)
    return output.getvalue(), len(rows), has_more


@router.get(
    "/api/dataset-csv",
    operation_id="readKaggleDatasetRows",
    dependencies=[Depends(index.require_bridge_auth)],
    response_model=CsvChunkResponse,
    summary="Read real Kaggle CSV data",
    description=(
        "Use for full analysis. Returns exact Kaggle CSV rows as plain CSV text. "
        "Repeat with nextOffset while hasMore is true."
    ),
)
def read_dataset_csv(
    owner: str = Query(..., min_length=1, max_length=100),
    slug: str = Query(..., min_length=1, max_length=160),
    filename: str = Query(default="", max_length=500),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=500),
) -> CsvChunkResponse:
    owner = file_access._safe_ref(owner, "owner")
    slug = file_access._safe_ref(slug, "slug")

    selected, _ = file_access._select(
        file_access._list_files(owner, slug), filename or None, "csv", MAX_SOURCE_FILE_BYTES
    )

    with tempfile.TemporaryDirectory(prefix="kaggle-csv-text-") as directory:
        path = file_access._download(owner, slug, selected, directory)
        actual_size = path.stat().st_size
        if actual_size > MAX_SOURCE_FILE_BYTES:
            raise HTTPException(
                status_code=413,
                detail="CSV text transfer is limited to Kaggle files at or below 3 MB.",
            )
        csv_text, returned_rows, has_more = _read_csv_chunk(path, selected, offset, limit)

    next_offset = offset + returned_rows if has_more else -1
    return CsvChunkResponse(
        dataset=f"{owner}/{slug}",
        filename=selected,
        csvText=csv_text,
        offset=offset,
        returnedRows=returned_rows,
        hasMore=has_more,
        nextOffset=next_offset,
        provenance="Exact rows read from the Kaggle CSV file; not reconstructed or invented.",
    )
