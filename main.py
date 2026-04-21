from __future__ import annotations

import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from audit_sink import AuditSink
from cache import TTLCache
from engine import (
    analyze_drug_safety,
    build_fallback_index,
    get_engine_runtime_stats,
    get_llm_circuit_status,
    load_fallback_interactions,
)
from error_taxonomy import build_error_response
from idempotency import IdempotencyStore
from llm_client import OllamaClient
from models import DrugSafetyRequest, DrugSafetyResponse
from rate_limiter import FixedWindowRateLimiter


BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR / "frontend"
FRONTEND_INDEX = FRONTEND_DIR / "index.html"
STATIC_DIR = FRONTEND_DIR / "static"
AUDIT_LOG_PATH = BASE_DIR / "logs" / "audit_trail.jsonl"
ANALYZE_RATE_LIMIT = int(os.getenv("ANALYZE_RATE_LIMIT", "40"))
ANALYZE_RATE_WINDOW_SECONDS = int(os.getenv("ANALYZE_RATE_WINDOW_SECONDS", "60"))
MAX_REQUEST_BYTES = int(os.getenv("MAX_REQUEST_BYTES", str(128 * 1024)))
IDEMPOTENCY_TTL_SECONDS = int(os.getenv("IDEMPOTENCY_TTL_SECONDS", "3600"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    started_at = time.time()

    cache = TTLCache(ttl_seconds=3600)
    llm_client = OllamaClient()
    rate_limiter = FixedWindowRateLimiter(limit=ANALYZE_RATE_LIMIT, window_seconds=ANALYZE_RATE_WINDOW_SECONDS)
    idempotency_store = IdempotencyStore(ttl_seconds=IDEMPOTENCY_TTL_SECONDS)
    audit_sink = AuditSink(output_path=AUDIT_LOG_PATH)

    fallback_path = BASE_DIR / "data" / "fallback_interactions.json"
    prompt_path = BASE_DIR / "prompts" / "system_prompt.txt"

    fallback_data = load_fallback_interactions(fallback_path)
    fallback_index = build_fallback_index(fallback_data)
    system_prompt = prompt_path.read_text(encoding="utf-8")
    llm_ready = await llm_client.warmup()

    app.state.started_at = started_at
    app.state.cache = cache
    app.state.llm_client = llm_client
    app.state.fallback_data = fallback_data
    app.state.fallback_index = fallback_index
    app.state.system_prompt = system_prompt
    app.state.llm_ready = llm_ready
    app.state.rate_limiter = rate_limiter
    app.state.idempotency_store = idempotency_store
    app.state.audit_sink = audit_sink

    await audit_sink.start()

    yield

    await audit_sink.stop()


app = FastAPI(title="EvoDoc Clinical Drug Safety Engine", version="1.0.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Internal clinic network; tighten in production with explicit origins.
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content=build_error_response(
            error_code="REQ_VALIDATION_ERROR",
            category="validation",
            message="Validation error in request payload",
            details=exc.errors(),
            request_id=getattr(request.state, "request_id", None),
            recoverable=True,
        ),
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content=build_error_response(
            error_code="HTTP_ERROR",
            category="request",
            message=str(exc.detail),
            details={"status_code": exc.status_code},
            request_id=getattr(request.state, "request_id", None),
            recoverable=exc.status_code < 500,
        ),
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content=build_error_response(
            error_code="INTERNAL_SERVER_ERROR",
            category="internal",
            message="An unexpected internal error occurred",
            details={"type": type(exc).__name__},
            request_id=getattr(request.state, "request_id", None),
            recoverable=False,
        ),
    )


@app.middleware("http")
async def request_guardrails_middleware(request: Request, call_next):
    if request.url.path == "/api/v1/analyze" and request.method.upper() == "POST":
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > MAX_REQUEST_BYTES:
            return JSONResponse(
                status_code=413,
                content=build_error_response(
                    error_code="PAYLOAD_TOO_LARGE",
                    category="guardrail",
                    message="Payload too large for analyze endpoint",
                    details={"max_bytes": MAX_REQUEST_BYTES},
                    request_id=getattr(request.state, "request_id", None),
                    recoverable=True,
                ),
            )

        client_host = request.client.host if request.client else "unknown"
        allowed, remaining = await app.state.rate_limiter.allow(client_host)
        if not allowed:
            return JSONResponse(
                status_code=429,
                content=build_error_response(
                    error_code="RATE_LIMIT_EXCEEDED",
                    category="guardrail",
                    message="Rate limit exceeded for analyze endpoint",
                    details={"limit": ANALYZE_RATE_LIMIT, "window_seconds": ANALYZE_RATE_WINDOW_SECONDS, "remaining": remaining},
                    request_id=getattr(request.state, "request_id", None),
                    recoverable=True,
                ),
            )

    return await call_next(request)


@app.get("/")
async def root():
    if FRONTEND_INDEX.exists():
        return FileResponse(FRONTEND_INDEX)
    return {
        "service": "EvoDoc Clinical Drug Safety Engine",
        "status": "running",
        "docs": "/docs",
        "health": "/api/v1/health",
        "analyze": "/api/v1/analyze",
    }


@app.get("/api/v1/health")
async def health() -> dict:
    uptime_seconds = int(time.time() - app.state.started_at)
    cache_stats = await app.state.cache.stats()
    llm_health = await app.state.llm_client.health()
    return {
        "status": "ok",
        "uptime_seconds": uptime_seconds,
        "llm": llm_health,
        "llm_circuit_breaker": get_llm_circuit_status(),
        "cache": cache_stats,
        "engine_runtime": get_engine_runtime_stats(),
        "llm_warmed_on_startup": app.state.llm_ready,
    }


@app.get("/api/v1/fallback-interactions")
async def fallback_interactions() -> dict:
    return {
        "count": len(app.state.fallback_data),
        "data": app.state.fallback_data,
    }


@app.post("/api/v1/analyze", response_model=DrugSafetyResponse)
async def analyze(request: Request, payload: DrugSafetyRequest) -> DrugSafetyResponse:
    idempotency_key = request.headers.get("Idempotency-Key")
    payload_dump = payload.model_dump(mode="json")
    payload_hash = app.state.idempotency_store.payload_hash(payload_dump)

    if idempotency_key:
        cached = await app.state.idempotency_store.get(idempotency_key)
        if cached is not None:
            cached_hash, cached_response = cached
            if cached_hash != payload_hash:
                raise HTTPException(status_code=409, detail="Idempotency key reused with a different payload")
            return DrugSafetyResponse(**cached_response)

    response = await analyze_drug_safety(
        request=payload,
        cache=app.state.cache,
        llm_client=app.state.llm_client,
        fallback_interactions=app.state.fallback_data,
        fallback_index=app.state.fallback_index,
        system_prompt=app.state.system_prompt,
    )

    response_payload = response.model_dump(mode="json")

    if idempotency_key:
        await app.state.idempotency_store.set(idempotency_key, payload_hash, response_payload)

    await app.state.audit_sink.enqueue(
        {
            "request_id": getattr(request.state, "request_id", None),
            "timestamp": int(time.time()),
            "source": response.source,
            "analysis_mode": response.analysis_mode,
            "patient_risk_score": response.patient_risk_score,
            "overall_risk_level": response.overall_risk_level,
            "requires_doctor_review": response.requires_doctor_review,
            "audit_trail": response.audit_trail.model_dump(mode="json"),
            "governance": response.governance.model_dump(mode="json"),
        }
    )

    return response
