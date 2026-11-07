"""Local contract boundary for the ISSAI evaluator."""

from contextlib import asynccontextmanager
import os
import time
import logging
from typing import Annotated, Any

from fastapi import Body, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from eva_ml import stub
from eva_ml.contracts import ContractError, Contracts
from eva_ml.engine import EngineError, loaded_engine
from eva_ml.issai import ISSAIProvider, InvalidDecision


@asynccontextmanager
async def lifespan(application: FastAPI):
    application.state.contracts = Contracts()
    mode = os.getenv("EVA_ML_PROVIDER","issai")
    if mode == "stub":
        application.state.provider = stub
        yield
    elif mode == "issai":
        started = time.monotonic()
        worker = loaded_engine()
        engine = await run_in_threadpool(worker.__enter__)
        try:
            application.state.provider = ISSAIProvider(engine)
            await run_in_threadpool(application.state.provider.prepare)
            logging.getLogger("uvicorn.error").info("ISSAI service ready in %.3f s",time.monotonic()-started)
            yield
        finally:
            await run_in_threadpool(worker.__exit__, None, None, None)
    else:
        raise RuntimeError("Unknown EVA_ML_PROVIDER")


app = FastAPI(
    title="EVA ML",
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
def health(request: Request) -> dict[str, str]:
    provider = request.app.state.provider
    return {
        "status": "ok",
        "service": "eva-ml",
        "provider": provider.name,
        "model": provider.model,
        "schema_version": "1.0.0",
    }


@app.post("/v1/evaluate")
def evaluate(request: Request, payload: Annotated[Any, Body()]) -> Any:
    contracts: Contracts = request.app.state.contracts
    try:
        contracts.validate_request(payload)
    except ContractError as error:
        return JSONResponse(status_code=422, content={"error": "invalid_request", "issues": error.issues})

    try:
        result = request.app.state.provider.evaluate(payload)
        contracts.validate_response(payload, result)
    except EngineError:
        return JSONResponse(status_code=503, content={"error": "local_engine_unavailable"})
    except (ContractError, InvalidDecision):
        # Do not pass malformed provider data or conversation text to callers/logs.
        return JSONResponse(status_code=500, content={"error": "invalid_provider_response"})
    return result
