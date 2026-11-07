import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from eva_ml.app import app
from eva_ml.contracts import Contracts


@pytest.fixture
def evaluation_request():
    fixture = Path(__file__).resolve().parents[2] / "contracts" / "examples" / "evaluation-request.json"
    return json.loads(fixture.read_text(encoding="utf-8"))


@pytest.fixture
def contracts():
    return Contracts()


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client
