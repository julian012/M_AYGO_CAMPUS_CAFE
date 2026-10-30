"""Bounded HTTP client for Catalog's public API (through API Gateway)."""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from common import ApiError


class CatalogClient:
    """GET /products/{id} with a hard overall deadline.

    GET is safe to repeat, so one retry is allowed, but only while the
    overall deadline has not expired. A timeout never becomes an accepted order.
    """

    def __init__(self, base_url, deadline_seconds=3.0, attempt_timeout=1.5, retries=1):
        self.base_url = base_url.rstrip("/")
        self.deadline_seconds = deadline_seconds
        self.attempt_timeout = attempt_timeout
        self.retries = retries

    def get_product(self, product_id):
        """Return the product dict, or None if Catalog says 404."""
        deadline = time.monotonic() + self.deadline_seconds
        url = f"{self.base_url}/products/{urllib.parse.quote(product_id, safe='')}"
        last_error = "unknown"
        for _ in range(self.retries + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                req = urllib.request.Request(url, headers={"Accept": "application/json"})
                with urllib.request.urlopen(req, timeout=min(self.attempt_timeout, remaining)) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    return None
                last_error = f"http {exc.code}"
                if exc.code < 500:
                    break  # a 4xx will not improve on retry
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
                last_error = type(exc).__name__
        raise ApiError(503, "CATALOG_UNAVAILABLE", "Product catalog is unavailable. Please retry.")

    def get_products(self, product_ids):
        return {pid: self.get_product(pid) for pid in product_ids}
