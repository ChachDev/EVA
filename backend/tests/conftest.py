import pytest

from app.config import REPOSITORY_ROOT
from app.contracts import Contracts


@pytest.fixture
def contracts():
    return Contracts(REPOSITORY_ROOT / "contracts")


@pytest.fixture
def request_data(contracts):
    return {
        "schema_version": "1.0.0",
        "conversation": contracts.example("conversation"),
        "checklist": contracts.example("checklist"),
    }


@pytest.fixture
def result_data(contracts):
    return contracts.example("evaluation-response")

