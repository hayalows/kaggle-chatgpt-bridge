from __future__ import annotations

import csv
import hmac
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict


_vercel_host = os.getenv("VERCEL_PROJECT_PRODUCTION_URL") or os.getenv("VERCEL_URL")
PUBLIC_BASE_URL = (
    os.getenv("PUBLIC_BASE_URL")
    or (f"https://{_vercel_host}" if _vercel_host else "https://YOUR-DOMAIN.vercel.app")
).rstrip("/")

app = FastAPI(
    title="Kaggle ChatGPT Bridge",
    version="0.1.2",
    description=(
        "Read-only bridge that lets a Custom GPT search and inspect public Kaggle "
        "datasets, competitions, notebooks, and models through the official Kaggle CLI."
    ),
    servers=[{"url": PUBLIC_BASE_URL}],
)


DatasetSort = Literal["hottest", "votes", "updated", "active"]
DatasetFileType = Literal["all", "csv", "sqlite", "json", "bigQuery"]
DatasetLicense = Literal["all", "cc", "gpl", "odb", "other"]
CompetitionCategory = Literal[
    "all", "featured", "research", "recruitment", "gettingStarted", "masters", "playground"
]
CompetitionSort = Literal[
    "grouped", "prize", "earliestDeadline", "latestDeadline", "numberOfTeams", "recentlyCreated"
]
NotebookLanguage = Literal["all", "python", "r", "sqlite", "julia"]
NotebookType = Literal["all", "script", "notebook"]
NotebookSort = Literal[
    "hotness", "commentCount", "dateCreated", "dateRun", "relevance",
    "scoreAscending", "scoreDescending", "viewCount", "voteCount"
]
ModelSort = Literal["hotness", "downloadCount", "voteCount", "notebookCount", "createTime"]


class HealthResponse(BaseModel):
    status: str
    version: str
    readOnly: bool
    kaggleConfigured: bool
    bridgeAuthConfigured: bool


class KaggleItem(BaseModel):
    """Common Kaggle list fields while still preserving extra CLI columns."""

    model_config = ConfigDict(extra="allow")

    ref: str | None = None
    id: str | None = None
    title: str | None = None
    name: str | None = None
    owner: str | None = None
    slug: str | None = None
    size: str | None = None
    lastUpdated: str | None = None
    downloadCount: str | None = None
    voteCount: str | None = None
    usabilityRating: str | None = None
    deadline: str | None = None
    category: str | None = None
    reward: str | None = None
    teamCount: str | None = None
    author: str | None = None
    lastRunTime: str | None = None


class PagedSearchResponse(BaseModel):
    query: str
    page: int
    count: int
    items: list[KaggleItem]


class SearchResponse(BaseModel):
    query: str
    count: int
    items: list[KaggleItem]


class DatasetFilesResponse(BaseModel):
    dataset: str
    count: int
    items: list[KaggleItem]


class KaggleMetadata(BaseModel):
    """Common dataset metadata fields with extra Kaggle fields preserved."""

    model_config = ConfigDict(extra="allow")

    id: str | None = None
    title: str | None = None
    subtitle: str | None = None
    description: str | None = None


class DatasetMetadataResponse(BaseModel):
    dataset: str
    metadata: KaggleMetadata


def _sanitize(text: str) -> str:
    token = os.getenv("KAGGLE_API_TOKEN")
    bridge_key = os.getenv("BRIDGE_API_KEY")
    for secret in (token, bridge_key):
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return text.strip()


bearer_scheme = HTTPBearer(auto_error=False, description="Bridge API key")


def require_bridge_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> None:
    expected = os.getenv("BRIDGE_API_KEY")
    if not expected:
        raise HTTPException(status_code=503, detail="BRIDGE_API_KEY is not configured on the server")

    if not credentials:
        raise HTTPException(status_code=401, detail="Missing Authorization header")

    if credentials.scheme.lower() != "bearer" or not hmac.compare_digest(credentials.credentials, expected):
        raise HTTPException(status_code=401, detail="Invalid API key")


def _kaggle_command(args: list[str]) -> list[str]:
    executable = shutil.which("kaggle")
    if executable:
        return [executable, *args]

    runner = (
        "import sys; "
        "from kaggle.cli import main; "
        "sys.argv=['kaggle']+sys.argv[1:]; "
        "main()"
    )
    return [sys.executable, "-c", runner, *args]


def run_kaggle(args: list[str], timeout: int = 25) -> str:
    if not os.getenv("KAGGLE_API_TOKEN"):
        raise HTTPException(status_code=503, detail="KAGGLE_API_TOKEN is not configured on the server")

    kaggle_config_dir = os.path.join(tempfile.gettempdir(), ".kaggle")
    try:
        os.makedirs(kaggle_config_dir, mode=0o700, exist_ok=True)
    except OSError as exc:
        raise HTTPException(status_code=500, detail="Could not prepare writable Kaggle config directory") from exc

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["HOME"] = tempfile.gettempdir()
    env["KAGGLE_CONFIG_DIR"] = kaggle_config_dir

    try:
        result = subprocess.run(
            _kaggle_command(args),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=504, detail="Kaggle request timed out") from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail="Kaggle CLI could not be started") from exc

    if result.returncode != 0:
        detail = _sanitize(result.stderr or result.stdout or "Kaggle command failed")
        raise HTTPException(status_code=502, detail=detail[:1200])

    return result.stdout


def parse_csv_output(output: str) -> list[dict[str, str]]:
    cleaned = output.lstrip("\ufeff\n\r ")
    if not cleaned:
        return []
    reader = csv.DictReader(io.StringIO(cleaned))
    return [dict(row) for row in reader]


@app.middleware("http")
async def no_store(request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.get("/", operation_id="serviceInfo", include_in_schema=False)
def root() -> dict[str, object]:
    return {
        "name": "Kaggle ChatGPT Bridge",
        "version": "0.1.2",
        "mode": "read-only",
        "docs": "/docs",
        "openapi": "/openapi.json",
        "health": "/api/health",
    }


@app.get("/api/health", operation_id="checkBridgeHealth", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        version="0.1.2",
        readOnly=True,
        kaggleConfigured=bool(os.getenv("KAGGLE_API_TOKEN")),
        bridgeAuthConfigured=bool(os.getenv("BRIDGE_API_KEY")),
    )


@app.get(
    "/api/datasets/search",
    operation_id="searchKaggleDatasets",
    dependencies=[Depends(require_bridge_auth)],
    response_model=PagedSearchResponse,
)
def search_datasets(
    q: str = Query(default="", max_length=200, description="Dataset search terms"),
    page: int = Query(default=1, ge=1, le=100),
    sort_by: DatasetSort = "hottest",
    file_type: DatasetFileType = "all",
    license: DatasetLicense = "all",
    user: str | None = Query(default=None, max_length=100),
) -> PagedSearchResponse:
    args = ["datasets", "list", "-v", "--page", str(page), "--sort-by", sort_by]
    if q:
        args += ["--search", q]
    if file_type != "all":
        args += ["--file-type", file_type]
    if license != "all":
        args += ["--license", license]
    if user:
        args += ["--user", user]

    items = [KaggleItem(**row) for row in parse_csv_output(run_kaggle(args))]
    return PagedSearchResponse(query=q, page=page, count=len(items), items=items)


@app.get(
    "/api/datasets/{owner}/{slug}/files",
    operation_id="listKaggleDatasetFiles",
    dependencies=[Depends(require_bridge_auth)],
    response_model=DatasetFilesResponse,
)
def dataset_files(
    owner: str,
    slug: str,
    page_size: int = Query(default=20, ge=1, le=200),
    page_token: str | None = Query(default=None, max_length=500),
) -> DatasetFilesResponse:
    ref = f"{owner}/{slug}"
    args = ["datasets", "files", ref, "-v", "--page-size", str(page_size)]
    if page_token:
        args += ["--page-token", page_token]
    items = [KaggleItem(**row) for row in parse_csv_output(run_kaggle(args))]
    return DatasetFilesResponse(dataset=ref, count=len(items), items=items)


@app.get(
    "/api/datasets/{owner}/{slug}/metadata",
    operation_id="getKaggleDatasetMetadata",
    dependencies=[Depends(require_bridge_auth)],
    response_model=DatasetMetadataResponse,
)
def dataset_metadata(owner: str, slug: str) -> DatasetMetadataResponse:
    ref = f"{owner}/{slug}"
    with tempfile.TemporaryDirectory(prefix="kaggle-meta-") as tmpdir:
        run_kaggle(["datasets", "metadata", ref, "-p", tmpdir], timeout=30)
        path = os.path.join(tmpdir, "dataset-metadata.json")
        if not os.path.exists(path):
            raise HTTPException(status_code=502, detail="Kaggle did not return dataset metadata")
        with open(path, "r", encoding="utf-8") as handle:
            metadata = json.load(handle)
    return DatasetMetadataResponse(dataset=ref, metadata=KaggleMetadata(**metadata))


@app.get(
    "/api/competitions/search",
    operation_id="searchKaggleCompetitions",
    dependencies=[Depends(require_bridge_auth)],
    response_model=PagedSearchResponse,
)
def search_competitions(
    q: str = Query(default="", max_length=200),
    page: int = Query(default=1, ge=1, le=100),
    category: CompetitionCategory = "all",
    sort_by: CompetitionSort = "latestDeadline",
) -> PagedSearchResponse:
    args = ["competitions", "list", "-v", "--page", str(page), "--sort-by", sort_by]
    if q:
        args += ["--search", q]
    if category != "all":
        args += ["--category", category]
    items = [KaggleItem(**row) for row in parse_csv_output(run_kaggle(args))]
    return PagedSearchResponse(query=q, page=page, count=len(items), items=items)


@app.get(
    "/api/notebooks/search",
    operation_id="searchKaggleNotebooks",
    dependencies=[Depends(require_bridge_auth)],
    response_model=PagedSearchResponse,
)
def search_notebooks(
    q: str = Query(default="", max_length=200),
    page: int = Query(default=1, ge=1, le=100),
    page_size: int = Query(default=20, ge=1, le=100),
    language: NotebookLanguage = "all",
    kernel_type: NotebookType = "all",
    sort_by: NotebookSort = "hotness",
    dataset: str | None = Query(default=None, max_length=220, description="owner/dataset-slug"),
    competition: str | None = Query(default=None, max_length=160),
) -> PagedSearchResponse:
    args = [
        "kernels", "list", "-v", "--page", str(page), "--page-size", str(page_size),
        "--sort-by", sort_by,
    ]
    if q:
        args += ["--search", q]
    if language != "all":
        args += ["--language", language]
    if kernel_type != "all":
        args += ["--kernel-type", kernel_type]
    if dataset:
        args += ["--dataset", dataset]
    if competition:
        args += ["--competition", competition]
    items = [KaggleItem(**row) for row in parse_csv_output(run_kaggle(args))]
    return PagedSearchResponse(query=q, page=page, count=len(items), items=items)


@app.get(
    "/api/models/search",
    operation_id="searchKaggleModels",
    dependencies=[Depends(require_bridge_auth)],
    response_model=SearchResponse,
)
def search_models(
    q: str = Query(default="", max_length=200),
    page_size: int = Query(default=20, ge=1, le=100),
    sort_by: ModelSort = "hotness",
    owner: str | None = Query(default=None, max_length=100),
) -> SearchResponse:
    args = ["models", "list", "-v", "--page-size", str(page_size), "--sort-by", sort_by]
    if q:
        args += ["--search", q]
    if owner:
        args += ["--owner", owner]
    items = [KaggleItem(**row) for row in parse_csv_output(run_kaggle(args))]
    return SearchResponse(query=q, count=len(items), items=items)


@app.get("/privacy", include_in_schema=False, response_class=HTMLResponse)
def privacy() -> str:
    return """
    <!doctype html>
    <html lang=\"en\"><head><meta charset=\"utf-8\"><title>Privacy - Kaggle ChatGPT Bridge</title></head>
    <body style=\"font-family:system-ui;max-width:760px;margin:48px auto;padding:0 20px;line-height:1.55\">
      <h1>Privacy</h1>
      <p>This bridge is designed for private use. It receives search and lookup parameters from an authenticated client and forwards the corresponding read-only request to Kaggle.</p>
      <p>The application code does not intentionally persist search queries, Kaggle responses, or API credentials. Hosting-platform logs may still record ordinary request metadata according to that platform's settings.</p>
      <p>Kaggle credentials and the bridge API key are supplied to the server through environment variables and are not returned by the API.</p>
    </body></html>
    """
