"""Load benchmark and stress generator for Zero Defect Space.
Compares single-worker throughput vs N concurrent workers.
Measures latency distribution (p50, p95, p99), checks for race conditions and data loss.
"""

from __future__ import annotations

import base64
import os
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from qc.core import EventStore


def generate_event_batch(count: int, duplicate_ratio: float = 0.1) -> list[dict[str, Any]]:
    batch = []
    num_unique = int(count * (1.0 - duplicate_ratio))
    for i in range(num_unique):
        event = {
            "schema_version": 1,
            "source_id": f"bench-source-{i % 5}",
            "event_id": f"evt-{i}",
            "item_id": f"ITEM-{i % 20:03d}",
            "event_type": "ItemReceived",
            "occurred_at": "2026-09-25T10:00:00+03:00",
            "payload": {"line": f"L{i % 2}", "batch": i},
        }
        batch.append(event)
    # Add duplicates of already created events
    num_dupes = count - num_unique
    for i in range(num_dupes):
        original = batch[i % num_unique]
        # Exact duplicate
        batch.append(dict(original))
    return batch


def run_benchmark(workers: int, events: list[dict[str, Any]], store: EventStore) -> dict[str, Any]:
    latencies: list[float] = []
    states: dict[str, int] = {}

    def process_one(ev: dict[str, Any]) -> tuple[str, float]:
        t0 = time.perf_counter()
        res = store.ingest(ev)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        return res["state"], elapsed_ms

    start_total = time.perf_counter()
    if workers == 1:
        for ev in events:
            st, lat = process_one(ev)
            states[st] = states.get(st, 0) + 1
            latencies.append(lat)
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(process_one, ev) for ev in events]
            for fut in as_completed(futures):
                st, lat = fut.result()
                states[st] = states.get(st, 0) + 1
                latencies.append(lat)

    total_time = time.perf_counter() - start_total
    latencies.sort()
    n = len(latencies)

    return {
        "workers": workers,
        "total_events": n,
        "total_seconds": round(total_time, 3),
        "events_per_sec": round(n / total_time, 1) if total_time > 0 else 0,
        "p50_ms": round(latencies[int(n * 0.50)], 2),
        "p95_ms": round(latencies[int(n * 0.95)], 2),
        "p99_ms": round(latencies[int(n * 0.99)], 2),
        "states": states,
    }


def main() -> None:
    print("=================================================================")
    print(" ZERO DEFECT SPACE: LOAD & CONCURRENCY BENCHMARK (1 vs N WORKERS)")
    print("=================================================================")

    total_items = 600
    dupe_ratio = 0.15
    print(f"Generating test dataset: {total_items} events ({int(dupe_ratio * 100)}% duplicates)...")
    dataset = generate_event_batch(total_items, duplicate_ratio=dupe_ratio)

    key_b64 = base64.b64encode(os.urandom(32)).decode()

    # Benchmark 1: Sequential (1 Worker)
    with tempfile.TemporaryDirectory() as tmp1:
        store1 = EventStore(os.path.join(tmp1, "bench1.db"), key_b64)
        print("\n[Test 1] Running 1 Worker (Sequential baseline)...")
        res1 = run_benchmark(workers=1, events=dataset, store=store1)
        integrity1 = store1.verify_integrity()
        store1.close()

    # Benchmark 2: Concurrent (4 Workers)
    with tempfile.TemporaryDirectory() as tmp4:
        store4 = EventStore(os.path.join(tmp4, "bench4.db"), key_b64)
        print("[Test 2] Running 4 Concurrent Workers (Multi-threaded pool)...")
        res4 = run_benchmark(workers=4, events=dataset, store=store4)
        integrity4 = store4.verify_integrity()
        store4.close()

    # Benchmark 3: High Concurrency (8 Workers)
    with tempfile.TemporaryDirectory() as tmp8:
        store8 = EventStore(os.path.join(tmp8, "bench8.db"), key_b64)
        print("[Test 3] Running 8 Concurrent Workers...")
        res8 = run_benchmark(workers=8, events=dataset, store=store8)
        integrity8 = store8.verify_integrity()
        store8.close()

    # Summary Output
    print("\n-----------------------------------------------------------------")
    print(f"{'Workers':<10} | {'Throughput':<14} | {'p50 (ms)':<10} | {'p95 (ms)':<10} | {'p99 (ms)':<10} | {'Integrity'}")
    print("-----------------------------------------------------------------")
    for r, integ in [(res1, integrity1), (res4, integrity4), (res8, integrity8)]:
        print(f"{r['workers']:<10} | {r['events_per_sec']:>7} evt/s | {r['p50_ms']:>8}ms | {r['p95_ms']:>8}ms | {r['p99_ms']:>8}ms | Valid: {integ['valid']} ({integ['checked_events']} records)")
    print("-----------------------------------------------------------------")
    print(f"Dataset applied: {res1['states'].get('applied', 0)}, duplicates deduplicated: {res1['states'].get('duplicate', 0)}")
    print("Integrity verified for the measured datasets; throughput is environment-dependent.")
    print("SQLite serializes writes in this implementation; worker count is not a scalability guarantee.\n")


if __name__ == "__main__":
    main()
