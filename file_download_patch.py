from __future__ import annotations

import re
from difflib import SequenceMatcher
from pathlib import Path, PurePosixPath

from fastapi import HTTPException

import index
from file_access import _safe_filename


def _norm_name(value: str) -> str:
    """Normalize filenames for safe comparison without depending on punctuation/case."""
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _find_downloaded_file(directory: str, requested: str) -> Path | None:
    root = Path(directory)
    files = [path for path in root.rglob("*") if path.is_file()]
    if not files:
        return None

    requested_path = PurePosixPath(requested)
    requested_rel = requested_path.as_posix().casefold()
    requested_base = requested_path.name.casefold()
    requested_suffix = requested_path.suffix.casefold()
    requested_stem_norm = _norm_name(requested_path.stem)

    # 1. Exact relative path, case-insensitive.
    relative_matches = []
    for path in files:
        try:
            relative = path.relative_to(root).as_posix().casefold()
        except ValueError:
            continue
        if relative == requested_rel:
            relative_matches.append(path)
    if len(relative_matches) == 1:
        return relative_matches[0]

    # 2. Exact basename, case-insensitive.
    basename_matches = [path for path in files if path.name.casefold() == requested_base]
    if len(basename_matches) == 1:
        return basename_matches[0]

    # 3. If Kaggle changed punctuation/spacing but preserved the logical stem.
    normalized_matches = [
        path
        for path in files
        if path.suffix.casefold() == requested_suffix
        and _norm_name(path.stem) == requested_stem_norm
    ]
    if len(normalized_matches) == 1:
        return normalized_matches[0]

    # 4. If there is exactly one file with the requested extension, it is safe to use.
    same_suffix = [path for path in files if path.suffix.casefold() == requested_suffix]
    if len(same_suffix) == 1:
        return same_suffix[0]

    # 5. For multiple same-extension files, accept only a clearly dominant fuzzy match.
    if requested_stem_norm and same_suffix:
        scored = []
        for path in same_suffix:
            candidate_norm = _norm_name(path.stem)
            score = SequenceMatcher(None, requested_stem_norm, candidate_norm).ratio()
            if requested_stem_norm in candidate_norm or candidate_norm in requested_stem_norm:
                score = max(score, 0.92)
            scored.append((score, path))
        scored.sort(key=lambda item: item[0], reverse=True)
        best_score, best_path = scored[0]
        second_score = scored[1][0] if len(scored) > 1 else 0.0
        if best_score >= 0.88 and best_score - second_score >= 0.08:
            return best_path

    # 6. Single-file downloads are safe when the extension still matches.
    if len(files) == 1:
        only = files[0]
        if requested_suffix == ".zip" or only.suffix.casefold() == requested_suffix:
            return only

    return None


def download_exact(owner: str, slug: str, filename: str, directory: str) -> Path:
    """Download one Kaggle file and safely resolve Kaggle renaming/ZIP transport cases."""
    filename = _safe_filename(filename)
    args = ["datasets", "download", f"{owner}/{slug}", "-f", filename, "-p", directory, "-q", "-o"]
    if not filename.lower().endswith(".zip"):
        args.append("--unzip")
    index.run_kaggle(args, timeout=35)

    exact = Path(directory) / filename
    if exact.is_file():
        return exact

    resolved = _find_downloaded_file(directory, filename)
    if resolved is not None:
        return resolved

    raise HTTPException(
        status_code=502,
        detail="Kaggle reported success but the requested file could not be safely matched after download.",
    )
