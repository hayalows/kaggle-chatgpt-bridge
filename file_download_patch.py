from pathlib import Path, PurePosixPath

from fastapi import HTTPException

import index
from file_access import _safe_filename


def download_exact(owner: str, slug: str, filename: str, directory: str) -> Path:
    """Download one Kaggle file and transparently unwrap Kaggle's ZIP transport when needed."""
    filename = _safe_filename(filename)
    args = ["datasets", "download", f"{owner}/{slug}", "-f", filename, "-p", directory, "-q", "-o"]
    if not filename.lower().endswith(".zip"):
        args.append("--unzip")
    index.run_kaggle(args, timeout=35)

    exact = Path(directory) / filename
    if exact.is_file():
        return exact

    basename = PurePosixPath(filename).name
    matches = [path for path in Path(directory).rglob("*") if path.is_file() and path.name == basename]
    if len(matches) == 1:
        return matches[0]

    files = [path for path in Path(directory).rglob("*") if path.is_file()]
    if len(files) == 1:
        only = files[0]
        if filename.lower().endswith(".zip") or only.suffix.lower() == PurePosixPath(filename).suffix.lower():
            return only

    raise HTTPException(
        status_code=502,
        detail="Kaggle reported success but the exact requested file could not be identified after download.",
    )
