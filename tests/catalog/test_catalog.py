"""Catalog handler tests against a moto-backed DynamoDB."""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "services", "catalog"))
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")

import boto3
from botocore.exceptions import ClientError
from moto import mock_aws

import app

SEED = os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "seed_data.json")


def ev(route, product_id=None):
    return {
        "routeKey": route,
        "pathParameters": {"productId": product_id} if product_id else None,
        "requestContext": {"requestId": "req-1"},
    }


@mock_aws
class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.table = boto3.resource("dynamodb").create_table(
            TableName="catalog",
            KeySchema=[{"AttributeName": "productId", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "productId", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        with open(SEED) as f:
            for p in json.load(f):
                self.table.put_item(Item=p)

    def test_list_has_five_products_with_one_unavailable(self):
        res = app.handler(ev("GET /products"), None, table=self.table)
        items = json.loads(res["body"])["items"]
        self.assertEqual(res["statusCode"], 200)
        self.assertGreaterEqual(len(items), 5)
        self.assertEqual([p["productId"] for p in items if not p["available"]], ["muffin"])
        self.assertEqual(set(items[0]), {"productId", "name", "priceCents", "currency", "available"})
        self.assertIsInstance(items[0]["priceCents"], int)

    def test_get_product(self):
        res = app.handler(ev("GET /products/{productId}", "coffee"), None, table=self.table)
        self.assertEqual(res["statusCode"], 200)
        self.assertEqual(json.loads(res["body"])["priceCents"], 350)

    def test_unknown_product_404(self):
        res = app.handler(ev("GET /products/{productId}", "ghost"), None, table=self.table)
        self.assertEqual(res["statusCode"], 404)
        self.assertEqual(json.loads(res["body"])["code"], "PRODUCT_NOT_FOUND")

    def test_store_failure_returns_503(self):
        broken = mock.Mock()
        broken.scan.side_effect = ClientError({"Error": {"Code": "InternalServerError", "Message": "x"}}, "Scan")
        res = app.handler(ev("GET /products"), None, table=broken)
        body = json.loads(res["body"])
        self.assertEqual((res["statusCode"], body["code"]), (503, "SERVICE_UNAVAILABLE"))
        self.assertEqual(body["requestId"], "req-1")


if __name__ == "__main__":
    unittest.main()
