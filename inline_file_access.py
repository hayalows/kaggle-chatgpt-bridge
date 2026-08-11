from __future__ import annotations

import base64
import tempfile
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

import index
import file_access

router = APIRouter()
INLINE_MAX_FILE_BYTES = 3_000_000
PreferredFormat = Literal["auto", "csv", "tsv", "parquet", "json", "xlsx", "sqlite", "zip"]
Base64FileContent = Annotated[str, Field(json_schema_extra={"format": "byte"})]


class OpenAIInlineFile(BaseModel):
    name: str
    mime_type: str
    content: Base64FileContent


class InlineFileTransferResponse(BaseModel):
    openaiFileResponse: list[OpenAIInlineFile]


@router.get(
    "/api/datasets/{owner}/{slug}/analysis-file-inline",
    operation_id="getKaggleDatasetFileForAnalysis",
    dependencies=[Depends(index.require_bridge_auth)],
    response_model=InlineFileTransferResponse,
    summary="Download real Kaggle file into the conversation",
    description=(
        "MUST call for full analysis or modelling when real rows are needed. Returns the exact Kaggle file inline "
        "for Code Interpreter. Do not substitute a preview. Raw file must be 3 MB or smaller."
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

    selected, _ = file_access._select(
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
                    f"{max_bytes} bytes. Use readKaggleDatasetRows as the fallback."
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
        ]
    )
