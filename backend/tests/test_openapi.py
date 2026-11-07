from copy import deepcopy
import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.openapi.models import OpenAPI
from fastapi.openapi.utils import get_openapi
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
import pytest

from app.config import Settings
from app.main import create_app
from app.openapi import install_contract_openapi


@pytest.fixture
def application(tmp_path, contracts, monkeypatch):
    app = create_app(Settings(database_path=tmp_path / "unused.sqlite3", seed_demo=False), start_workers=False)

    def no_database(*_args, **_kwargs):
        pytest.fail("OpenAPI generation must not initialize or connect to a database.")

    monkeypatch.setattr(app.state.store, "initialize", no_database)
    monkeypatch.setattr(app.state.store.engine, "connect", no_database)
    install_contract_openapi(app, contracts)
    try:
        yield app
    finally:
        app.state.store.close()


def references(value):
    if isinstance(value, dict):
        if "$ref" in value:
            yield value["$ref"]
        for child in value.values():
            yield from references(child)
    elif isinstance(value, list):
        for child in value:
            yield from references(child)


def resolve(document, reference):
    assert reference.startswith("#/")
    assert reference.count("#") == 1
    result = document
    for token in reference[2:].split("/"):
        result = result[token.replace("~1", "/").replace("~0", "~")]
    return result


def test_actual_routes_have_documented_requests_and_success_responses(application):
    document = application.openapi()
    OpenAPI.model_validate(document)
    assert document["openapi"].startswith("3.1.")
    for route in application.routes:
        if not isinstance(route, APIRoute) or not route.include_in_schema:
            continue
        for method in route.methods:
            operation = document["paths"][route.path][method.lower()]
            response = operation["responses"][str(route.status_code or 200)]
            assert response["content"], (route.path, method)
            for media_type in response["content"].values():
                assert media_type["schema"], (route.path, method)
            if method == "POST":
                assert operation["requestBody"]["required"] is True
                assert operation["requestBody"]["content"]


def test_all_references_are_local_and_resolve_including_canonical_fragments(application, contracts):
    originals = deepcopy(contracts.schemas)
    document = application.openapi()
    for reference in references(document):
        assert isinstance(resolve(document, reference), dict)
    publish = document["components"]["schemas"]["Contract_checklist-publish"]
    assert publish["properties"]["criteria"]["items"]["$ref"] == (
        "#/components/schemas/Contract_checklist/properties/criteria/items"
    )
    assert contracts.schemas == originals
    assert all("$id" not in document["components"]["schemas"]["Contract_" + name] for name in contracts.schemas)


def test_embedded_contracts_still_validate_the_approved_synthetic_examples(application, contracts):
    document = application.openapi()
    for name in contracts.schemas:
        if not (contracts.directory / "examples" / f"{name}.json").exists():
            continue
        schema = {"$ref": f"#/components/schemas/Contract_{name}", "components": document["components"]}
        Draft202012Validator(schema).validate(contracts.example(name))


def test_binary_upload_range_and_safe_error_bodies_are_described(application):
    paths = application.openapi()["paths"]
    upload = paths["/api/v1/audio-assets"]["post"]
    assert set(upload["requestBody"]["content"]) == {"audio/wav", "audio/x-wav", "audio/mpeg", "audio/flac", "audio/ogg"}
    assert all(value["schema"] == {"type": "string", "format": "binary"}
               for value in upload["requestBody"]["content"].values())
    key = next(parameter for parameter in upload["parameters"] if parameter["name"] == "idempotency-key")
    assert key["in"] == "header" and key["required"] is True
    assert key["schema"]["format"] == "uuid"
    content = paths["/api/v1/audio-assets/{asset_id}/content"]["get"]
    assert {"200", "206", "400", "404", "416", "422", "503"} <= set(content["responses"])
    assert "Content-Range" in content["responses"]["206"]["headers"]
    assert "application/json" not in content["responses"]["200"]["content"]
    assert {parameter["name"] for parameter in content["parameters"]} >= {"Range", "If-Range"}
    evaluation = paths["/api/v1/evaluations"]["post"]
    assert {"400", "404", "409", "413", "415", "422", "502", "503"} <= set(evaluation["responses"])
    for code in ("400", "409", "413", "415", "422", "502", "503"):
        assert evaluation["responses"][code]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/BackendError",
        }
    error = application.openapi()["components"]["schemas"]["BackendError"]
    assert error["properties"]["detail"]["type"] == "string"


def test_existing_pydantic_schema_metadata_and_openapi_cache_are_preserved(application, contracts):
    baseline = get_openapi(title=application.title, version=application.version, routes=application.routes)
    document = application.openapi()
    assert document["components"]["schemas"]["CreateEvaluation"] == baseline["components"]["schemas"]["CreateEvaluation"]
    assert document["info"]["title"] == application.title
    assert application.openapi() is document

    @application.get("/synthetic-extra")
    def extra() -> str:
        return "synthetic"

    refreshed = application.openapi()
    assert refreshed is not document
    assert "/synthetic-extra" in refreshed["paths"]
    assert len(refreshed["paths"]["/api/v1/audio-assets/{asset_id}/content"]["get"]["parameters"]) == 3


def test_remote_and_local_json_pointer_fragments_are_converted_without_double_hash():
    app = FastAPI()
    contracts = SimpleNamespace(schemas={
        "first": {"$id": "https://eva.local/schemas/first.schema.json",
                  "$defs": {"with/slash~": {"type": "string"}},
                  "properties": {"local": {"$ref": "#/$defs/with~1slash~0"}}},
        "second": {"$id": "https://eva.local/schemas/second.schema.json",
                   "$ref": "https://eva.local/schemas/first.schema.json#/%24defs/with~1slash~0"},
    })
    install_contract_openapi(app, contracts)
    document = app.openapi()
    for reference in references(document):
        assert resolve(document, reference) == {"type": "string"}


def test_openapi_endpoint_needs_no_lifespan_database_or_model(application, tmp_path):
    # Constructing a client without its context does not enter the DB/worker lifespan.
    client = TestClient(application, raise_server_exceptions=True)
    try:
        response = client.get("/openapi.json")
    finally:
        client.close()
    assert response.status_code == 200
    assert response.json() == application.openapi()
    assert not (tmp_path / "unused.sqlite3").exists()
    assert str(tmp_path) not in json.dumps(response.json())
