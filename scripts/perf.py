#!/usr/bin/env python3
"""Latency measurement for GET /products against the deployed API (stdlib only).

Cold start is measured separately: the Catalog function configuration is touched (forcing new
execution environments) and the very next request is timed.

Usage: python3 scripts/perf.py --api URL --catalog-function NAME [--region us-east-1]
"""
import argparse
import http.client
import statistics
import subprocess
import time
import urllib.parse


def timed_get(conn, path):
    t = time.perf_counter()
    conn.request("GET", path)
    r = conn.getresponse()
    r.read()
    return r.status, (time.perf_counter() - t) * 1000


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", required=True)
    ap.add_argument("--catalog-function", required=True)
    ap.add_argument("--region", default="us-east-1")
    a = ap.parse_args()
    host = urllib.parse.urlparse(a.api).netloc
    conn = http.client.HTTPSConnection(host, timeout=15)

    cfg = subprocess.run(["aws", "lambda", "get-function-configuration", "--function-name", a.catalog_function,
                          "--region", a.region, "--query", "[Runtime,Architectures[0],MemorySize]", "--output", "text"],
                         capture_output=True, text=True, check=True).stdout.split()
    print(f"date={time.strftime('%Y-%m-%dT%H:%M:%S%z')} api_region={a.region} runtime={cfg[0]} arch={cfg[1]} memory={cfg[2]}MB")
    print("client: sequential requests from the local machine over one keep-alive HTTPS connection\n")

    # Cold start: force new execution environments, then time the first request
    subprocess.run(["aws", "lambda", "update-function-configuration", "--function-name", a.catalog_function,
                    "--region", a.region, "--description", "perf-" + str(int(time.time()))], check=True, capture_output=True)
    subprocess.run(["aws", "lambda", "wait", "function-updated", "--function-name", a.catalog_function,
                    "--region", a.region], check=True)
    st, cold = timed_get(conn, "/products")
    print(f"cold start (first request after config change): status={st} {cold:.0f} ms")

    warm = [timed_get(conn, "/products") for _ in range(3)]
    print("warm-up (3, excluded from stats): " + ", ".join(f"{t:.0f}" for _, t in warm) + " ms")

    runs = [timed_get(conn, "/products") for _ in range(20)]
    times = sorted(t for _, t in runs)
    ok = all(s == 200 for s, _ in runs)
    p95 = times[int(round(0.95 * len(times))) - 1]   # nearest-rank
    print(f"\n20 sequential reads (all 200: {ok}):")
    print("  samples ms: " + ", ".join(f"{t:.0f}" for _, t in runs))
    print(f"  min={times[0]:.0f}  median={statistics.median(times):.0f}  mean={statistics.mean(times):.0f}  "
          f"p95={p95:.0f}  max={times[-1]:.0f} ms")


if __name__ == "__main__":
    main()
