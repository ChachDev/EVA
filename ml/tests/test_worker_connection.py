import pytest

from eva_ml.engine import EngineError, LocalEngine, loaded_engine


def test_existing_worker_uses_only_loopback_and_requires_model_alias(monkeypatch):
    monkeypatch.setenv("EVA_LLAMA_WORKER_PORT", "18777")
    monkeypatch.setenv("EVA_ISSAI_GGUF", "weights/check.gguf")
    observed = []

    def listing(worker, route, timeout):
        observed.append((worker.base, route))
        return {"data": [{"id": "check"}]}

    monkeypatch.setattr(LocalEngine, "request", listing)
    with loaded_engine() as worker:
        assert worker.model == "check"
    assert observed == [("http://127.0.0.1:18777", "/v1/models")]
    monkeypatch.setattr(LocalEngine, "request", lambda *a, **kw: {"data": [{"id": "other"}]})
    with pytest.raises(EngineError, match="requested model"), loaded_engine():
        pytest.fail("A mismatched model must never be used")


@pytest.mark.parametrize("port", ["", "65536", "https://example.org"])
def test_invalid_shared_worker_never_falls_back(monkeypatch, port):
    monkeypatch.setenv("EVA_LLAMA_WORKER_PORT", port)
    with pytest.raises(EngineError, match="TCP port"), loaded_engine():
        pytest.fail("Invalid worker settings must fail before connecting")
