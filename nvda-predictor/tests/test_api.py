import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.main import app

client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "model_ready" in body


def test_model_info_or_503():
    r = client.get("/model")
    assert r.status_code in (200, 503)
    if r.status_code == 200:
        assert r.json()["ticker"]
