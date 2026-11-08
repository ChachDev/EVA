"""Optional evaluation service using the existing HTTP contract and ISSAI lifespan."""
from contextlib import asynccontextmanager
import os
from pathlib import Path

from fastapi import FastAPI

from .app import app as primary_app, evaluate, health, lifespan as primary_lifespan
from .request_ledger import RequestLedgerProvider, TEACHING
from .ledger_spans import SpanLedgerProvider
from .ledger_calibration import CalibratedProvider
from .ledger_knowledge import ProductVerifier
from .criterion_reader import CriterionReader
from .share_resolution import ArchiveReview
from .identity_order import IdentityOrderProvider


@asynccontextmanager
async def lifespan(application):
    mode = os.getenv("EVA_LEDGER_MODE", "off")
    if mode not in {"off", "map", "examples", "spans", "spans-examples", "expert", "product", "criterion", "resolution", "identity-order"}:
        raise ValueError("Unknown EVA_LEDGER_MODE")
    async with primary_lifespan(application):
        archive = None
        if mode != "off":
            if os.getenv("EVA_ML_PROVIDER", "issai") != "issai":
                raise ValueError("The request ledger requires the real local evaluator")
            provider = application.state.provider
            if mode == "identity-order":
                application.state.provider = IdentityOrderProvider(provider)
            elif mode == "resolution":
                archive = ArchiveReview(provider, Path(os.environ["EVA_SHARE_ARCHIVE"]))
                application.state.provider = archive
            elif mode == "criterion":
                application.state.provider = CriterionReader(provider)
            elif mode == "product":
                application.state.provider = ProductVerifier.from_file(provider, os.environ["EVA_PRODUCT_CATALOG"])
            elif mode == "expert":
                application.state.provider = CalibratedProvider(provider, os.environ["EVA_LEDGER_CATALOG"])
            elif mode.startswith("spans"):
                application.state.provider = SpanLedgerProvider(provider, TEACHING if mode == "spans-examples" else ())
            else:
                application.state.provider = RequestLedgerProvider(provider, TEACHING if mode == "examples" else ())
        try:
            yield
        finally:
            if archive is not None:
                archive.close()


app = FastAPI(title="EVA consultation experiment", lifespan=lifespan, docs_url=None, redoc_url=None,
              exception_handlers=primary_app.exception_handlers.copy())
app.add_api_route("/health", health, methods=["GET"])
app.add_api_route("/v1/evaluate", evaluate, methods=["POST"])
