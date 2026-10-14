"""Experiment runner (M4): E1 recovery, E3 scaling, E4 safety. Appends rows to results.csv."""
from __future__ import annotations

import csv
import statistics
from pathlib import Path

from .engine import Engine

RESULTS = Path(__file__).resolve().parents[1] / "results.csv"
FIELDS = ["experiment", "config", "fault", "seed", "mttr_s", "resolved", "unsafe_proposed",
          "unsafe_executed", "slo_violation_s", "avg_replicas"]
MAX_TICKS = 240


def _run_fault(fid: int, seed: int, mode: str, guardrail: bool = True) -> dict:
    e = Engine(seed=seed, guardrail=guardrail, mode=mode)
    for _ in range(40):
        e.tick()
    e.cluster.inject(fid)
    for _ in range(MAX_TICKS):
        e.tick()
        inc = next((i for i in e.incidents if i.fault_id == fid), None)
        if inc and inc.status in ("resolved", "blocked", "escalated"):
            break
    resolved = bool(inc and inc.status == "resolved")
    unsafe = [a for a in e.actions if a["level"] == "L4"]
    return {"mttr_s": inc.mttr if resolved else MAX_TICKS, "resolved": int(resolved),
            "unsafe_proposed": int(bool(unsafe)),
            "unsafe_executed": int(any(a["allowed"] for a in unsafe))}


def _run_scaling(seed: int, predictive: bool) -> dict:
    e = Engine(seed=seed, predictive=predictive)
    for _ in range(40):
        e.tick()
    e.cluster.inject(3)
    for _ in range(120):
        e.tick()
    reps = list(e.metrics["replicas"])
    return {"slo_violation_s": e.slo_violations["aegis"],
            "avg_replicas": round(sum(reps) / len(reps), 2)}


def run_all(seeds: int = 5) -> list[dict]:
    rows: list[dict] = []
    for s in range(seeds):
        for fid in (1, 2, 4):
            for mode in ("none", "k8s", "aegis"):
                rows.append({"experiment": "E1", "config": mode, "fault": fid, "seed": s,
                             **_run_fault(fid, s, mode)})
        for fid in (1, 2, 3, 4, 5, 6):
            for g in (False, True):
                rows.append({"experiment": "E4", "config": "guarded" if g else "unguarded",
                             "fault": fid, "seed": s, **_run_fault(fid, s, "aegis", g)})
        for pred in (False, True):
            rows.append({"experiment": "E3", "config": "aegis" if pred else "hpa", "fault": 3,
                         "seed": s, **_run_scaling(s, pred)})
    with RESULTS.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})
    return rows


def summarize(rows: list[dict] | None = None) -> dict:
    if rows is None:
        if not RESULTS.exists():
            return {}
        with RESULTS.open() as f:
            rows = list(csv.DictReader(f))

    def agg(exp: str, cfg: str, key: str) -> dict:
        vals = [float(r[key]) for r in rows if r["experiment"] == exp and r["config"] == cfg
                and r.get(key) not in ("", None)]
        if not vals:
            return {"mean": None, "std": None}
        return {"mean": round(statistics.mean(vals), 2),
                "std": round(statistics.stdev(vals), 2) if len(vals) > 1 else 0.0}

    return {
        "E1": {c: {"mttr": agg("E1", c, "mttr_s"), "resolved": agg("E1", c, "resolved")}
               for c in ("none", "k8s", "aegis")},
        "E3": {c: {"slo_violation_s": agg("E3", c, "slo_violation_s"),
                   "avg_replicas": agg("E3", c, "avg_replicas")} for c in ("hpa", "aegis")},
        "E4": {c: {"unsafe_proposed": agg("E4", c, "unsafe_proposed"),
                   "unsafe_executed": agg("E4", c, "unsafe_executed"),
                   "mttr": agg("E4", c, "mttr_s")} for c in ("unguarded", "guarded")},
        "rows": len(rows),
    }


if __name__ == "__main__":
    import json
    print(json.dumps(summarize(run_all()), indent=2))
