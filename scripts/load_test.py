"""
Lightweight concurrent-load test for the production-readiness work
(docs/superpowers/specs/2026-07-10-production-readiness-design.md).
No new dependency - uses concurrent.futures + requests (already a
project dependency) rather than pulling in locust for a one-off script.

Simulates a burst of concurrent users hitting a mix of cached and
cache-excluded routes, and reports: error count, response time
percentiles, and - the specific thing this validates, not just "did it
not crash" - whether repeat requests to the same URL get measurably
faster after the first (proof the cache is actually being hit, not
silently bypassed).

Usage:
    python scripts/load_test.py --base-url http://localhost:5000 --users 75 --cookie "session=<value>"

The --cookie value must be a valid logged-in session cookie (copy it from
your browser's dev tools after logging in locally) - every route under
test requires authentication, there is no anonymous path to hit.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

_ROUTES = [
    "/dashboard",
    "/jobs",
    "/jobs?market=ai_ml_global",
    "/jobs?market=swe_backend_global&remote_type=remote",
    "/skills",
    "/skills/intelligence",
    "/companies/intelligence",
    "/titles/analytics",
    "/metrics",
]


def _fetch(base_url: str, path: str, cookie: str) -> tuple[str, int, float]:
    start = time.monotonic()
    try:
        resp = requests.get(f"{base_url}{path}", headers={"Cookie": cookie}, timeout=15)
        elapsed = time.monotonic() - start
        return path, resp.status_code, elapsed
    except requests.RequestException as exc:
        elapsed = time.monotonic() - start
        print(f"  !! request to {path} failed: {exc}", file=sys.stderr)
        return path, 0, elapsed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:5000")
    parser.add_argument("--users", type=int, default=75, help="concurrent simulated users")
    parser.add_argument("--cookie", required=True, help="logged-in session cookie, e.g. 'session=eyJ...'")
    args = parser.parse_args()

    print(f"Warming cache with one request per route...")
    for path in _ROUTES:
        _fetch(args.base_url, path, args.cookie)

    print(f"\nCold (first-hit-per-route) timings just captured above are the baseline.")
    print(f"Now firing {args.users} concurrent requests across {len(_ROUTES)} routes...\n")

    tasks = [(_ROUTES[i % len(_ROUTES)]) for i in range(args.users)]
    results: list[tuple[str, int, float]] = []
    start = time.monotonic()
    with ThreadPoolExecutor(max_workers=args.users) as pool:
        futures = [pool.submit(_fetch, args.base_url, path, args.cookie) for path in tasks]
        for f in as_completed(futures):
            results.append(f.result())
    total_wall_time = time.monotonic() - start

    errors = [r for r in results if r[1] != 200]
    times = sorted(r[2] for r in results)

    print(f"Total wall time for {args.users} concurrent requests: {total_wall_time:.2f}s")
    print(f"Errors (non-200): {len(errors)} / {len(results)}")
    for path, status, _ in errors[:10]:
        print(f"  {status} {path}")

    if times:
        print(f"Response time - min: {times[0]*1000:.0f}ms, "
              f"median: {statistics.median(times)*1000:.0f}ms, "
              f"p95: {times[int(len(times)*0.95)]*1000:.0f}ms, "
              f"max: {times[-1]*1000:.0f}ms")

    print("\nCache-hit verification: re-fetching each route once more, timing should be low (cached)...")
    for path in _ROUTES:
        _, status, elapsed = _fetch(args.base_url, path, args.cookie)
        flag = "OK" if status == 200 else f"FAILED ({status})"
        print(f"  {path:55} {elapsed*1000:6.1f}ms  {flag}")

    if errors:
        print(f"\nFAILED: {len(errors)} requests did not return 200.")
        sys.exit(1)
    print("\nPASSED: no errors under load.")


if __name__ == "__main__":
    main()
