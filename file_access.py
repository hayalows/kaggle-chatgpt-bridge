from __future__ import annotations

import base64
import csv
import hashlib
import hmac
import json
import mimetypes
import os
import re
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import index

router = APIRouter()
MAX_FILE_BYTES = 10_000_000
SIGNED_URL_TTL = 300
PreferredFormat = Literal["auto", "csv", "tsv", "parquet", "json", "xlsx", "sqlite", "zip"]


class FileTransferResponse(BaseModel):
    openaiFileResponse: list[str]
    dataset: str
    filename: str
    sizeBytes: int | None = None
    mimeType: str
    provenance: str
    expiresInSeconds: int


class DatasetPreviewResponse(BaseModel):
    dataset: str
    filename: str
    sizeBytes: int
    format: str
    columns: list[str]
    rows: list[list[str | int | float | bool | None]]
    returnedRows: int
    truncated: bool
    provenance: str


def _safe_ref(value: str, label: str) -> str:
    if not value or len(value) > 100 or not re.fullmatch(r"[A-Za-z0-9._-]+", value):
        raise HTTPException(status_code=422, detail=f"Invalid {label}")
    return value


def _safe_filename(filename: str) -> str:
    if not filename or len(filename) > 500:
        raise HTTPException(status_code=422, detail="Invalid filename")
    path = PurePosixPath(filename)
    if path.is_absolute() or ".." in path.parts or "\x00" in filename:
        raise HTTPException(status_code=422, detail="Unsafe filename")
    return str(path)


def _size_bytes(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    if text.isdigit():
        return int(text)
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*(B|KB|MB|GB|KIB|MIB|GIB)", text, re.I)
    if not match:
        return None
    amount = float(match.group(1))
    unit = match.group(2).upper()
    scale = {"B": 1, "KB": 1000, "MB": 1000**2, "GB": 1000**3,
             "KIB": 1024, "MIB": 1024**2, "GIB": 1024**3}[unit]
    return int(amount * scale)


def _filename(row: dict[str, str]) -> str | None:
    return next((row.get(k) for k in ("name", "fileName", "filename", "path") if row.get(k)), None)


def _row_size(row: dict[str, str]) -> int | None:
    for key in ("size", "sizeBytes", "bytes", "fileSize"):
        if key in row:
            parsed = _size_bytes(row.get(key))
            if parsed is not None:
                return parsed
    return None


def _list_files(owner: str, slug: str) -> list[dict[str, str]]:
    output = index.run_kaggle(["datasets", "files", f"{owner}/{slug}", "-v", "--page-size", "200"])
    return index.parse_csv_output(output)


def _mime(filename: str) -> str:
    lower = filename.lower()
    overrides = {
        ".csv": "text/csv", ".tsv": "text/tab-separated-values", ".jsonl": "application/x-ndjson",
        ".parquet": "application/vnd.apache.parquet", ".sqlite": "application/vnd.sqlite3",
        ".db": "application/vnd.sqlite3", ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xls": "application/vnd.ms-excel", ".zip": "application/zip",
    }
    for suffix, value in overrides.items():
        if lower.endswith(suffix):
            return value
    return mimetypes.guess_type(filename)[0] or "application/octet-stream"


def _allowed_file(filename: str) -> bool:
    mime = _mime(filename)
    lower = filename.lower()
    return not mime.startswith(("image/", "video/")) and not lower.endswith(
        (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".mp4", ".mov", ".avi", ".mkv", ".webm")
    )


def _score(filename: str, prefer: PreferredFormat) -> int:
    lower = filename.lower()
    base = {".csv": 100, ".tsv": 95, ".parquet": 90, ".json": 80, ".jsonl": 78,
            ".xlsx": 70, ".xls": 68, ".sqlite": 60, ".db": 58, ".zip": 40, ".txt": 30}
    score = next((v for ext, v in base.items() if lower.endswith(ext)), 0)
    if prefer != "auto":
        wanted = {"csv": (".csv",), "tsv": (".tsv",), "parquet": (".parquet",),
                  "json": (".json", ".jsonl"), "xlsx": (".xlsx", ".xls"),
                  "sqlite": (".sqlite", ".db"), "zip": (".zip",)}[prefer]
        if lower.endswith(wanted):
            score += 1000
    return score


def _select(rows: list[dict[str, str]], requested: str | None, prefer: PreferredFormat, max_bytes: int) -> tuple[str, int | None]:
    if requested:
        requested = _safe_filename(requested)
        for row in rows:
            if _filename(row) == requested:
                size = _row_size(row)
                if size is not None and size > max_bytes:
                    raise HTTPException(status_code=413, detail=f"{requested} exceeds the {max_bytes}-byte Action return limit.")
                if not _allowed_file(requested):
                    raise HTTPException(status_code=415, detail="Image and video files cannot be returned through this Action.")
                return requested, size
        raise HTTPException(status_code=404, detail="Filename not found in dataset")

    candidates: list[tuple[int, int, str, int | None]] = []
    for row in rows:
        name = _filename(row)
        if not name:
            continue
        try:
            name = _safe_filename(name)
        except HTTPException:
            continue
        size = _row_size(row)
        if size is not None and size > max_bytes:
            continue
        if not _allowed_file(name):
            continue
        score = _score(name, prefer)
        if score:
            candidates.append((score, -(size if size is not None else max_bytes), name, size))
    if not candidates:
        raise HTTPException(status_code=404, detail="No analysis-friendly file within the 10 MB Action file limit was found.")
    candidates.sort(reverse=True)
    _, _, name, size = candidates[0]
    return name, size


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _b64d(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _sign(owner: str, slug: str, filename: str, size: int | None) -> str:
    secret = os.getenv("BRIDGE_API_KEY")
    if not secret:
        raise HTTPException(status_code=503, detail="BRIDGE_API_KEY is not configured")
    payload = {"owner": owner, "slug": slug, "filename": filename, "size": size, "exp": int(time.time()) + SIGNED_URL_TTL}
    body = _b64e(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
    return f"{body}.{_b64e(sig)}"


def _verify(token: str) -> dict[str, object]:
    secret = os.getenv("BRIDGE_API_KEY")
    if not secret:
        raise HTTPException(status_code=503, detail="BRIDGE_API_KEY is not configured")
    try:
        body, sig_text = token.split(".", 1)
        expected = hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(_b64d(sig_text), expected):
            raise ValueError("bad signature")
        payload = json.loads(_b64d(body))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=401, detail="Invalid file token") from exc
    if int(payload.get("exp", 0)) < int(time.time()):
        raise HTTPException(status_code=410, detail="File URL expired")
    payload["owner"] = _safe_ref(str(payload.get("owner", "")), "owner")
    payload["slug"] = _safe_ref(str(payload.get("slug", "")), "slug")
    payload["filename"] = _safe_filename(str(payload.get("filename", "")))
    return payload


def _download(owner: str, slug: str, filename: str, directory: str) -> Path:
    filename = _safe_filename(filename)
    index.run_kaggle(["datasets", "download", f"{owner}/{slug}", "-f", filename, "-p", directory, "-q", "-o"], timeout=35)
    exact = Path(directory) / filename
    if exact.is_file():
        return exact
    basename = PurePosixPath(filename).name
    matches = [p for p in Path(directory).rglob("*") if p.is_file() and p.name == basename]
    if len(matches) == 1:
        return matches[0]
    files = [p for p in Path(directory).rglob("*") if p.is_file()]
    if len(files) == 1:
        return files[0]
    raise HTTPException(status_code=502, detail="Kaggle reported success but the requested file was not found")


def _cell(value: object) -> str | int | float | bool | None:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _matrix(records: list[dict[str, object]], columns: list[str]) -> list[list[str | int | float | bool | None]]:
    return [[_cell(record.get(column)) for column in columns] for record in records]


def _preview(path: Path, filename: str, limit: int):
    lower = filename.lower()
    if lower.endswith((".csv", ".tsv")):
        delimiter = "\t" if lower.endswith(".tsv") else ","
        with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
            reader = csv.DictReader(f, delimiter=delimiter)
            columns = list(reader.fieldnames or [])
            records, truncated = [], False
            for i, row in enumerate(reader):
                if i >= limit:
                    truncated = True
                    break
                records.append(dict(row))
        return ("tsv" if delimiter == "\t" else "csv"), columns, _matrix(records, columns), truncated
    if lower.endswith(".jsonl"):
        records, columns, truncated = [], [], False
        with path.open("r", encoding="utf-8-sig", errors="replace") as f:
            for line in f:
                if not line.strip():
                    continue
                if len(records) >= limit:
                    truncated = True
                    break
                value = json.loads(line)
                rec = value if isinstance(value, dict) else {"value": value}
                records.append(rec)
                for key in rec:
                    if str(key) not in columns:
                        columns.append(str(key))
        return "jsonl", columns or ["value"], _matrix(records, columns or ["value"]), truncated
    if lower.endswith(".json"):
        with path.open("r", encoding="utf-8-sig", errors="replace") as f:
            value = json.load(f)
        values = value if isinstance(value, list) else [value]
        selected, truncated = values[:limit], len(values) > limit
        records, columns = [], []
        for item in selected:
            rec = item if isinstance(item, dict) else {"value": item}
            records.append(rec)
            for key in rec:
                if str(key) not in columns:
                    columns.append(str(key))
        return "json", columns or ["value"], _matrix(records, columns or ["value"]), truncated
    raise HTTPException(status_code=415, detail="Preview supports CSV, TSV, JSON, and JSONL. Return other formats to Code Interpreter instead.")


@router.get(
    "/api/datasets/{owner}/{slug}/analysis-file",
    operation_id="getKaggleDatasetFileForAnalysis",
    dependencies=[Depends(index.require_bridge_auth)],
    response_model=FileTransferResponse,
    summary="Return an exact Kaggle file to the GPT conversation",
    description=("Use this whenever the user asks to analyse, clean, model, forecast, train, inspect rows, or work with real data. "
                 "It returns the exact Kaggle file using openaiFileResponse so Code Interpreter can work on real rows. "
                 "If filename is omitted, a useful tabular file under 10 MB is selected automatically."),
)
def analysis_file(
    owner: str,
    slug: str,
    filename: str | None = Query(default=None, max_length=500),
    prefer: PreferredFormat = Query(default="auto"),
    max_bytes: int = Query(default=MAX_FILE_BYTES, ge=1, le=MAX_FILE_BYTES),
) -> FileTransferResponse:
    owner, slug = _safe_ref(owner, "owner"), _safe_ref(slug, "slug")
    selected, size = _select(_list_files(owner, slug), filename, prefer, max_bytes)
    token = _sign(owner, slug, selected, size)
    return FileTransferResponse(
        openaiFileResponse=[f"{index.PUBLIC_BASE_URL}/api/action-file/{token}"],
        dataset=f"{owner}/{slug}", filename=selected, sizeBytes=size, mimeType=_mime(selected),
        provenance="Exact file retrieved from Kaggle; not reconstructed or regenerated.",
        expiresInSeconds=SIGNED_URL_TTL,
    )


@router.get(
    "/api/datasets/{owner}/{slug}/preview",
    operation_id="previewKaggleDatasetFile",
    dependencies=[Depends(index.require_bridge_auth)],
    response_model=DatasetPreviewResponse,
    summary="Preview exact rows from a Kaggle data file",
    description="Read a small preview directly from the exact Kaggle file. For full analysis or training, use getKaggleDatasetFileForAnalysis.",
)
def preview_file(owner: str, slug: str, filename: str = Query(..., max_length=500), rows: int = Query(default=20, ge=1, le=100)) -> DatasetPreviewResponse:
    owner, slug, filename = _safe_ref(owner, "owner"), _safe_ref(slug, "slug"), _safe_filename(filename)
    with tempfile.TemporaryDirectory(prefix="kaggle-preview-") as d:
        path = _download(owner, slug, filename, d)
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            raise HTTPException(status_code=413, detail="Preview is limited to files at or below 10 MB.")
        fmt, columns, preview_rows, truncated = _preview(path, filename, rows)
    return DatasetPreviewResponse(dataset=f"{owner}/{slug}", filename=filename, sizeBytes=size, format=fmt,
                                  columns=columns, rows=preview_rows, returnedRows=len(preview_rows), truncated=truncated,
                                  provenance="Rows read directly from the exact Kaggle file.")


@router.get("/api/action-file/{token}", include_in_schema=False)
def signed_file(token: str):
    payload = _verify(token)
    owner, slug, filename = str(payload["owner"]), str(payload["slug"]), str(payload["filename"])
    tmp = tempfile.TemporaryDirectory(prefix="kaggle-action-file-")
    try:
        path = _download(owner, slug, filename, tmp.name)
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            tmp.cleanup()
            raise HTTPException(status_code=413, detail="File exceeds the 10 MB Action return limit.")
        if not _allowed_file(filename):
            tmp.cleanup()
            raise HTTPException(status_code=415, detail="Image and video files cannot be returned through this Action.")
        headers = {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(PurePosixPath(filename).name)}",
                   "Content-Length": str(size), "X-Kaggle-Dataset": f"{owner}/{slug}", "X-Data-Provenance": "exact-kaggle-file"}
        def stream():
            try:
                with path.open("rb") as f:
                    while chunk := f.read(256 * 1024):
                        yield chunk
            finally:
                tmp.cleanup()
        return StreamingResponse(stream(), media_type=_mime(filename), headers=headers)
    except Exception:
        tmp.cleanup()
        raise
