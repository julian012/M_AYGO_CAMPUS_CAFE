"""Pure business rules for orders: no AWS, no HTTP."""
import hashlib
import json

from common import ApiError

MAX_LINES = 10
MAX_QUANTITY = 10
MAX_KEY_LENGTH = 128
MAX_ID_LENGTH = 64


def parse_create_request(headers, raw_body):
    """Validate a POST /orders request. Returns (idempotency_key, items)."""
    key = (headers or {}).get("idempotency-key", "")
    if not isinstance(key, str) or not key.strip():
        raise ApiError(400, "MISSING_IDEMPOTENCY_KEY", "Idempotency-Key header is required.")
    key = key.strip()
    if len(key) > MAX_KEY_LENGTH:
        raise ApiError(400, "INVALID_IDEMPOTENCY_KEY", "Idempotency-Key is too long.")

    try:
        body = json.loads(raw_body or "")
    except (TypeError, ValueError):
        raise ApiError(400, "INVALID_REQUEST", "Body must be valid JSON.")
    if not isinstance(body, dict) or not isinstance(body.get("items"), list):
        raise ApiError(400, "INVALID_REQUEST", "Body must contain an items list.")

    raw_items = body["items"]
    if not 1 <= len(raw_items) <= MAX_LINES:
        raise ApiError(400, "INVALID_REQUEST", "An order needs 1 to 10 product lines.")

    items, seen = [], set()
    for line in raw_items:
        if not isinstance(line, dict):
            raise ApiError(400, "INVALID_REQUEST", "Each item must be an object.")
        product_id, quantity = line.get("productId"), line.get("quantity")
        if not isinstance(product_id, str) or not product_id.strip() or len(product_id) > MAX_ID_LENGTH:
            raise ApiError(400, "INVALID_REQUEST", "Each item needs a productId.")
        # bool is a subclass of int in Python; reject it explicitly.
        if isinstance(quantity, bool) or not isinstance(quantity, int) or not 1 <= quantity <= MAX_QUANTITY:
            raise ApiError(400, "INVALID_REQUEST", "quantity must be an integer from 1 to 10.")
        if product_id in seen:
            raise ApiError(400, "DUPLICATE_PRODUCT", "A product may appear only once per order.")
        seen.add(product_id)
        # Any client-supplied price or total is ignored on purpose.
        items.append({"productId": product_id, "quantity": quantity})
    return key, items


def fingerprint(items):
    """Stable hash of the normalized request, independent of line order."""
    normalized = sorted(
        ({"productId": i["productId"], "quantity": i["quantity"]} for i in items),
        key=lambda i: i["productId"],
    )
    return hashlib.sha256(json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def build_order(order_id, items, products, now):
    """Compute the order from server-side product data (price snapshot)."""
    lines, total, currency = [], 0, None
    for item in items:
        product = products[item["productId"]]
        currency = currency or product["currency"]
        if product["currency"] != currency:
            raise ApiError(503, "CATALOG_INCONSISTENT", "Catalog returned mixed currencies.")
        lines.append(
            {
                "productId": product["productId"],
                "name": product["name"],
                "quantity": item["quantity"],
                "unitPriceCents": product["priceCents"],
            }
        )
        total += product["priceCents"] * item["quantity"]
    return {
        "orderId": order_id,
        "status": "CREATED",
        "items": lines,
        "totalCents": total,
        "currency": currency,
        "createdAt": now,
        "updatedAt": now,
    }
