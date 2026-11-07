import asyncio
import json
from threading import get_ident
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.diagnostics import collect_diagnostics
from app.ml_client import MLInvalidResponse, MLUnavailable


PRIVATE = "synthetic-private-diagnostic-marker"


@pytest.fixture
def settings():
    return SimpleNamespace(
        ml_timeout_seconds=20.0, ml_max_concurrency=2,
        database_url=f"postgresql://synthetic:{PRIVATE}@127.0.0.1/synthetic",
        ml_url=f"http://127.0.0.1:8001/{PRIVATE}",
    )


@pytest.fixture
def health():
    return {
        "status": "ok", "service": "eva-ml", "schema_version": "1.0.0",
        "provider": "local", "model": "synthetic-model-v1",
    }


class FakeConnection:
    def __init__(self, result=1):
        self.result = result
        self.statements = []
        self.thread = None
        self.exited = False

    def __enter__(self):
        return self

    def __exit__(self, *_exception):
        self.exited = True

    def execute(self, statement):
        self.thread = get_ident()
        self.statements.append(str(statement))
        return self

    def scalar_one(self):
        return self.result


def fake_store(connection=None, *, error=None):
    connect = Mock(return_value=connection or FakeConnection(), side_effect=error)
    return SimpleNamespace(engine=SimpleNamespace(connect=connect), initialize=Mock(), close=Mock())


def run(settings, store, *, health=None, error=None):
    ml = SimpleNamespace(probe=AsyncMock(return_value=health, side_effect=error))
    result = asyncio.run(collect_diagnostics(settings, store, ml))
    ml.probe.assert_awaited_once_with(timeout_seconds=2)
    store.initialize.assert_not_called()
    store.close.assert_not_called()
    return result


def test_ready_checks_only_select_one_without_blocking_the_event_loop(settings, health):
    connection = FakeConnection()
    result = run(settings, fake_store(connection), health=health)
    assert result == {
        "status": "ready", "database": "ready", "ml": "ready",
        "provider": "local", "model": "synthetic-model-v1",
        "limits": {"ml_timeout_seconds": 20.0, "ml_max_concurrency": 2},
    }
    assert connection.statements == ["SELECT 1"]
    assert connection.thread != get_ident()
    assert connection.exited is True


@pytest.mark.parametrize("provider,model", [("stub", "unconfigured"), ("stub", "synthetic"), ("local", "unconfigured")])
def test_stub_is_degraded_without_fabricating_model_readiness(settings, health, provider, model):
    health.update(provider=provider, model=model)
    result = run(settings, fake_store(), health=health)
    assert result["status"] == "degraded"
    assert result["ml"] == "stub"
    assert result["provider"] == provider
    assert result["model"] == model


def test_database_failure_still_reports_ml_without_exposing_exception(settings, health, caplog, capsys):
    result = run(settings, fake_store(error=RuntimeError(PRIVATE)), health=health)
    assert result["database"] == "unavailable"
    assert result["ml"] == "ready"
    assert result["status"] == "degraded"
    assert PRIVATE not in json.dumps(result)
    assert PRIVATE not in caplog.text
    assert PRIVATE not in capsys.readouterr().out


def test_select_one_with_an_unexpected_result_is_not_ready(settings, health):
    result = run(settings, fake_store(FakeConnection(result=0)), health=health)
    assert result["database"] == "unavailable"
    assert result["status"] == "degraded"


@pytest.mark.parametrize("error,expected", [
    (MLUnavailable(PRIVATE), "unavailable"),
    (TimeoutError(PRIVATE), "unavailable"),
    (RuntimeError(PRIVATE), "unavailable"),
    (MLInvalidResponse(PRIVATE), "invalid_response"),
])
def test_ml_failures_are_safe_and_do_not_hide_database_readiness(settings, error, expected, caplog, capsys):
    result = run(settings, fake_store(), error=error)
    assert result["database"] == "ready"
    assert result["ml"] == expected
    assert result["status"] == "degraded"
    assert "provider" not in result
    assert "model" not in result
    assert PRIVATE not in json.dumps(result)
    assert PRIVATE not in caplog.text
    captured = capsys.readouterr()
    assert PRIVATE not in captured.out + captured.err


@pytest.mark.parametrize("mutation", [
    lambda health: None,
    lambda health: [],
    lambda health: {**health, "status": "loading"},
    lambda health: {**health, "service": "another-service"},
    lambda health: {**health, "schema_version": "99"},
    lambda health: {**health, "provider": ""},
    lambda health: {**health, "model": "  "},
    lambda health: {**health, "model": {"private": PRIVATE}},
    lambda health: {**health, "model": f"postgresql://user:{PRIVATE}@127.0.0.1/db"},
    lambda health: {**health, "provider": "unsafe\nmetadata"},
    lambda health: {**health, "model": "x" * 201},
    lambda health: {key: value for key, value in health.items() if key != "provider"},
])
def test_malformed_health_cannot_supply_metadata_or_readiness(settings, health, mutation):
    result = run(settings, fake_store(), health=mutation(health))
    assert result["ml"] == "invalid_response"
    assert result["status"] == "degraded"
    assert "provider" not in result
    assert "model" not in result
    assert PRIVATE not in json.dumps(result)


def test_unrelated_health_fields_and_config_secrets_are_never_copied(settings, health):
    health.update(database_url=settings.database_url, api_key=PRIVATE, error={"private": PRIVATE})
    result = run(settings, fake_store(), health=health)
    assert result["ml"] == "ready"
    assert set(result) == {"status", "database", "ml", "provider", "model", "limits"}
    assert PRIVATE not in json.dumps(result)


def test_cancellation_propagates_instead_of_becoming_a_successful_diagnostic(settings):
    ml = SimpleNamespace(probe=AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(collect_diagnostics(settings, fake_store(), ml))
