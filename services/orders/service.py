"""Order use cases. Depends on a repository and a catalog client (injected)."""
import uuid
from datetime import datetime, timezone

import domain
from common import ApiError


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class OrderService:
    def __init__(self, repo, catalog, clock=utc_now, id_factory=lambda: str(uuid.uuid4())):
        self.repo = repo
        self.catalog = catalog
        self.clock = clock
        self.id_factory = id_factory

    def create_order(self, key, items):
        """Returns (status, order, headers)."""
        fp = domain.fingerprint(items)

        # Check for a previous submission BEFORE contacting Catalog.
        existing = self.repo.get_idempotency(key)
        if existing:
            return self._replay(existing, fp)

        products = {}
        for item in items:
            product = self.catalog.get_product(item["productId"])
            if product is None:
                raise ApiError(400, "PRODUCT_NOT_FOUND", f"Unknown product: {item['productId']}.")
            if not product.get("available"):
                raise ApiError(409, "PRODUCT_UNAVAILABLE", "This product cannot be ordered.")
            products[item["productId"]] = product

        order = domain.build_order(self.id_factory(), items, products, self.clock())
        if self.repo.create_order_atomic(order, key, fp):
            return 201, order, {"Location": f"/orders/{order['orderId']}"}

        # Lost the race: another request claimed this key. Resolve like a replay.
        existing = self.repo.get_idempotency(key)
        if existing:
            return self._replay(existing, fp)
        raise ApiError(503, "TEMPORARY_FAILURE", "Could not store the order. Please retry.")

    @staticmethod
    def _replay(existing, fp):
        if existing["fingerprint"] != fp:
            raise ApiError(409, "IDEMPOTENCY_KEY_CONFLICT", "This Idempotency-Key was used with a different request.")
        return 200, existing["response"], {}

    def get_order(self, order_id):
        order = self.repo.get_order(order_id)
        if order is None:
            raise ApiError(404, "ORDER_NOT_FOUND", "Order not found.")
        return order

    def cancel_order(self, order_id):
        order = self.repo.cancel_order(order_id, self.clock())
        if order is None:
            raise ApiError(404, "ORDER_NOT_FOUND", "Order not found.")
        return order
