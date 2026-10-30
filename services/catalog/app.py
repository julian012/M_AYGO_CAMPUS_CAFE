"""Catalog service: products, prices and availability. Owns the Catalog table."""
import os
import time

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from common import ApiError, error_response, log, plain, response

_table = None


def get_table():
    global _table
    if _table is None:
        _table = boto3.resource("dynamodb").Table(os.environ["CATALOG_TABLE"])
    return _table


def list_products(table):
    items, kwargs = [], {}
    while True:
        page = table.scan(**kwargs)
        items.extend(page.get("Items", []))
        if "LastEvaluatedKey" not in page:
            break
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    return sorted((plain(i) for i in items), key=lambda p: p["productId"])


def get_product(table, product_id):
    item = table.get_item(Key={"productId": product_id}).get("Item")
    if item is None:
        raise ApiError(404, "PRODUCT_NOT_FOUND", "Unknown product.")
    return plain(item)


def handler(event, context, table=None):
    started = time.monotonic()
    request_id = event.get("requestContext", {}).get("requestId", "unknown")
    route = event.get("routeKey", "")
    operation = route
    try:
        table = table or get_table()
        if route == "GET /products":
            result = response(200, {"items": list_products(table)})
        elif route == "GET /products/{productId}":
            product_id = (event.get("pathParameters") or {}).get("productId", "")
            result = response(200, get_product(table, product_id))
        else:
            raise ApiError(404, "ROUTE_NOT_FOUND", "Route not found.")
        log(request_id, operation, "ok", started, status=result["statusCode"])
        return result
    except ApiError as err:
        log(request_id, operation, err.code, started, status=err.status)
        return error_response(err, request_id)
    except (ClientError, BotoCoreError) as exc:
        log(request_id, operation, "STORE_UNAVAILABLE", started, status=503, error=type(exc).__name__)
        return error_response(
            ApiError(503, "SERVICE_UNAVAILABLE", "The catalog cannot be read right now."),
            request_id,
        )
