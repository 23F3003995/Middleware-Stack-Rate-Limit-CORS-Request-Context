"""GET /ping with three composed middleware layers.

Layer order (outermost -> innermost):
    CORS -> request context -> rate limiter -> route
so every response, including 429s, carries CORS headers and X-Request-ID.

Env vars:
    EMAIL          your logged-in address (required)
    EXTRA_ORIGINS  comma-separated extra allowed origins, e.g. the exam page origin
Run (single worker, state is in memory):
    uvicorn ping_api:app --host 0.0.0.0 --port $PORT
"""
import math
import os
import time
import uuid
from collections import defaultdict, deque

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

EMAIL = os.environ.get("EMAIL", "").strip()
if not EMAIL:
    raise RuntimeError("Set the EMAIL environment variable to your logged-in address")

ASSIGNED_ORIGIN = "https://app-lugkhn.example.com"
EXTRA_ORIGINS = [o.strip().rstrip("/") for o in os.environ.get("EXTRA_ORIGINS", "").split(",") if o.strip()]
ALLOWED_ORIGINS = [ASSIGNED_ORIGIN, *EXTRA_ORIGINS]   # explicit list, never "*"

BUCKET = 11            # B requests ...
WINDOW_SECONDS = 10.0  # ... per 10 seconds, per X-Client-Id

app = FastAPI(title="Ping API")
_hits: dict[str, deque] = defaultdict(deque)


# ---- Middleware 3 (innermost): per-client rate limiter
@app.middleware("http")
async def rate_limiter(request: Request, call_next):
    client_id = request.headers.get("x-client-id")
    if request.method == "OPTIONS" or not client_id:
        return await call_next(request)

    now = time.monotonic()
    q = _hits[client_id]
    while q and now - q[0] >= WINDOW_SECONDS:
        q.popleft()
    if len(q) >= BUCKET:
        retry_after = max(1, math.ceil(WINDOW_SECONDS - (now - q[0])))
        return JSONResponse(
            {"detail": "Rate limit exceeded", "request_id": request.state.request_id},
            status_code=429,
            headers={"Retry-After": str(retry_after)},
        )
    q.append(now)
    return await call_next(request)


# ---- Middleware 1 (middle): request context
@app.middleware("http")
async def request_context(request: Request, call_next):
    rid = (request.headers.get("x-request-id") or "").strip() or str(uuid.uuid4())
    request.state.request_id = rid
    response = await call_next(request)
    response.headers["X-Request-ID"] = rid
    return response


# ---- Middleware 2 (outermost): scoped CORS, added last
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["X-Request-ID", "X-Client-Id", "Content-Type"],
    expose_headers=["X-Request-ID", "Retry-After"],
)


@app.get("/ping")
def ping(request: Request):
    return {"email": EMAIL, "request_id": request.state.request_id}


@app.get("/health")
def health():
    return {"ok": True}
