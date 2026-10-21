import json
import logging
import re
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from .api import router
from .config import settings
from .database import SessionLocal, engine
from .models import SCOPED_TABLES
from .security import DomainError

logger = logging.getLogger("aftercare")
logging.basicConfig(level=logging.INFO, format="%(message)s")


@asynccontextmanager
async def lifespan(app):
    from .database import verify_runtime_database

    verify_runtime_database()
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    yield


app = FastAPI(
    title="循单 Aftercare API",
    version="0.1.0",
    lifespan=lifespan,
    docs_url=None if settings().app_env == "production" else "/api/docs",
    openapi_url=None if settings().app_env == "production" else "/api/openapi.json",
)


@app.middleware("http")
async def request_scope(request: Request, call_next):
    request_id = request.headers.get("x-request-id", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", request_id):
        request_id = uuid.uuid4().hex
    request.state.request_id = request_id
    started = time.monotonic()
    with SessionLocal() as db:
        request.state.db = db
        db.info["request_id"] = request_id
        committed = False
        try:
            response = await call_next(request)
            if response.status_code < 400:
                await run_in_threadpool(db.commit)
                committed = True
            else:
                await run_in_threadpool(db.rollback)
        except IntegrityError:
            await run_in_threadpool(db.rollback)
            response = JSONResponse(
                {
                    "code": "concurrent_conflict",
                    "message": "记录发生冲突，请刷新后核查",
                    "request_id": request_id,
                },
                status_code=409,
            )
        except SQLAlchemyError:
            await run_in_threadpool(db.rollback)
            logger.error(
                json.dumps({"event": "database_error", "request_id": request_id})
            )
            response = JSONResponse(
                {
                    "code": "database_unavailable",
                    "message": "数据服务暂时不可用，请稍后重试",
                    "request_id": request_id,
                },
                status_code=503,
            )
        except Exception as exc:
            await run_in_threadpool(db.rollback)
            logger.error(
                json.dumps(
                    {
                        "event": "request_failed",
                        "error_type": type(exc).__name__,
                        "request_id": request_id,
                    }
                )
            )
            response = JSONResponse(
                {
                    "code": "internal_error",
                    "message": "操作未完成，请稍后重试或联系维护人员",
                    "request_id": request_id,
                },
                status_code=500,
            )
        finally:
            from .storage import discard_file

            for key in db.info.pop("pending_files", []):
                if not committed:
                    await run_in_threadpool(discard_file, key)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        logger.info(
            json.dumps(
                {
                    "event": "http.request",
                    "request_id": request_id,
                    "method": request.method,
                    "route": getattr(request.scope.get("route"), "path", "unmatched"),
                    "status": response.status_code,
                    "duration_ms": round((time.monotonic() - started) * 1000),
                    "tenant_id": getattr(request.state, "tenant_id", None),
                }
            )
        )
        from .telemetry import record_request

        record_request(
            getattr(request.scope.get("route"), "path", "unmatched"),
            response.status_code,
            (time.monotonic() - started) * 1000,
        )
        return response


@app.exception_handler(DomainError)
async def domain_error(request, exc):
    return JSONResponse(
        {
            "code": exc.code,
            "message": exc.message,
            "request_id": request.state.request_id,
        },
        status_code=exc.status,
    )


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    return JSONResponse(
        {
            "code": "validation_error",
            "message": "提交字段不符合要求，请检查必填项和格式",
            "fields": [
                {"field": ".".join(str(x) for x in e["loc"]), "message": e["msg"]}
                for e in exc.errors()
            ],
            "request_id": request.state.request_id,
        },
        status_code=422,
    )


@app.get("/health/live")
def live():
    return {"status": "ok"}


@app.get("/health/ready")
def ready():
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    return {"status": "ready"}


@app.get("/health/workers")
def workers_ready():
    from .telemetry import worker_metrics

    alive = worker_metrics(None)[0].value
    return JSONResponse(
        {"status": "ready" if alive else "unavailable"},
        status_code=200 if alive else 503,
    )


app.include_router(router)

if settings().otel_exporter_otlp_endpoint:
    from .telemetry import SENSITIVE_TRACE_URLS, configure
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    configure(settings().otel_service_name)
    FastAPIInstrumentor.instrument_app(app, excluded_urls=SENSITIVE_TRACE_URLS)
