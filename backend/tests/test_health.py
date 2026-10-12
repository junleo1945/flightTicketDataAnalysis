import os

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/test")
os.environ.setdefault("DEEPSEEK_BASE_URL", "http://example.invalid/v1")
os.environ.setdefault("AGENT_MODEL", "test-agent")
os.environ.setdefault("WORKFLOW_MODEL", "test-workflow")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


def test_health_is_ok_without_database():
    client = TestClient(app)
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
