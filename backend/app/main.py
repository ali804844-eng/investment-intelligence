"""FastAPI application factory."""
from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import agent, auth, dashboard, learning, market, misc, portfolios, research
from app.config import get_settings


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = {"ts": self.formatTime(record), "level": record.levelname, "logger": record.name, "msg": record.getMessage()}
        for k in ("request_id", "path", "status", "duration_ms", "method"):
            if hasattr(record, k):
                base[k] = getattr(record, k)
        return json.dumps(base)


def configure_logging() -> None:
    h = logging.StreamHandler()
    h.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [h]
    root.setLevel(logging.INFO)


log = logging.getLogger("api")


def create_app() -> FastAPI:
    s = get_settings()
    if s.app_env != "test":
        configure_logging()
    if s.is_production:
        problems = s.validate_for_production()
        if any("SECRET_KEY" in p for p in problems):
            raise RuntimeError("Refusing to start in production with an insecure SECRET_KEY")
    app = FastAPI(title="Investment Intelligence API", version="1.0.0",
                  docs_url="/api/docs" if not s.is_production else None, openapi_url="/api/openapi.json")
    app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in s.cors_origins.split(",") if o.strip()],
                       allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

    @app.middleware("http")
    async def headers_and_logging(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        t0 = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            log.exception("unhandled error", extra={"request_id": rid, "path": request.url.path})
            response = JSONResponse({"detail": "Internal server error", "request_id": rid}, status_code=500)
        response.headers["X-Request-ID"] = rid
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if not request.url.path.startswith("/api/docs"):
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; "
                "connect-src 'self'; frame-ancestors 'none'")
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
            log.info("request", extra={"request_id": rid, "path": request.url.path, "method": request.method,
                                       "status": response.status_code, "duration_ms": round((time.perf_counter() - t0) * 1000, 1)})
        return response

    for r in (auth, dashboard, market, portfolios, research, learning, agent, misc):
        app.include_router(r.router)

    static = Path(__file__).resolve().parent.parent / "static"
    if static.is_dir():
        app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith("api/"):
                return JSONResponse({"detail": "Not found"}, status_code=404)
            f = static / path
            if path and f.is_file() and static in f.resolve().parents:
                return FileResponse(f)
            return FileResponse(static / "index.html")

    return app


app = create_app()
