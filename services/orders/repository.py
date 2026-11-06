"""DynamoDB adapter for the Orders table (orders + idempotency records).

Single table, partition key `pk`:
  ORDER#<orderId>   the order + ownerIssuer/ownerSubject (never returned to clients)
  IDEMP#<sha256>    sha256 of the JSON tuple [iss, sub, clientKey]; stores the owner pair,
                    fingerprint, orderId and the stored creation response
"""
import hashlib
import json

import boto3
from botocore.exceptions import ClientError

from common import plain

OWNER_FIELDS = ("ownerIssuer", "ownerSubject")


def idempotency_pk(principal, key):
    """Collision-safe, principal-scoped key. JSON encoding makes the tuple unambiguous
    (no delimiter ambiguity), and hashing bounds the length."""
    tuple_json = json.dumps([principal.iss, principal.sub, key], separators=(",", ":"), ensure_ascii=True)
    return "IDEMP#" + hashlib.sha256(tuple_json.encode()).hexdigest()


class DynamoOrderRepository:
    def __init__(self, table_name, table=None):
        self.table_name = table_name
        self.table = table or boto3.resource("dynamodb").Table(table_name)
        self.client = self.table.meta.client

    def get_idempotency(self, principal, key):
        item = self.table.get_item(Key={"pk": idempotency_pk(principal, key)}, ConsistentRead=True).get("Item")
        if not item:
            return None
        return {
            "ownerIssuer": item.get("ownerIssuer"),
            "ownerSubject": item.get("ownerSubject"),
            "fingerprint": item["fingerprint"],
            "orderId": item["orderId"],
            "response": json.loads(item["response"]),
        }

    def create_order_atomic(self, order, principal, key, fingerprint):
        """Write the order and its idempotency record in ONE transaction.

        Both puts are conditional on the key not existing, so concurrent
        identical submissions cannot both succeed. Returns False when another
        request already claimed the key (or order id).
        """
        owner = {"ownerIssuer": principal.iss, "ownerSubject": principal.sub}
        order_item = {"pk": f"ORDER#{order['orderId']}", **order, **owner}
        idemp_item = {
            "pk": idempotency_pk(principal, key),
            **owner,
            "fingerprint": fingerprint,
            "orderId": order["orderId"],
            "response": json.dumps(order),
            "createdAt": order["createdAt"],
        }
        # table.meta.client is the resource's client: it serializes plain Python values itself.
        try:
            self.client.transact_write_items(
                TransactItems=[
                    {"Put": {"TableName": self.table_name, "Item": order_item,
                             "ConditionExpression": "attribute_not_exists(pk)"}},
                    {"Put": {"TableName": self.table_name, "Item": idemp_item,
                             "ConditionExpression": "attribute_not_exists(pk)"}},
                ]
            )
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "TransactionCanceledException":
                reasons = exc.response.get("CancellationReasons", [])
                if any(r.get("Code") == "ConditionalCheckFailed" for r in reasons):
                    return False
            raise

    def get_order(self, order_id, principal):
        """Return the order only if its stored owner equals the principal.
        Missing, foreign and legacy ownerless orders all return None."""
        item = self.table.get_item(Key={"pk": f"ORDER#{order_id}"}, ConsistentRead=True).get("Item")
        if not item or item.get("ownerIssuer") != principal.iss or item.get("ownerSubject") != principal.sub:
            return None
        return self._strip(item)

    def cancel_order(self, order_id, principal, now):
        """CREATED -> CANCELLED, only for the owner. The owner pair and the allowed status
        are enforced in the write condition itself. Returns the order, or None if it
        does not exist or is not the principal's."""
        try:
            item = self.table.update_item(
                Key={"pk": f"ORDER#{order_id}"},
                UpdateExpression="SET #s = :cancelled, updatedAt = :now",
                ConditionExpression="ownerIssuer = :iss AND ownerSubject = :sub AND #s = :created",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={
                    ":cancelled": "CANCELLED", ":created": "CREATED", ":now": now,
                    ":iss": principal.iss, ":sub": principal.sub,
                },
                ReturnValues="ALL_NEW",
            )["Attributes"]
            return self._strip(item)
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
            # Not owner / missing / already cancelled. Re-read ONLY through the authorized
            # path, so a foreign order is indistinguishable from a missing one.
            return self.get_order(order_id, principal)

    @staticmethod
    def _strip(item):
        if not item:
            return None
        item = plain(item)
        item.pop("pk", None)
        for field in OWNER_FIELDS:
            item.pop(field, None)
        return item
