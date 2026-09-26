"""In-process EventStore benchmark: throughput, request latency and integrity by worker count."""

from __future__ import annotations

import base64
import argparse
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


def run_benchmark(workers: int, events: list[dict[str, Any]], store: EventStore,
                  batch_size: int = 1) -> dict[str, Any]:
    latencies: list[float] = []
    states: dict[str, int] = {}
    batches = [events[index:index + batch_size] for index in range(0, len(events), batch_size)]

    def process_one(batch: list[dict[str, Any]]) -> tuple[list[str], float]:
        t0 = time.perf_counter()
        results = ([store.ingest(batch[0])] if batch_size == 1 else store.ingest_many(batch))
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        return [result["state"] for result in results], elapsed_ms

    def collect(result: tuple[list[str], float]) -> None:
        outcome, latency = result
        for state in outcome:
            states[state] = states.get(state, 0) + 1
        latencies.append(latency)

    start_total = time.perf_counter()
    if workers == 1:
        for batch in batches:
            collect(process_one(batch))
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(process_one, batch) for batch in batches]
            for fut in as_completed(futures):
                collect(fut.result())

    total_time = time.perf_counter() - start_total
    latencies.sort()
    n = len(latencies)

    return {
        "workers": workers,
        "total_events": len(events),
        "requests": n,
        "batch_size": batch_size,
        "total_seconds": round(total_time, 3),
        "events_per_sec": round(len(events) / total_time, 1) if total_time > 0 else 0,
        "p50_ms": round(latencies[int(n * 0.50)], 2),
        "p95_ms": round(latencies[int(n * 0.95)], 2),
        "p99_ms": round(latencies[int(n * 0.99)], 2),
        "states": states,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure event ingestion without a throughput guarantee")
    parser.add_argument("--events", type=int, default=600)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--profile", action="store_true")
    args = parser.parse_args()
    if args.events < 10:
        parser.error("--events must be at least 10")
    if args.batch_size < 1:
        parser.error("--batch-size must be positive")
    if args.batch_size > 100:
        parser.error("--batch-size must not exceed the HTTP limit of 100")
    print("=================================================================")
    print(" ZERO DEFECT SPACE: LOAD & CONCURRENCY BENCHMARK (1 vs N WORKERS)")
    print("=================================================================")

    total_items = args.events
    dupe_ratio = 0.15
    print(f"Generating test dataset: {total_items} events ({int(dupe_ratio * 100)}% duplicates)...")
    dataset = generate_event_batch(total_items, duplicate_ratio=dupe_ratio)

    key_b64 = base64.b64encode(os.urandom(32)).decode()
    profiles = [({} if args.profile else None) for _ in range(3)]

    # Benchmark 1: Sequential (1 Worker)
    with tempfile.TemporaryDirectory() as tmp1:
        store1 = EventStore(os.path.join(tmp1, "bench1.db"), key_b64, timings=profiles[0])
        if profiles[0] is not None:
            profiles[0].clear()
        print("\n[Test 1] Running 1 Worker (Sequential baseline)...")
        res1 = run_benchmark(workers=1, events=dataset, store=store1, batch_size=args.batch_size)
        integrity1 = store1.verify_integrity()
        store1.close()

    # Benchmark 2: Concurrent (4 Workers)
    with tempfile.TemporaryDirectory() as tmp4:
        store4 = EventStore(os.path.join(tmp4, "bench4.db"), key_b64, timings=profiles[1])
        if profiles[1] is not None:
            profiles[1].clear()
        print("[Test 2] Running 4 Concurrent Workers (Multi-threaded pool)...")
        res4 = run_benchmark(workers=4, events=dataset, store=store4, batch_size=args.batch_size)
        integrity4 = store4.verify_integrity()
        store4.close()

    # Benchmark 3: High Concurrency (8 Workers)
    with tempfile.TemporaryDirectory() as tmp8:
        store8 = EventStore(os.path.join(tmp8, "bench8.db"), key_b64, timings=profiles[2])
        if profiles[2] is not None:
            profiles[2].clear()
        print("[Test 3] Running 8 Concurrent Workers...")
        res8 = run_benchmark(workers=8, events=dataset, store=store8, batch_size=args.batch_size)
        integrity8 = store8.verify_integrity()
        store8.close()

    # Summary Output
    print("\n-----------------------------------------------------------------")
    print(f"Request size: up to {args.batch_size} events; p50/p95/p99 are per request, throughput is per event.")
    print(f"{'Workers':<10} | {'Throughput':<14} | {'p50 (ms)':<10} | {'p95 (ms)':<10} | {'p99 (ms)':<10} | {'Integrity'}")
    print("-----------------------------------------------------------------")
    for r, integ in [(res1, integrity1), (res4, integrity4), (res8, integrity8)]:
        print(f"{r['workers']:<10} | {r['events_per_sec']:>7} evt/s | {r['p50_ms']:>8}ms | {r['p95_ms']:>8}ms | {r['p99_ms']:>8}ms | Valid: {integ['valid']} ({integ['checked_events']} records)")
    print("-----------------------------------------------------------------")
    print(f"Dataset applied: {res1['states'].get('applied', 0)}, duplicates deduplicated: {res1['states'].get('duplicate', 0)}")
    print(f"Requests per run: {res1['requests']} (batch size up to {args.batch_size})")
    if res1["requests"] < 100:
        print("Latency percentiles are exploratory: fewer than 100 requests per run.")
    print("Integrity verified for the measured datasets; throughput is environment-dependent.")
    print("SQLite serializes writes in this implementation; worker count is not a scalability guarantee.\n")
    if args.profile:
        print("Stage timings, wall-clock milliseconds summed across calls (SQLite excludes anchor phases):")
        for result, profile in zip((res1, res4, res8), profiles):
            parts = " ".join(f"{stage}={sum(profile.get(stage, [])):.1f}ms"
                             for stage in ("sqlite", "crypto", "anchor_verify", "anchor_write"))
            print(f"  workers={result['workers']}: {parts}")


if __name__ == "__main__":
    main()
