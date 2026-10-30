"""DynamoDB adapter for the Orders table (orders + idempotency records).

Single table, partition key `pk`:
  ORDER#<orderId>   the order
  IDEMP#<key>       fingerprint, orderId and the stored creation response
"""
import json

import boto3
from botocore.exceptions import ClientError

from common import plain

class DynamoOrderRepository:
    def __init__(self, table_name, table=None):
        self.table_name = table_name
        self.table = table or boto3.resource("dynamodb").Table(table_name)
        self.client = self.table.meta.client

    def get_idempotency(self, key):
        item = self.table.get_item(Key={"pk": f"IDEMP#{key}"}, ConsistentRead=True).get("Item")
        if not item:
            return None
        return {
            "fingerprint": item["fingerprint"],
            "orderId": item["orderId"],
            "response": json.loads(item["response"]),
        }

    def create_order_atomic(self, order, key, fingerprint):
        """Write the order and its idempotency record in ONE transaction.

        Both puts are conditional on the key not existing, so concurrent
        identical submissions cannot both succeed. Returns False when another
        request already claimed the key (or order id).
        """
        order_item = {"pk": f"ORDER#{order['orderId']}", **order}
        idemp_item = {
            "pk": f"IDEMP#{key}",
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

    def get_order(self, order_id):
        item = self.table.get_item(Key={"pk": f"ORDER#{order_id}"}, ConsistentRead=True).get("Item")
        return self._strip(item)

    def cancel_order(self, order_id, now):
        """CREATED -> CANCELLED. Returns the order, or None if it does not exist."""
        try:
            item = self.table.update_item(
                Key={"pk": f"ORDER#{order_id}"},
                UpdateExpression="SET #s = :cancelled, updatedAt = :now",
                ConditionExpression="#s = :created",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":cancelled": "CANCELLED", ":created": "CREATED", ":now": now},
                ReturnValues="ALL_NEW",
            )["Attributes"]
            return self._strip(item)
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
            # Missing, or already cancelled: read the current state.
            return self.get_order(order_id)

    @staticmethod
    def _strip(item):
        if not item:
            return None
        item = plain(item)
        item.pop("pk", None)
        return item
