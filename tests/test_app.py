from fastapi.testclient import TestClient

from index import app, parse_csv_output


client = TestClient(app)


def test_health_is_public():
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["readOnly"] is True


def test_protected_route_rejects_missing_key(monkeypatch):
    monkeypatch.setenv("BRIDGE_API_KEY", "test-secret")
    response = client.get("/api/datasets/search?q=ghana")
    assert response.status_code == 401


def test_protected_route_rejects_wrong_key(monkeypatch):
    monkeypatch.setenv("BRIDGE_API_KEY", "test-secret")
    response = client.get(
        "/api/datasets/search?q=ghana",
        headers={"Authorization": "Bearer wrong-secret"},
    )
    assert response.status_code == 401


def test_csv_parser():
    rows = parse_csv_output("ref,title\na/b,Example\n")
    assert rows == [{"ref": "a/b", "title": "Example"}]


def test_openapi_has_action_operation_ids():
    schema = client.get("/openapi.json").json()
    operation_ids = {
        operation["operationId"]
        for path in schema["paths"].values()
        for operation in path.values()
        if isinstance(operation, dict) and "operationId" in operation
    }
    assert "searchKaggleDatasets" in operation_ids
    assert "searchKaggleCompetitions" in operation_ids
    assert "searchKaggleNotebooks" in operation_ids
    assert "searchKaggleModels" in operation_ids
