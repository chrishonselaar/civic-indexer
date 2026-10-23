"""Budget gates: pre-flight check, spend tracking, BUDGET-STOP.

Degrade, never surprise-bill (decision-inventory.md §9): a tripped budget stops
cleanly, records BUDGET-STOP in the cost ledger, and leaves remaining work
deferred — it is never an error.
"""

import threading

from engine import db

EUR_PER_REQUEST = 0.0  # discovery/fetch requests are free; the gate guards volume anyway
EUR_PER_GB = 0.0


class BudgetStop(Exception):
    """Raised when the budget gate trips; callers stop cleanly and defer the rest."""


class Budget:
    def __init__(self, run_id: int, source_id: str, eur_cap: float):
        self.run_id = run_id
        self.source_id = source_id
        self.eur_cap = eur_cap
        self.eur_spent = 0.0
        self.counters: dict[str, float] = {}
        self._lock = threading.Lock()  # add() may run under a fetch thread pool

    def add(self, category: str, amount: float = 1, eur: float = 0.0) -> None:
        with self._lock:
            self.counters[category] = self.counters.get(category, 0) + amount
            self.eur_spent += eur
            tripped = self.eur_spent > self.eur_cap
        if tripped:
            self.record_stop(f"cap {self.eur_cap} EUR exceeded at {category}")
            raise BudgetStop(f"budget cap {self.eur_cap} EUR reached")

    def preflight(self, projected_eur: float, note: str) -> bool:
        """True if projected spend fits; records a BUDGET-STOP and returns False if not."""
        if projected_eur > self.eur_cap:
            self.record_stop(f"pre-flight: projected {projected_eur:.2f} EUR > cap ({note})")
            return False
        return True

    def record_stop(self, note: str) -> None:
        with db.tx() as cur:
            cur.execute(
                "INSERT INTO cost_ledger (run_id, source_id, category, amount, eur, note) "
                "VALUES (%s, %s, 'budget_stop', 0, %s, %s)",
                (self.run_id, self.source_id, self.eur_spent, note),
            )

    def flush(self) -> dict:
        """Persist per-category counters; return the report cost block."""
        with db.tx() as cur:
            for category, amount in self.counters.items():
                cur.execute(
                    "INSERT INTO cost_ledger (run_id, source_id, category, amount, eur) "
                    "VALUES (%s, %s, %s, %s, 0)",
                    (self.run_id, self.source_id, category, amount),
                )
        return {
            **{k: int(v) for k, v in self.counters.items()},
            "eur_spent": round(self.eur_spent, 4),
            "budget_remaining_eur": round(self.eur_cap - self.eur_spent, 4),
        }
