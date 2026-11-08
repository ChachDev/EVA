"""Local contract boundary for the ISSAI evaluator."""

from contextlib import asynccontextmanager
import os
from pathlib import Path
import time
import logging
from typing import Annotated, Any

from fastapi import Body, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from eva_ml import stub
from eva_ml.audit import COMPONENTS
from eva_ml.questions import select_auditor
from eva_ml.contracts import ContractError, Contracts
from eva_ml.engine import EngineError, loaded_engine
from eva_ml.issai import InvalidDecision, digest
from eva_ml.consultation_v11 import select_provider


@asynccontextmanager
async def lifespan(application: FastAPI):
    application.state.contracts = Contracts()
    application.state.auditor = None
    mode = os.getenv("EVA_ML_PROVIDER","issai")
    if mode == "stub":
        application.state.provider = stub
        yield
    elif mode == "issai":
        started = time.monotonic()
        worker = loaded_engine()
        engine = await run_in_threadpool(worker.__enter__)
        try:
            application.state.provider = select_provider(engine, os.getenv("EVA_CONSULTATION_VARIANT", "consultation-v1.1"))
            configured = os.getenv("EVA_AUDIT_COMPONENTS", ",".join(sorted(COMPONENTS)))
            keys=set() if configured.strip().lower()=="off" else {key.strip() for key in configured.split(",") if key.strip()}
            try:
                application.state.auditor = select_auditor(application.state.provider,keys,
                                                          os.getenv("EVA_AUDIT_PROFILE","source-events"))
            except ValueError:
                logging.getLogger("uvicorn.error").warning("Invalid experimental audit configuration; baseline remains available.")
            await run_in_threadpool(application.state.provider.prepare)
            logging.getLogger("uvicorn.error").info("ISSAI service ready in %.3f s",time.monotonic()-started)
            yield
        finally:
            application.state.auditor = None
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


@app.post("/v1/verify")
def verify(request: Request, envelope: Annotated[Any, Body()]) -> Any:
    if not isinstance(envelope,dict) or set(envelope)!={"request","baseline","request_hash"}:
        return JSONResponse(status_code=422,content={"error":"invalid_verification_request"})
    payload=envelope["request"]
    base=envelope["baseline"]
    contracts=request.app.state.contracts
    try:
        contracts.validate_request(payload)
        contracts.validate_response(payload,base)
    except ContractError:
        return JSONResponse(status_code=422,content={"error":"invalid_verification_request"})
    if digest(payload)!=envelope["request_hash"]:
        return JSONResponse(status_code=422,content={"error":"snapshot_mismatch"})
    auditor=getattr(request.app.state,"auditor",None)
    if auditor is None:
        return JSONResponse(status_code=503,content={"error":"real_evaluator_required"})
    if (base["model"],base["provider"])!=(auditor.base.model,auditor.base.name):
        return JSONResponse(status_code=422,content={"error":"baseline_model_mismatch"})
    if not getattr(auditor,"reuses_baseline",False):
        return audit(request,payload)
    try:
        result=auditor.evaluate(payload,saved_baseline=base)
        contracts.validate_response(payload,result["response"])
        return result
    except EngineError:
        return JSONResponse(status_code=503,content={"error":"local_engine_unavailable"})
    except (ContractError, InvalidDecision):
        return JSONResponse(status_code=500,content={"error":"invalid_provider_response"})


@app.post("/v1/audit")
def audit(request: Request, payload: Annotated[Any, Body()]) -> Any:
    contracts = request.app.state.contracts
    try:
        contracts.validate_request(payload)
    except ContractError as error:
        return JSONResponse(status_code=422, content={"error":"invalid_request","issues":error.issues})
    auditor = getattr(request.app.state,"auditor",None)
    if auditor is None:
        return JSONResponse(status_code=503,content={"error":"real_evaluator_required"})
    try:
        result = auditor.evaluate(payload)
        contracts.validate_response(payload,result["response"])
        return result
    except EngineError:
        return JSONResponse(status_code=503,content={"error":"local_engine_unavailable"})
    except (ContractError, InvalidDecision):
        return JSONResponse(status_code=500,content={"error":"invalid_provider_response"})
