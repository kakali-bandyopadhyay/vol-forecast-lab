"""Measure API search-cache warm misses and hits; writes a post-hoc latency diagnostic."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT = REPO_ROOT / "results" / "text" / "10c_cache_latency.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--n", type=int, default=20, help="number of distinct query strings (minimum 20)")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    return parser


def _summary(samples: list[float]) -> dict[str, float]:
    import numpy as np

    return {"p50_ms": float(np.percentile(samples, 50)), "p95_ms": float(np.percentile(samples, 95))}


def measure(base_url: str, n: int, output: Path) -> dict:
    if n < 20:
        raise ValueError("--n must be at least 20")
    queries = [f"cache latency diagnostic unique query {i}" for i in range(n)]
    endpoint = base_url.rstrip("/") + "/search"

    def call(query: str) -> float:
        started = time.perf_counter()
        response = requests.get(endpoint, params={"q": query}, timeout=30)
        response.raise_for_status()
        return (time.perf_counter() - started) * 1000

    call("cache latency diagnostic warm-up")

    def paced_group() -> list[float]:
        samples = []
        for query in queries:
            # Stay under the API's five-token-per-second refill after its ten-token burst.
            time.sleep(0.21)
            samples.append(call(query))
        return samples

    misses = paced_group()
    hits = paced_group()
    result = {"diagnostic": "POST-HOC CACHE LATENCY", "n_queries": n,
              "warm_miss": _summary(misses), "hit": _summary(hits)}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    args = build_parser().parse_args()
    measure(args.base_url, args.n, args.output)
