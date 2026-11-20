import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import Settings
from .db import Database
from .llm import AllProvidersUnavailable, LLMGateway
from .schemas import ChatMetadata, ChatRequest, ChatResponse, HealthResponse, HistoryResponse
from .simulation import parse_simulation_header

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
for noisy in ("LiteLLM", "LiteLLM Router", "LiteLLM Proxy", "litellm"):
    logging.getLogger(noisy).setLevel(logging.CRITICAL)
logger = logging.getLogger("app")


def create_app(settings: Settings | None = None, gateway: LLMGateway | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db = Database(settings.database_path)
        db.init_schema()
        app.state.db = db
        app.state.gateway = gateway or LLMGateway(
            api_keys=settings.api_keys, model=settings.llm_model, timeout_s=settings.llm_timeout_s
        )
        logger.info(
            "started: %d keys, model=%s, failure simulation=%s",
            len(settings.api_keys),
            settings.llm_model,
            settings.enable_failure_simulation,
        )
        yield

    app = FastAPI(title="Lyftr Chat API", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.origins,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    @app.exception_handler(Exception)
    async def unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": "internal server error"})

    # HEAD is accepted too: uptime monitors (e.g. UptimeRobot) probe with HEAD by default.
    @app.api_route("/health", methods=["GET", "HEAD"], response_model=HealthResponse)
    async def health(request: Request):
        db: Database = request.app.state.db
        gateway: LLMGateway = request.app.state.gateway
        if not await run_in_threadpool(db.ping):
            return JSONResponse(status_code=503, content={"detail": "database unavailable"})
        if not gateway.key_ids:
            return JSONResponse(status_code=503, content={"detail": "no API keys configured"})
        return HealthResponse(
            status="ok", keys_configured=len(gateway.key_ids), cache_enabled=gateway.cache_enabled
        )

    @app.post("/chat", response_model=ChatResponse)
    async def chat(
        body: ChatRequest,
        request: Request,
        x_simulate_failure: str | None = Header(default=None),
    ):
        started = time.perf_counter()
        db: Database = request.app.state.db
        gateway: LLMGateway = request.app.state.gateway

        override = body.system_prompt.strip() if body.system_prompt and body.system_prompt.strip() else None
        stored = await run_in_threadpool(db.get_system_prompt, body.session_id)
        system_prompt = override or stored or settings.default_system_prompt
        history = await run_in_threadpool(db.get_history, body.session_id)
        messages = (
            [{"role": "system", "content": system_prompt}]
            + [{"role": m["role"], "content": m["content"]} for m in history]
            + [{"role": "user", "content": body.message}]
        )

        failures = (
            parse_simulation_header(x_simulate_failure) if settings.enable_failure_simulation else {}
        )
        try:
            result = await gateway.complete(messages, simulated_failures=failures)
        except AllProvidersUnavailable:
            return JSONResponse(status_code=503, content={"detail": "all providers unavailable"})

        metadata = ChatMetadata(
            resolution_source="LLM_CACHE" if result.cache_hit else "LIVE_INFERENCE",
            model=result.model,
            latency_ms=int((time.perf_counter() - started) * 1000),
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            key_id=result.key_id,
            fallback_attempts=result.fallback_attempts,
        )
        await run_in_threadpool(
            db.save_turn, body.session_id, override, body.message, result.reply, metadata.model_dump()
        )
        return ChatResponse(session_id=body.session_id, reply=result.reply, metadata=metadata)

    @app.get("/sessions/{session_id}/messages", response_model=HistoryResponse, response_model_exclude_unset=True)
    async def session_messages(session_id: str, request: Request):
        db: Database = request.app.state.db
        if not await run_in_threadpool(db.session_exists, session_id):
            return JSONResponse(status_code=404, content={"detail": "session not found"})
        history = await run_in_threadpool(db.get_history, session_id)
        return HistoryResponse(session_id=session_id, messages=history)

    return app


app = create_app()
