import os

import pytest
from fastapi.testclient import TestClient

import app as app_entry
import file_access


client = TestClient(app_entry.app)


def test_openapi_exposes_data_access_actions():
    schema = client.get("/openapi.json").json()
    operation_ids = {
        operation["operationId"]
        for path in schema["paths"].values()
        for operation in path.values()
        if isinstance(operation, dict) and "operationId" in operation
    }
    assert "getKaggleDatasetFileForAnalysis" in operation_ids
    assert "previewKaggleDatasetFile" in operation_ids
    assert schema["components"]["schemas"]["FileTransferResponse"]["properties"]["openaiFileResponse"]["type"] == "array"


def test_signed_token_round_trip(monkeypatch):
    monkeypatch.setenv("BRIDGE_API_KEY", "test-secret")
    token = file_access._sign("owner", "dataset", "data.csv", 123)
    payload = file_access._verify(token)
    assert payload["owner"] == "owner"
    assert payload["slug"] == "dataset"
    assert payload["filename"] == "data.csv"
    assert payload["size"] == 123


def test_path_traversal_is_rejected():
    with pytest.raises(Exception):
        file_access._safe_filename("../secret.csv")


def test_auto_select_prefers_small_csv():
    rows = [
        {"name": "notes.txt", "size": "1 KB"},
        {"name": "data.csv", "size": "2 MB"},
        {"name": "huge.csv", "size": "50 MB"},
    ]
    assert file_access._select(rows, None, "auto", 10_000_000) == ("data.csv", 2_000_000)


def test_analysis_action_returns_signed_file_url(monkeypatch):
    monkeypatch.setenv("BRIDGE_API_KEY", "test-secret")
    monkeypatch.setattr(file_access.index, "PUBLIC_BASE_URL", "https://example.test")
    monkeypatch.setattr(file_access, "_list_files", lambda *args, **kwargs: [{"name": "data.csv", "size": "2 KB"}])

    response = client.get(
        "/api/datasets/owner/dataset/analysis-file",
        headers={"Authorization": "Bearer test-secret"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["filename"] == "data.csv"
    assert body["openaiFileResponse"][0].startswith("https://example.test/api/action-file/")
    assert "Exact file" in body["provenance"]
