
import base64
import math
import time
import uuid
from collections import defaultdict, deque
from threading import Lock

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel


app = FastAPI(title="Q11 Orders API")

# Allow the IIT Madras assignment grader to access the API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://exam.sanand.workers.dev",
        "https://q11-orders-api.vercel.app",
        "http://localhost:3000",
        "http://127.0.0.1:9000",
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

TOTAL_ORDERS = 42
RATE_LIMIT = 19
WINDOW_SECONDS = 10

# In-memory stores
idempotency_store = {}
request_times = defaultdict(deque)
data_lock = Lock()


class OrderRequest(BaseModel):
    item: str = "default item"
    quantity: int = 1


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    # CORS preflight requests must not consume the rate limit.
    if request.method == "OPTIONS":
        return await call_next(request)

    client_id = request.headers.get("X-Client-Id")
    if not client_id:
        client_id = request.client.host if request.client else "anonymous"

    now = time.monotonic()

    with data_lock:
        times = request_times[client_id]

        while times and now - times[0] >= WINDOW_SECONDS:
            times.popleft()

        if len(times) >= RATE_LIMIT:
            retry_after = max(
                1, math.ceil(WINDOW_SECONDS - (now - times[0]))
            )
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded"},
                headers={"Retry-After": str(retry_after)},
            )

        times.append(now)

    return await call_next(request)


@app.get("/")
def home():
    return {
        "message": "Q11 Orders API is running",
        "total_orders": TOTAL_ORDERS,
        "rate_limit": RATE_LIMIT,
        "window_seconds": WINDOW_SECONDS,
    }


@app.post("/orders", status_code=201)
def create_order(
    body: OrderRequest | None = None,
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key"
    ),
):
    if not idempotency_key:
        raise HTTPException(
            status_code=400,
            detail="Idempotency-Key header is required",
        )

    with data_lock:
        if idempotency_key in idempotency_store:
            return idempotency_store[idempotency_key]

        body = body or OrderRequest()
        order = {
            "id": str(uuid.uuid4()),
            "item": body.item,
            "quantity": body.quantity,
        }
        idempotency_store[idempotency_key] = order
        return order


def encode_cursor(offset: int) -> str:
    raw = str(offset).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> int:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        offset = int(
            base64.urlsafe_b64decode(padded).decode("utf-8")
        )
        if offset < 0 or offset > TOTAL_ORDERS:
            raise ValueError
        return offset
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid cursor")


@app.get("/orders")
def list_orders(
    limit: int = Query(default=10, ge=1, le=100),
    cursor: str | None = Query(default=None),
):
    offset = decode_cursor(cursor) if cursor else 0
    end = min(offset + limit, TOTAL_ORDERS)

    items = [
        {"id": order_id}
        for order_id in range(offset + 1, end + 1)
    ]

    next_cursor = encode_cursor(end) if end < TOTAL_ORDERS else None

    return {
        "items": items,
        "next_cursor": next_cursor,
    }
