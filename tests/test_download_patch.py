from pathlib import Path

import file_download_patch


def test_non_zip_download_uses_unzip(monkeypatch, tmp_path):
    seen = {}

    def fake_run(args, timeout=0):
        seen["args"] = args
        (tmp_path / "data.csv").write_text("a,b\n1,2\n", encoding="utf-8")
        return ""

    monkeypatch.setattr(file_download_patch.index, "run_kaggle", fake_run)
    path = file_download_patch.download_exact("owner", "dataset", "data.csv", str(tmp_path))
    assert path.name == "data.csv"
    assert "--unzip" in seen["args"]


def test_zip_download_keeps_zip(monkeypatch, tmp_path):
    seen = {}

    def fake_run(args, timeout=0):
        seen["args"] = args
        (tmp_path / "archive.zip").write_bytes(b"PK")
        return ""

    monkeypatch.setattr(file_download_patch.index, "run_kaggle", fake_run)
    path = file_download_patch.download_exact("owner", "dataset", "archive.zip", str(tmp_path))
    assert path.name == "archive.zip"
    assert "--unzip" not in seen["args"]
