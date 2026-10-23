"""Thread-safety of the fetch-phase accounting primitives.

The fetch loop parallelises `_process_one` under a ThreadPoolExecutor when a source
declares `fetch.politeness.concurrency > 1` (e.g. a bulk file repository). The network
fetch and each item's independent DB transaction run lock-free; only the shared
in-memory counters are guarded. `dict[k] += 1` and `obj.attr += x` are NOT atomic under
the GIL, so these guards are load-bearing — regress them and run reports silently
undercount. These tests hammer the guards from many threads and assert exact totals.
"""

import threading
from concurrent.futures import ThreadPoolExecutor

from engine.budget import Budget
from engine.process import _bump

WORKERS = 16
PER_WORKER = 500


def test_bump_counts_are_exact_under_concurrency():
    lock = threading.Lock()
    outcomes = {"fetched": 0, "extracted_ok": 0, "deferred": {}, "failed": 0}

    def worker():
        for _ in range(PER_WORKER):
            _bump(lock, outcomes, "fetched")
            _bump(lock, outcomes, "extracted_ok")
            _bump(lock, outcomes, "deferred", "ocr_tail")

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for fut in [pool.submit(worker) for _ in range(WORKERS)]:
            fut.result()

    total = WORKERS * PER_WORKER
    assert outcomes["fetched"] == total
    assert outcomes["extracted_ok"] == total
    assert outcomes["deferred"]["ocr_tail"] == total
    assert outcomes["failed"] == 0  # never bumped


def test_budget_add_is_exact_under_concurrency():
    # eur_cap high enough never to trip; record_stop hits the DB and is not under test.
    budget = Budget(run_id=0, source_id="t", eur_cap=1e18)

    def worker():
        for _ in range(PER_WORKER):
            budget.add("fetch_requests", 1)
            budget.add("bytes_downloaded", 10, eur=0.001)

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for fut in [pool.submit(worker) for _ in range(WORKERS)]:
            fut.result()

    total = WORKERS * PER_WORKER
    assert budget.counters["fetch_requests"] == total
    assert budget.counters["bytes_downloaded"] == total * 10
    assert abs(budget.eur_spent - total * 0.001) < 1e-6
