"""
Simple load generator — no extra dependencies (stdlib only).

Use this to (a) generate traffic so your Prometheus/Grafana dashboards light up,
and (b) push enough CPU to watch autoscaling kick in. Operating a service means
seeing it under load, not just hitting it once with curl.

Usage:
    python scripts/load_test.py http://<your-api-url> --requests 500 --concurrency 10
"""

import argparse
import json
import random
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

CONTRACTS = ["month-to-month", "one-year", "two-year"]
PAYMENTS = ["electronic-check", "mailed-check", "bank-transfer", "credit-card"]
INTERNET = ["dsl", "fiber", "none"]


def random_customer() -> dict:
    tenure = random.randint(0, 72)
    monthly = round(random.uniform(20, 130), 2)
    return {
        "tenure_months": tenure,
        "monthly_charges": monthly,
        "total_charges": round(monthly * tenure * random.uniform(0.8, 1.1), 2),
        "num_support_tickets": random.randint(0, 8),
        "contract_type": random.choice(CONTRACTS),
        "payment_method": random.choice(PAYMENTS),
        "internet_service": random.choice(INTERNET),
    }


def one_request(base_url: str) -> tuple[int, float]:
    payload = json.dumps(random_customer()).encode()
    req = urllib.request.Request(
        f"{base_url}/predict", data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, time.perf_counter() - start
    except Exception:
        return 0, time.perf_counter() - start


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("base_url", help="Base URL, e.g. http://my-alb-dns")
    parser.add_argument("--requests", type=int, default=200)
    parser.add_argument("--concurrency", type=int, default=10)
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    print(f"Sending {args.requests} requests at concurrency {args.concurrency} -> {base}")

    latencies, statuses = [], []
    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        for status, latency in pool.map(lambda _: one_request(base), range(args.requests)):
            statuses.append(status)
            latencies.append(latency)
    elapsed = time.perf_counter() - start

    ok = sum(1 for s in statuses if s == 200)
    latencies.sort()
    p50 = latencies[len(latencies) // 2]
    p95 = latencies[int(len(latencies) * 0.95)]

    print(f"\nDone in {elapsed:.2f}s")
    print(f"Success: {ok}/{args.requests}  ({ok / args.requests * 100:.1f}%)")
    print(f"Throughput: {args.requests / elapsed:.1f} req/s")
    print(f"Latency  p50: {p50 * 1000:.1f} ms   p95: {p95 * 1000:.1f} ms")


if __name__ == "__main__":
    main()
