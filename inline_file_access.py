from __future__ import annotations

import base64
import tempfile
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

import index
import file_access

router = APIRouter()
INLINE_MAX_FILE_BYTES = 3_000_000
PreferredFormat = Literal["auto", "csv", "tsv", "parquet", "json", "xlsx", "sqlite", "zip"]


class OpenAIInlineFile(BaseModel):
    name: str
    mime_type: str
    content: str


class InlineFileTransferResponse(BaseModel):
    openaiFileResponse: list[OpenAIInlineFile]
    dataset: str
    filename: str
    sizeBytes: int
    mimeType: str
    provenance: str
    transferMode: str


@router.get(
    "/api/datasets/{owner}/{slug}/analysis-file-inline",
    operation_id="getKaggleDatasetFileForAnalysis",
    dependencies=[Depends(index.require_bridge_auth)],
    response_model=InlineFileTransferResponse,
    summary="Return an exact Kaggle file inline for Code Interpreter",
    description=(
        "Use this whenever the user asks to analyse, clean, model, forecast, train, inspect real rows, "
        "or create outputs from Kaggle data. The exact Kaggle file is downloaded by the bridge and returned "
        "inline through openaiFileResponse so it becomes available to the conversation and Code Interpreter. "
        "If filename is omitted, a useful structured file is selected automatically. The raw file must be 3 MB or smaller."
    ),
)
def analysis_file_inline(
    owner: str,
    slug: str,
    filename: str | None = Query(default=None, max_length=500),
    prefer: PreferredFormat = Query(default="auto"),
    max_bytes: int = Query(default=INLINE_MAX_FILE_BYTES, ge=1, le=INLINE_MAX_FILE_BYTES),
) -> InlineFileTransferResponse:
    owner = file_access._safe_ref(owner, "owner")
    slug = file_access._safe_ref(slug, "slug")

    selected, listed_size = file_access._select(
        file_access._list_files(owner, slug), filename, prefer, max_bytes
    )

    with tempfile.TemporaryDirectory(prefix="kaggle-inline-file-") as directory:
        path = file_access._download(owner, slug, selected, directory)
        actual_size = path.stat().st_size
        if actual_size > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"{selected} is {actual_size} bytes. Inline GPT Action transfer is limited to "
                    f"{max_bytes} bytes on this Vercel deployment. Try a smaller file or preview it instead."
                ),
            )
        if not file_access._allowed_file(selected):
            raise HTTPException(status_code=415, detail="Image and video files cannot be returned through this Action.")
        content = base64.b64encode(path.read_bytes()).decode("ascii")

    return InlineFileTransferResponse(
        openaiFileResponse=[
            OpenAIInlineFile(
                name=selected.rsplit("/", 1)[-1],
                mime_type=file_access._mime(selected),
                content=content,
            )
        ],
        dataset=f"{owner}/{slug}",
        filename=selected,
        sizeBytes=actual_size,
        mimeType=file_access._mime(selected),
        provenance="Exact file retrieved from Kaggle; not reconstructed or regenerated.",
        transferMode="inline-base64",
    )
