"""Orders service Lambda handler (HTTP API payload format 2.0). Owns the Orders table."""
import os
import time

from botocore.exceptions import BotoCoreError, ClientError

import domain
from catalog_client import CatalogClient
from common import ApiError, error_response, log, response
from repository import DynamoOrderRepository
from service import OrderService

_repo = None


def get_repo():
    global _repo
    if _repo is None:
        _repo = DynamoOrderRepository(os.environ["ORDERS_TABLE"])
    return _repo


def build_service(event, repo=None, catalog=None):
    if catalog is None:
        # Catalog is reached through the same gateway unless overridden
        # (used to inject a Catalog failure for the fault-isolation check).
        base_url = os.environ.get("CATALOG_BASE_URL") or "https://" + event["requestContext"]["domainName"]
        catalog = CatalogClient(
            base_url,
            deadline_seconds=float(os.environ.get("CATALOG_DEADLINE_SECONDS", "3")),
            attempt_timeout=float(os.environ.get("CATALOG_ATTEMPT_TIMEOUT_SECONDS", "1.5")),
        )
    return OrderService(repo or get_repo(), catalog)


def handler(event, context, repo=None, catalog=None):
    started = time.monotonic()
    request_id = event.get("requestContext", {}).get("requestId", "unknown")
    route = event.get("routeKey", "")
    extra = {}
    try:
        service = build_service(event, repo, catalog)
        params = event.get("pathParameters") or {}
        if route == "POST /orders":
            key, items = domain.parse_create_request(event.get("headers"), event.get("body"))
            status, order, headers = service.create_order(key, items)
            extra["orderId"] = order["orderId"]
            result = response(status, order, headers)
        elif route == "GET /orders/{orderId}":
            extra["orderId"] = params.get("orderId")
            result = response(200, service.get_order(params.get("orderId", "")))
        elif route == "POST /orders/{orderId}/cancel":
            extra["orderId"] = params.get("orderId")
            result = response(200, service.cancel_order(params.get("orderId", "")))
        else:
            raise ApiError(404, "ROUTE_NOT_FOUND", "Route not found.")
        log(request_id, route, "ok", started, status=result["statusCode"], **extra)
        return result
    except ApiError as err:
        log(request_id, route, err.code, started, status=err.status, **extra)
        return error_response(err, request_id)
    except (ClientError, BotoCoreError) as exc:
        log(request_id, route, "STORE_UNAVAILABLE", started, status=503, error=type(exc).__name__, **extra)
        return error_response(
            ApiError(503, "SERVICE_UNAVAILABLE", "Orders cannot be processed right now."), request_id
        )
