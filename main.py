import base64
import math
import time
import uuid
from collections import defaultdict, deque
from threading import Lock
from typing import Any, Dict, Optional

from fastapi import FastAPI, Header, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

app = FastAPI(title="Q11 Orders API")

# Setup CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Retry-After", "*"],
)

TOTAL_ORDERS = 42
RATE_LIMIT = 19
WINDOW_SECONDS = 10.0

# In-memory stores
idempotency_store: Dict[str, Dict[str, Any]] = {}
request_times: Dict[str, deque] = defaultdict(deque)
data_lock = Lock()


@app.middleware("http")
async def rate_limit_and_cors_middleware(request: Request, call_next):
    # Preflight OPTIONS requests should pass through without rate limiting
    if request.method == "OPTIONS":
        response = await call_next(request)
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "*"
        response.headers["Access-Control-Allow-Headers"] = "*"
        return response

    client_id = request.headers.get("X-Client-Id")

    # Rate limiting applies strictly per client ID when X-Client-Id is provided
    if client_id:
        now = time.time()
        with data_lock:
            times = request_times[client_id]

            # Evict timestamps older than 10 seconds window
            while times and (now - times[0]) >= WINDOW_SECONDS:
                times.popleft()

            if len(times) >= RATE_LIMIT:
                retry_after = max(1, math.ceil(WINDOW_SECONDS - (now - times[0])))
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Rate limit exceeded"},
                    headers={
                        "Retry-After": str(retry_after),
                        "Access-Control-Allow-Origin": "*",
                        "Access-Control-Allow-Methods": "*",
                        "Access-Control-Allow-Headers": "*",
                        "Access-Control-Expose-Headers": "Retry-After",
                    },
                )

            times.append(now)

    response = await call_next(request)
    # Guarantee CORS headers on all outgoing responses
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Expose-Headers"] = "Retry-After"
    return response


@app.get("/")
def home():
    return {
        "message": "Q11 Orders API is running",
        "total_orders": TOTAL_ORDERS,
        "rate_limit": RATE_LIMIT,
        "window_seconds": WINDOW_SECONDS,
    }


@app.post("/orders", status_code=status.HTTP_201_CREATED)
async def create_order(
    request: Request,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    try:
        body = await request.json()
    except Exception:
        body = {}

    with data_lock:
        if idempotency_key and idempotency_key in idempotency_store:
            return JSONResponse(
                status_code=status.HTTP_201_CREATED,
                content=idempotency_store[idempotency_key],
            )

        order_id = str(uuid.uuid4())
        order_data = {
            "id": order_id,
            "status": "created",
            "items": body.get("items", []),
        }

        if idempotency_key:
            idempotency_store[idempotency_key] = order_data

        return JSONResponse(
            status_code=status.HTTP_201_CREATED,
            content=order_data,
        )


def encode_cursor(offset: int) -> str:
    raw = str(offset).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> int:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        offset = int(base64.urlsafe_b64decode(padded).decode("utf-8"))
        if offset < 0 or offset > TOTAL_ORDERS:
            raise ValueError
        return offset
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid cursor")


@app.get("/orders")
def list_orders(
    limit: int = Query(default=10, ge=1),
    cursor: Optional[str] = Query(default=None),
):
    offset = decode_cursor(cursor) if cursor else 0
    end = min(offset + limit, TOTAL_ORDERS)

    items = [{"id": order_id} for order_id in range(offset + 1, end + 1)]
    next_cursor = encode_cursor(end) if end < TOTAL_ORDERS else None

    return {
        "items": items,
        "next_cursor": next_cursor,
    }
