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


ISS = "https://tenant.example.test/"
ALICE = ("auth0|alice", ISS)
BOB = ("auth0|bob", ISS)
_UNSET = object()


def event(route, body=None, key=None, order_id=None, who=ALICE, claims=_UNSET):
    """Build an HTTP API v2 event. LOCAL FIXTURE: the claims are fabricated, mimicking what
    the gateway's JWT authorizer would inject; this does NOT test token verification."""
    if claims is _UNSET:
        claims = None if who is None else {"iss": who[1], "sub": who[0]}
    headers = {"content-type": "application/json"}
    if key:
        headers["idempotency-key"] = key
    return {
        "routeKey": route,
        "headers": headers,
        "body": json.dumps(body) if body is not None else None,
        "pathParameters": {"orderId": order_id} if order_id else None,
        "requestContext": {"requestId": "req-1", "domainName": "example.test", **(
            {"authorizer": {"jwt": {"claims": claims, "scopes": []}}} if claims is not None else {})},
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

    def create(self, key="k1", items=None, who=ALICE, catalog=None, extra=None):
        items = items if items is not None else [{"productId": "coffee", "quantity": 2}]
        body = {"items": items, **(extra or {})}
        return self.call(event("POST /orders", body, key=key, who=who), catalog=catalog)

    def get(self, oid, who=ALICE):
        return self.call(event("GET /orders/{orderId}", order_id=oid, who=who))

    def cancel(self, oid, who=ALICE):
        return self.call(event("POST /orders/{orderId}/cancel", order_id=oid, who=who))

    def raw_order(self, oid):
        return self.table.get_item(Key={"pk": f"ORDER#{oid}"}).get("Item")

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

    # --- S1: trusted context / forged owner ----------------------------
    def test_s1_missing_or_malformed_context_fails_closed_without_writes(self):
        bad_claims = [None, {}, {"sub": "x"}, {"iss": ISS}, {"iss": "", "sub": "x"}, {"iss": ISS, "sub": 5}, "str"]
        for claims in bad_claims:
            ev = event("POST /orders", {"items": [{"productId": "coffee", "quantity": 1}]}, key="k", claims=claims)
            self.assertEqual(self.call(ev)[0], 401, claims)
            self.assertEqual(self.call(event("GET /orders/{orderId}", order_id="x", claims=claims))[0], 401)
            self.assertEqual(self.call(event("POST /orders/{orderId}/cancel", order_id="x", claims=claims))[0], 401)
        self.assertEqual(self.table.scan()["Items"], [])
        self.assertEqual(self.catalog.calls, 0)

    def test_s1_identity_in_headers_or_body_is_ignored(self):
        ev = event("POST /orders", {"items": [{"productId": "coffee", "quantity": 1}]}, key="k", who=None)
        ev["headers"].update({"x-owner": "auth0|alice", "authorization": "Bearer x"})
        ev["authorizer"] = {"jwt": {"claims": {"iss": ISS, "sub": "auth0|alice"}}}  # wrong place
        self.assertEqual(self.call(ev)[0], 401)

    def test_s1_forged_owner_fields_rejected_and_nothing_persisted(self):
        for field in ("owner", "ownerSub", "ownerIssuer", "ownerSubject", "customerId"):
            status, body, _ = self.create(key=f"k-{field}", extra={field: "auth0|alice"}, who=BOB)
            self.assertEqual((status, body["code"]), (400, "FORBIDDEN_FIELD"), field)
        status, _, _ = self.create(key="k-line", items=[{"productId": "coffee", "quantity": 1, "owner": "x"}], who=BOB)
        self.assertEqual(status, 400)
        self.assertEqual(self.table.scan()["Items"], [])

    def test_created_order_persists_server_derived_owner_and_hides_it(self):
        _, created, _ = self.create(who=BOB)
        row = self.raw_order(created["orderId"])
        self.assertEqual((row["ownerIssuer"], row["ownerSubject"]), (ISS, "auth0|bob"))
        self.assertNotIn("ownerSubject", created)
        self.assertNotIn("ownerIssuer", created)
        self.assertNotIn("pk", created)

    # --- S2: reads ------------------------------------------------------
    def test_s2_read_own_foreign_missing_and_legacy_ownerless(self):
        _, mine, _ = self.create(key="a", who=ALICE)
        self.table.put_item(Item={"pk": "ORDER#legacy", "orderId": "legacy", "status": "CREATED", "items": [], "totalCents": 0})
        self.table.put_item(Item={"pk": "ORDER#half", "orderId": "half", "status": "CREATED", "ownerIssuer": ISS})
        s_own, own, _ = self.get(mine["orderId"], ALICE)
        self.assertEqual((s_own, own), (200, mine))
        bodies = []
        for oid, who in ((mine["orderId"], BOB), ("no-such", ALICE), ("legacy", ALICE), ("half", ALICE)):
            status, body, _ = self.get(oid, who)
            self.assertEqual(status, 404)
            bodies.append(body)
        for body in bodies:
            self.assertEqual(set(body), {"code", "message", "requestId"})
            self.assertEqual((body["code"], body["message"]), ("ORDER_NOT_FOUND", "Order not found"))
        self.assertEqual(self.raw_order("legacy").get("ownerSubject"), None)  # never auto-claimed

    # --- S3/S4: cancellation -------------------------------------------
    def test_s3_foreign_cancel_is_404_and_changes_nothing(self):
        _, mine, _ = self.create(who=ALICE)
        before = self.raw_order(mine["orderId"])
        status, body, _ = self.cancel(mine["orderId"], BOB)
        self.assertEqual((status, body["message"]), (404, "Order not found"))
        self.assertEqual(self.raw_order(mine["orderId"]), before)
        # and Alice still cancels, then repeats (documented baseline: 200, unchanged)
        s1, c1, _ = self.cancel(mine["orderId"], ALICE)
        s2, c2, _ = self.cancel(mine["orderId"], ALICE)
        self.assertEqual((s1, s2, c1["status"], c1), (200, 200, "CANCELLED", c2))
        # Bob cannot even see the cancelled state
        self.assertEqual(self.cancel(mine["orderId"], BOB)[0], 404)
        self.assertEqual(self.get(mine["orderId"], BOB)[0], 404)

    def test_s3_cancel_legacy_ownerless_is_404_and_untouched(self):
        self.table.put_item(Item={"pk": "ORDER#legacy", "orderId": "legacy", "status": "CREATED"})
        self.assertEqual(self.cancel("legacy", ALICE)[0], 404)
        self.assertEqual(self.raw_order("legacy")["status"], "CREATED")

    def test_s4_cancel_write_condition_includes_owner_and_status(self):
        _, mine, _ = self.create()
        captured = {}
        real = self.table.update_item

        def spy(**kwargs):
            captured.update(kwargs)
            return real(**kwargs)

        self.table.update_item = spy
        self.cancel(mine["orderId"], ALICE)
        cond = captured["ConditionExpression"]
        self.assertIn("ownerIssuer", cond)
        self.assertIn("ownerSubject", cond)
        self.assertIn("#s = :created", cond)
        values = captured["ExpressionAttributeValues"]
        self.assertEqual((values[":iss"], values[":sub"]), (ISS, "auth0|alice"))

    def test_s4_stale_read_cannot_win_against_racing_status_change(self):
        """A pre-check would pass, but the status changes before the write: the conditional
        update must refuse and must not overwrite the newer state."""
        _, mine, _ = self.create()
        oid = mine["orderId"]
        real = self.table.update_item

        def racing(**kwargs):
            self.table.update_item = real
            self.table.update_item(  # competing writer moves the order out of CREATED
                Key={"pk": f"ORDER#{oid}"}, UpdateExpression="SET #s = :x",
                ExpressionAttributeNames={"#s": "status"}, ExpressionAttributeValues={":x": "FULFILLED"})
            return real(**kwargs)

        self.table.update_item = racing
        status, body, _ = self.cancel(oid, ALICE)
        self.assertEqual(self.raw_order(oid)["status"], "FULFILLED")  # stale transition did not win
        self.assertEqual(status, 200)  # baseline contract: owner sees current state
        self.assertEqual(body["status"], "FULFILLED")

    def test_s4_owner_flip_between_check_and_write_is_refused(self):
        _, mine, _ = self.create(who=ALICE)
        oid = mine["orderId"]
        real = self.table.update_item

        def racing(**kwargs):
            self.table.update_item = real
            self.table.update_item(Key={"pk": f"ORDER#{oid}"}, UpdateExpression="SET ownerSubject = :b",
                                   ExpressionAttributeValues={":b": "auth0|bob"})
            return real(**kwargs)

        self.table.update_item = racing
        self.assertEqual(self.cancel(oid, ALICE)[0], 404)
        self.assertEqual(self.raw_order(oid)["status"], "CREATED")

    # --- S5/S6/S7/S10: idempotency -------------------------------------
    def test_s5_replay_then_changed_payload_conflicts(self):
        _, first, _ = self.create(key="same")
        s_replay, replay, _ = self.create(key="same")
        s_conf, conf, _ = self.create(key="same", items=[{"productId": "tea", "quantity": 1}])
        self.assertEqual((s_replay, replay), (200, first))
        self.assertEqual((s_conf, conf["code"]), (409, "IDEMPOTENCY_KEY_CONFLICT"))
        self.assertEqual(len(self.order_rows()), 1)

    def test_s5_equivalent_json_canonicalization(self):
        _, first, _ = self.create(key="c", items=[{"productId": "coffee", "quantity": 1}, {"productId": "tea", "quantity": 2}])
        status, replay, _ = self.create(key="c", items=[{"quantity": 2, "productId": "tea"}, {"quantity": 1, "productId": "coffee"}])
        self.assertEqual((status, replay), (200, first))

    def test_s6_bob_same_client_key_gets_independent_order(self):
        _, alice, _ = self.create(key="shared", who=ALICE)
        status, bob, _ = self.create(key="shared", who=BOB)
        self.assertEqual(status, 201)
        self.assertNotEqual(bob["orderId"], alice["orderId"])
        self.assertEqual(self.raw_order(bob["orderId"])["ownerSubject"], "auth0|bob")
        # Bob with a DIFFERENT payload on the same key: no conflict with Alice's record
        status, other, _ = self.create(key="shared2", who=ALICE)
        status, bob2, _ = self.create(key="shared2", who=BOB, items=[{"productId": "tea", "quantity": 3}])
        self.assertEqual(status, 201)
        self.assertEqual(len(self.order_rows()), 4)
        # Bob's replay is Bob's own
        self.assertEqual(self.create(key="shared", who=BOB)[1]["orderId"], bob["orderId"])

    def test_idempotency_key_encoding_has_no_collisions(self):
        from auth import Principal
        from repository import idempotency_pk
        a = idempotency_pk(Principal("iss", "a|b"), "c")
        b = idempotency_pk(Principal("iss", "a"), "b|c")
        c = idempotency_pk(Principal("iss|a", "b"), "c")
        self.assertEqual(len({a, b, c}), 3)
        self.assertLessEqual(len(a), 80)

    def test_s7_concurrent_duplicates_from_same_principal_one_order(self):
        results, barrier = [], threading.Barrier(6)

        def worker(who):
            barrier.wait()
            results.append((who, self.create(key="race", who=who)))

        threads = [threading.Thread(target=worker, args=(ALICE if i < 3 else BOB,)) for i in range(6)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(len(self.order_rows()), 2)  # one per principal
        for who in (ALICE, BOB):
            ids = {r[1]["orderId"] for w, r in results if w == who and r[0] in (200, 201)}
            self.assertEqual(len(ids), 1)
            self.assertEqual(self.raw_order(ids.pop())["ownerSubject"], who[0])
        self.assertEqual(len([i for i in self.table.scan()["Items"] if i["pk"].startswith("IDEMP#")]), 2)

    def test_s7_conditional_failure_replays_the_winner_and_leaves_no_orphan(self):
        """Simulate losing the race: winner's record appears between our lookup and our write."""
        first = self.create(key="x")[1]
        real_get = self.repo.get_idempotency
        calls = {"n": 0}

        def blind_first(principal, key):
            calls["n"] += 1
            return None if calls["n"] == 1 else real_get(principal, key)

        self.repo.get_idempotency = blind_first
        status, body, _ = self.create(key="x")
        self.assertEqual((status, body), (200, first))
        self.assertEqual(len(self.order_rows()), 1)  # transaction rolled back both puts

    def test_s10_replay_after_catalog_price_change_keeps_snapshot_for_same_principal_only(self):
        _, first, _ = self.create(key="p", who=ALICE)
        PRODUCTS["coffee"]["priceCents"] = 999
        try:
            _, replay, _ = self.create(key="p", who=ALICE)
            _, bob, _ = self.create(key="p", who=BOB)
        finally:
            PRODUCTS["coffee"]["priceCents"] = 350
        self.assertEqual(replay, first)
        self.assertEqual(bob["totalCents"], 999 * 2)  # Bob's new order is priced fresh

    # --- S8: pricing / catalog failure --------------------------------
    def test_s8_tampered_price_ignored_and_catalog_failure_leaves_nothing(self):
        status, body, _ = self.create(key="t", items=[{"productId": "coffee", "quantity": 1, "unitPriceCents": 1}],
                                      extra={"totalCents": 1})
        self.assertEqual((status, body["totalCents"]), (201, 350))
        status, body, _ = self.create(key="d", who=BOB, catalog=FakeCatalog(down=True))
        self.assertEqual((status, body["code"]), (503, "CATALOG_UNAVAILABLE"))
        self.assertEqual(len(self.table.scan()["Items"]), 2)  # only the first order + its record

    # --- S9: logs ---------------------------------------------------
    def test_s9_logs_have_request_id_and_no_identity_or_tokens(self):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.create(key="l", who=ALICE)
            self.get("nope", BOB)
        out = buf.getvalue()
        self.assertIn('"requestId": "req-1"', out)
        for secret in ("auth0|alice", "auth0|bob", ISS, "Bearer"):
            self.assertNotIn(secret, out)

    # --- A5: same sub under a different issuer ----------------------
    def test_a5_same_subject_different_issuer_is_a_different_principal(self):
        other = ("auth0|alice", "https://other.example.test/")
        _, mine, _ = self.create(key="k", who=ALICE)
        self.assertEqual(self.get(mine["orderId"], other)[0], 404)
        self.assertEqual(self.cancel(mine["orderId"], other)[0], 404)
        status, theirs, _ = self.create(key="k", who=other)
        self.assertEqual(status, 201)
        self.assertNotEqual(theirs["orderId"], mine["orderId"])

    def test_expected_issuer_pin_rejects_other_issuer(self):
        os.environ["EXPECTED_ISSUER"] = ISS
        try:
            self.assertEqual(self.create(key="a", who=ALICE)[0], 201)
            self.assertEqual(self.create(key="b", who=("auth0|x", "https://evil.example.test/"))[0], 401)
        finally:
            del os.environ["EXPECTED_ISSUER"]

    def test_unknown_route_404(self):
        self.assertEqual(self.call(event("DELETE /orders"))[0], 404)


if __name__ == "__main__":
    unittest.main()
