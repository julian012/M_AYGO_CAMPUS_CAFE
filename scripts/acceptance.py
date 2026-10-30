#!/usr/bin/env python3
"""Acceptance run against the deployed API. Standard library only; prints real results.

Usage: python3 scripts/acceptance.py --api https://xxxx.execute-api.us-east-1.amazonaws.com \
           [--orders-function NAME]   # also checks persistence across a configuration change
"""
import argparse
import json
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from urllib import request, error

results = []


def call(api, method, path, body=None, key=None, raw=None):
    headers = {}
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    if data is not None:
        headers["content-type"] = "application/json"
    if key is not None:
        headers["Idempotency-Key"] = key
    req = request.Request(api + path, data=data, method=method, headers=headers)
    try:
        with request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read() or b"null"), dict(r.headers)
    except error.HTTPError as e:
        payload = e.read()
        try:
            return e.code, json.loads(payload), dict(e.headers)
        except ValueError:
            return e.code, payload.decode(), dict(e.headers)


def check(name, ok, detail=""):
    results.append(ok)
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}".rstrip())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", required=True)
    ap.add_argument("--orders-function")
    ap.add_argument("--region", default="us-east-1")
    a = ap.parse_args()
    api = a.api.rstrip("/")
    print(f"API {api}  started {time.strftime('%Y-%m-%dT%H:%M:%S%z')}\n")

    st, body, _ = call(api, "GET", "/products")
    check("GET /products -> 200 with items", st == 200 and len(body["items"]) >= 1, f"({st}, {len(body['items'])} items)")
    catalog = {p["productId"]: p for p in body["items"]}
    avail = [p for p in catalog.values() if p["available"]]
    unavail = [p for p in catalog.values() if not p["available"]]
    coffee = avail[0]

    st, body, _ = call(api, "GET", f"/products/{coffee['productId']}")
    check("GET /products/{id} -> 200", st == 200 and body["productId"] == coffee["productId"], f"({st})")
    st, body, _ = call(api, "GET", "/products/does-not-exist")
    check("GET unknown product -> 404", st == 404, f"({st})")

    # Positive: create, totals computed by server, client price ignored
    key = "acc-" + uuid.uuid4().hex
    payload = {"items": [{"productId": coffee["productId"], "quantity": 2}], "totalCents": 1, "priceCents": 1}
    st, order, hdr = call(api, "POST", "/orders", payload, key)
    expected = coffee["priceCents"] * 2
    check("POST /orders -> 201", st == 201, f"({st})")
    check("Location header present", any(k.lower() == "location" for k in hdr), "")
    check("Server total ignores client-supplied price", order.get("totalCents") == expected,
          f"(got {order.get('totalCents')}, expected {expected})")
    oid = order["orderId"]

    st, again, _ = call(api, "POST", "/orders", payload, key)
    check("Replay same key+body -> 200 same orderId", st == 200 and again["orderId"] == oid, f"({st})")
    st, body, _ = call(api, "POST", "/orders", {"items": [{"productId": coffee["productId"], "quantity": 3}]}, key)
    check("Same key, different body -> 409", st == 409 and body.get("code") == "IDEMPOTENCY_KEY_CONFLICT", f"({st})")

    st, got, _ = call(api, "GET", f"/orders/{oid}")
    check("GET /orders/{id} -> 200 CREATED", st == 200 and got["status"] == "CREATED", f"({st})")
    st, body, _ = call(api, "GET", "/orders/nope")
    check("GET unknown order -> 404", st == 404, f"({st})")

    # Negative validation
    item = {"productId": coffee["productId"], "quantity": 1}
    negatives = [
        ("missing Idempotency-Key", dict(body={"items": [item]}, key=None), 400),
        ("empty items", dict(body={"items": []}, key="n-" + uuid.uuid4().hex), 400),
        ("quantity 0", dict(body={"items": [{**item, "quantity": 0}]}, key="n-" + uuid.uuid4().hex), 400),
        ("quantity 11", dict(body={"items": [{**item, "quantity": 11}]}, key="n-" + uuid.uuid4().hex), 400),
        ("quantity float", dict(body={"items": [{**item, "quantity": 1.5}]}, key="n-" + uuid.uuid4().hex), 400),
        ("quantity bool", dict(body={"items": [{**item, "quantity": True}]}, key="n-" + uuid.uuid4().hex), 400),
        ("duplicate product", dict(body={"items": [item, item]}, key="n-" + uuid.uuid4().hex), 400),
        ("unknown product", dict(body={"items": [{"productId": "zzz", "quantity": 1}]}, key="n-" + uuid.uuid4().hex), 400),
        ("invalid JSON", dict(raw=b"{bad", key="n-" + uuid.uuid4().hex), 400),
    ]
    if unavail:
        negatives.append(("unavailable product", dict(body={"items": [{"productId": unavail[0]["productId"], "quantity": 1}]},
                          key="n-" + uuid.uuid4().hex), 409))
    for name, kw, want in negatives:
        st, _, _ = call(api, "POST", "/orders", **kw)
        check(f"Negative: {name} -> {want}", st == want, f"({st})")

    # Cancel
    st, c1, _ = call(api, "POST", f"/orders/{oid}/cancel")
    check("Cancel -> 200 CANCELLED", st == 200 and c1["status"] == "CANCELLED", f"({st})")
    st, c2, _ = call(api, "POST", f"/orders/{oid}/cancel")
    check("Cancel again -> 200 CANCELLED (repeatable)", st == 200 and c2["status"] == "CANCELLED", f"({st})")
    st, _, _ = call(api, "POST", "/orders/nope/cancel")
    check("Cancel unknown -> 404", st == 404, f"({st})")

    # Reliability: 5 concurrent identical submissions -> one order
    ckey = "conc-" + uuid.uuid4().hex
    cbody = {"items": [{"productId": coffee["productId"], "quantity": 1}]}
    with ThreadPoolExecutor(5) as ex:
        outs = list(ex.map(lambda _: call(api, "POST", "/orders", cbody, ckey), range(5)))
    codes = sorted(o[0] for o in outs)
    ids = {o[1].get("orderId") for o in outs if o[0] in (200, 201)}
    print(f"       concurrent statuses: {codes}, orderIds: {sorted(ids)}")
    check("5 concurrent same key -> exactly one orderId, exactly one 201",
          len(ids) == 1 and codes.count(201) == 1 and all(c in (200, 201) for c in codes))

    # Persistence across a Lambda configuration change (new execution environments)
    if a.orders_function:
        before = call(api, "GET", f"/orders/{oid}")[1]
        subprocess.run(["aws", "lambda", "update-function-configuration", "--function-name", a.orders_function,
                        "--region", a.region, "--description", "acceptance-" + str(int(time.time()))],
                       check=True, capture_output=True)
        subprocess.run(["aws", "lambda", "wait", "function-updated", "--function-name", a.orders_function,
                        "--region", a.region], check=True)
        st, after, _ = call(api, "GET", f"/orders/{oid}")
        check("Order readable after configuration change", st == 200 and after == before, f"({st})")
        st, rep, _ = call(api, "POST", "/orders", payload, key)
        check("Replay after configuration change -> 200 same orderId", st == 200 and rep["orderId"] == oid, f"({st})")

    print(f"\n{sum(results)}/{len(results)} checks passed")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
