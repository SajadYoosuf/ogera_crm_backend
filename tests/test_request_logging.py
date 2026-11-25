"""The request-logging middleware must not hold back a server-sent event stream.

Uses a real HTTP server: the in-process test client collects the whole body before returning,
which would hide the problem.
"""
import socket
import threading
import time

import httpx
import uvicorn
from fastapi import FastAPI
from fastapi.responses import StreamingResponse

from middleware import RequestLoggingMiddleware


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_first_event_arrives_before_the_stream_ends():
    gate = threading.Event()
    app = FastAPI()
    app.add_middleware(RequestLoggingMiddleware)

    @app.get("/events")
    def events():
        def stream():
            yield "retry: 3000\n\n"
            gate.wait(timeout=5)  # the stream stays open, like the notification stream
            yield "event: notification\ndata: {}\n\n"

        return StreamingResponse(stream(), media_type="text/event-stream")

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        with httpx.stream("GET", f"http://127.0.0.1:{port}/events", timeout=10) as response:
            started = time.monotonic()
            first = next(response.iter_text())
            elapsed = time.monotonic() - started
            gate.set()
        assert "retry: 3000" in first
        assert elapsed < 2, f"first event took {elapsed:.1f}s; the middleware is holding the stream back"
    finally:
        gate.set()
        server.should_exit = True
        thread.join(timeout=5)
