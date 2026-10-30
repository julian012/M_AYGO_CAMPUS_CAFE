"""Orders business-rule tests against a moto-backed DynamoDB (real transactions/conditions)."""
import json
import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "services", "orders"))
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")

import boto3
from moto import mock_aws

import app
from common import ApiError
from repository import DynamoOrderRepository

PRODUCTS = {
    "coffee": {"productId": "coffee", "name": "Coffee", "priceCents": 350, "currency": "USD", "available": True},
    "tea": {"productId": "tea", "name": "Tea", "priceCents": 300, "currency": "USD", "available": True},
    "muffin": {"productId": "muffin", "name": "Muffin", "priceCents": 400, "currency": "USD", "available": False},
}


class FakeCatalog:
    def __init__(self, down=False):
        self.down = down
        self.calls = 0

    def get_product(self, product_id):
        self.calls += 1
        if self.down:
            raise ApiError(503, "CATALOG_UNAVAILABLE", "Product catalog is unavailable. Please retry.")
        return PRODUCTS.get(product_id)


def event(route, body=None, key=None, order_id=None):
    headers = {"content-type": "application/json"}
    if key:
        headers["idempotency-key"] = key
    return {
        "routeKey": route,
        "headers": headers,
        "body": json.dumps(body) if body is not None else None,
        "pathParameters": {"orderId": order_id} if order_id else None,
        "requestContext": {"requestId": "req-1", "domainName": "example.test"},
    }


@mock_aws
class OrdersTests(unittest.TestCase):
    def setUp(self):
        ddb = boto3.resource("dynamodb")
        self.table = ddb.create_table(
            TableName="orders",
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        self.repo = DynamoOrderRepository("orders", self.table)
        self.catalog = FakeCatalog()

    def call(self, ev, catalog=None):
        res = app.handler(ev, None, repo=self.repo, catalog=catalog or self.catalog)
        return res["statusCode"], json.loads(res["body"]), res["headers"]

    def create(self, key="k1", items=None, **kw):
        items = items if items is not None else [{"productId": "coffee", "quantity": 2}]
        return self.call(event("POST /orders", {"items": items}, key=key), **kw)

    def order_rows(self):
        return [i for i in self.table.scan()["Items"] if i["pk"].startswith("ORDER#")]

    # --- correctness -------------------------------------------------
    def test_create_computes_total_server_side(self):
        status, body, headers = self.create()
        self.assertEqual(status, 201)
        self.assertEqual(body["totalCents"], 700)
        self.assertEqual(body["status"], "CREATED")
        self.assertEqual(body["items"][0]["unitPriceCents"], 350)
        self.assertEqual(headers["Location"], f"/orders/{body['orderId']}")

    def test_client_price_override_is_ignored(self):
        ev = event("POST /orders", {"items": [{"productId": "coffee", "quantity": 2, "unitPriceCents": 1}],
                                    "totalCents": 1}, key="k-price")
        status, body, _ = self.call(ev)
        self.assertEqual((status, body["totalCents"]), (201, 700))

    def test_invalid_requests_return_400(self):
        bad_bodies = [
            [],
            [{"productId": "coffee", "quantity": 0}],
            [{"productId": "coffee", "quantity": 11}],
            [{"productId": "coffee", "quantity": 1.5}],
            [{"productId": "coffee", "quantity": True}],
            [{"productId": "coffee", "quantity": "2"}],
            [{"productId": "coffee", "quantity": 1}, {"productId": "coffee", "quantity": 2}],
            [{"quantity": 1}],
            [{"productId": f"p{i}", "quantity": 1} for i in range(11)],
        ]
        for i, items in enumerate(bad_bodies):
            status, body, _ = self.create(key=f"bad-{i}", items=items)
            self.assertEqual(status, 400, items)
            self.assertIn("requestId", body)
        self.assertEqual(self.order_rows(), [])

    def test_missing_key_and_bad_json_return_400(self):
        status, body, _ = self.call(event("POST /orders", {"items": [{"productId": "coffee", "quantity": 1}]}))
        self.assertEqual((status, body["code"]), (400, "MISSING_IDEMPOTENCY_KEY"))
        ev = event("POST /orders", key="k")
        ev["body"] = "{not json"
        self.assertEqual(self.call(ev)[0], 400)

    def test_unknown_product_400_and_unavailable_409(self):
        status, body, _ = self.create(key="u1", items=[{"productId": "ghost", "quantity": 1}])
        self.assertEqual((status, body["code"]), (400, "PRODUCT_NOT_FOUND"))
        status, body, _ = self.create(key="u2", items=[{"productId": "muffin", "quantity": 1}])
        self.assertEqual((status, body["code"]), (409, "PRODUCT_UNAVAILABLE"))
        self.assertEqual(self.order_rows(), [])

    # --- idempotency -------------------------------------------------
    def test_replay_returns_original_without_new_order_or_catalog_call(self):
        _, first, _ = self.create(key="same")
        calls = self.catalog.calls
        status, replay, _ = self.create(key="same")
        self.assertEqual(status, 200)
        self.assertEqual(replay, first)
        self.assertEqual(self.catalog.calls, calls)  # idempotency checked before Catalog
        self.assertEqual(len(self.order_rows()), 1)

    def test_same_key_different_request_conflicts(self):
        self.create(key="same")
        status, body, _ = self.create(key="same", items=[{"productId": "tea", "quantity": 1}])
        self.assertEqual((status, body["code"]), (409, "IDEMPOTENCY_KEY_CONFLICT"))
        self.assertEqual(len(self.order_rows()), 1)

    def test_replay_is_stable_after_price_change(self):
        _, first, _ = self.create(key="same")
        PRODUCTS["coffee"]["priceCents"] = 999
        try:
            _, replay, _ = self.create(key="same")
        finally:
            PRODUCTS["coffee"]["priceCents"] = 350
        self.assertEqual(replay["totalCents"], first["totalCents"])

    def test_five_concurrent_identical_requests_create_one_order(self):
        results, barrier = [], threading.Barrier(5)

        def worker():
            barrier.wait()
            results.append(self.create(key="race"))

        threads = [threading.Thread(target=worker) for _ in range(5)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(len(self.order_rows()), 1)
        self.assertEqual({r[1]["orderId"] for r in results}, {self.order_rows()[0]["orderId"]})
        self.assertEqual(sorted(r[0] for r in results).count(201), 1)
        self.assertTrue(all(r[0] in (200, 201) for r in results))

    # --- fault isolation ---------------------------------------------
    def test_catalog_failure_returns_503_and_stores_nothing(self):
        status, body, _ = self.create(key="down", catalog=FakeCatalog(down=True))
        self.assertEqual((status, body["code"]), (503, "CATALOG_UNAVAILABLE"))
        self.assertEqual(self.table.scan()["Items"], [])
        # The same key can still be used once Catalog recovers.
        self.assertEqual(self.create(key="down")[0], 201)

    # --- retrieve / cancel / persistence ------------------------------
    def test_get_order_survives_new_repository_instance(self):
        _, created, _ = self.create()
        fresh = DynamoOrderRepository("orders", boto3.resource("dynamodb").Table("orders"))
        res = app.handler(event("GET /orders/{orderId}", order_id=created["orderId"]), None, repo=fresh, catalog=self.catalog)
        self.assertEqual(res["statusCode"], 200)
        self.assertEqual(json.loads(res["body"]), created)

    def test_get_unknown_order_404(self):
        self.assertEqual(self.call(event("GET /orders/{orderId}", order_id="nope"))[0], 404)

    def test_cancel_is_idempotent_and_persisted(self):
        _, created, _ = self.create()
        oid = created["orderId"]
        s1, c1, _ = self.call(event("POST /orders/{orderId}/cancel", order_id=oid))
        s2, c2, _ = self.call(event("POST /orders/{orderId}/cancel", order_id=oid))
        self.assertEqual((s1, s2, c1["status"]), (200, 200, "CANCELLED"))
        self.assertEqual(c1, c2)  # second cancel changes nothing, incl. updatedAt
        self.assertEqual(self.call(event("GET /orders/{orderId}", order_id=oid))[1]["status"], "CANCELLED")

    def test_cancel_unknown_order_404_and_creates_nothing(self):
        self.assertEqual(self.call(event("POST /orders/{orderId}/cancel", order_id="ghost"))[0], 404)
        self.assertEqual(self.table.scan()["Items"], [])

    def test_unknown_route_404(self):
        self.assertEqual(self.call(event("DELETE /orders"))[0], 404)


if __name__ == "__main__":
    unittest.main()
