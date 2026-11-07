"""Local ML boundary; a real provider can replace stub.evaluate later."""

from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Body, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from eva_ml import stub
from eva_ml.contracts import ContractError, Contracts


@asynccontextmanager
async def lifespan(application: FastAPI):
    application.state.contracts = Contracts()
    yield


app = FastAPI(
    title="EVA ML — local stub",
    version="0.1.0",
    lifespan=lifespan,
    # Default Swagger assets use a CDN. Keep this stub fully local.
    docs_url=None,
    redoc_url=None,
)


@app.exception_handler(RequestValidationError)
async def invalid_json(_request: Request, _error: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"error": "invalid_request", "issues": [{"path": "/", "rule": "valid_json_body"}]},
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "eva-ml",
        "provider": "stub",
        "model": "unconfigured",
        "schema_version": "1.0.0",
    }


@app.post("/v1/evaluate")
def evaluate(request: Request, payload: Annotated[Any, Body()]) -> Any:
    contracts: Contracts = request.app.state.contracts
    try:
        contracts.validate_request(payload)
    except ContractError as error:
        return JSONResponse(status_code=422, content={"error": "invalid_request", "issues": error.issues})

    result = stub.evaluate(payload)
    try:
        contracts.validate_response(payload, result)
    except ContractError:
        # Do not pass malformed provider data or conversation text to callers/logs.
        return JSONResponse(status_code=500, content={"error": "invalid_provider_response"})
    return result
