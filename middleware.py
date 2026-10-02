import json
import time
import traceback
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

# ANSI color codes for terminal output
RESET = "\033[0m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
CYAN = "\033[36m"
BOLD = "\033[1m"
DIM = "\033[2m"

# Never write these values to the logs.
SENSITIVE_KEYS = {"password", "password_hash", "access_token", "token", "authorization"}
MAX_BODY_CHARS = 2000
SKIP_BODY_PATHS = {"/docs", "/redoc", "/openapi.json"}


def _redact(value):
    if isinstance(value, dict):
        return {
            k: "***" if k.lower() in SENSITIVE_KEYS else _redact(v) for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return value


def _format_body(raw: bytes, content_type: str) -> str:
    """Render a body for the log: JSON is redacted, everything else is summarised."""
    if not raw:
        return "-"
    if "json" not in content_type:
        return f"<{content_type or 'unknown'}, {len(raw)} bytes>"
    try:
        text = json.dumps(_redact(json.loads(raw)), ensure_ascii=False)
    except ValueError:
        text = "<invalid json>"
    if len(text) > MAX_BODY_CHARS:
        text = text[:MAX_BODY_CHARS] + f"... [truncated, {len(text)} chars]"
    return text


def _status_color(status_code: int) -> str:
    if status_code < 300:
        return GREEN
    if status_code < 400:
        return CYAN
    if status_code < 500:
        return YELLOW
    return RED


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start_time = time.perf_counter()
        log_bodies = request.url.path not in SKIP_BODY_PATHS

        request_body = b""
        if log_bodies:
            request_body = await request.body()  # Starlette caches it for the route handler

        try:
            response = await call_next(request)
        except Exception:
            # Handle here (inside CORSMiddleware) rather than letting Starlette's outer error
            # handler answer: that 500 has no CORS headers, so browsers block it and the
            # frontend reports a network error instead of the real failure.
            print(f"{RED}{traceback.format_exc()}{RESET}")
            response = JSONResponse(status_code=500, content={"detail": "Internal server error"})

        response_body = b""
        if log_bodies and hasattr(response, "body_iterator"):
            # Drain the streamed body so it can be logged, then rebuild the response.
            response_body = b"".join([chunk async for chunk in response.body_iterator])
            response = Response(
                content=response_body,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type=response.media_type,
            )

        duration_ms = (time.perf_counter() - start_time) * 1000
        color = _status_color(response.status_code)
        client = request.client.host if request.client else "-"

        print(
            f"{BOLD}{request.method:<7}{RESET} "
            f"{request.url.path:<40} "
            f"{color}{response.status_code}{RESET} "
            f"{duration_ms:>7.2f}ms "
            f"from {client}"
        )
        if log_bodies:
            query = f"?{request.url.query}" if request.url.query else ""
            print(f"{DIM}  request  {request.method} {request.url.path}{query} "
                  f"{_format_body(request_body, request.headers.get('content-type', ''))}{RESET}")
            print(f"{DIM}  response {response.status_code} "
                  f"{_format_body(response_body or response.body, response.headers.get('content-type', ''))}{RESET}")

        return response
