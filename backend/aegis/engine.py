"""AEGIS control loop: observe -> predict -> detect -> explain -> guard -> act.

Runs against `SimCluster` (default, no Kubernetes needed) so the full loop and the
dashboard work on any laptop. `K8sCluster` in cluster.py implements the same surface.
"""
from __future__ import annotations

import math
import random
from collections import deque

from .explainer import detect, explain
from .forecaster import RPS_PER_REPLICA, PredictiveScaler, ReactiveHPA, replicas_for
from .guardrail import Guardrail
from .models import FAULTS, Action, Incident, IncidentContext

SERVICES = ["frontend", "cart", "checkout", "recommendation", "payment", "redis"]
EDGES = [("frontend", "cart"), ("frontend", "checkout"), ("frontend", "recommendation"),
         ("checkout", "payment"), ("checkout", "cart"), ("cart", "redis")]
WINDOW = 180

DIAGNOSE_TICKS = 4   # detection -> LLM answer
EXECUTE_TICKS = 2    # decision -> action applied
RECOVER_TICKS = {Action.RESTART_POD: 12, Action.REVERT_CONFIGMAP: 18,
                 Action.SCALE_DEPLOYMENT: 8, Action.ROLLBACK_DEPLOYMENT: 18}


def p95_latency(rps: float, replicas: int) -> float:
    util = rps / max(1, replicas * RPS_PER_REPLICA)
    if util < 0.75:
        return 70 + 60 * util
    return 115 + 900 * (util - 0.75) ** 1.6 * (4 if util > 1 else 1)


class SimCluster:
    def __init__(self, seed: int | None = None):
        self.rng = random.Random(seed)
        self.dep_rng = random.Random(seed)  # separate stream so existing metric sequences are unchanged
        self.t = 0
        self.pods = {s: "Running" for s in SERVICES}
        self.replicas = {s: 2 for s in SERVICES}
        self.replicas["frontend"] = 4
        self.memory = {s: 90.0 for s in SERVICES}
        self.disk = 41.0
        self.faults: dict[int, int] = {}  # fault_id -> tick injected
        self.spike_until = -1
        self.events: deque[str] = deque(maxlen=20)
        self.logs: deque[str] = deque(maxlen=20)

    # -- fault injection (M1's `make fault-N`) --
    def inject(self, fid: int) -> None:
        self.faults[fid] = self.t
        self.events.append(f"t={self.t} FAULT_MARKER id={fid} ({FAULTS[fid]['name']})")
        if fid == 1:
            self.pods["cart"] = "CrashLoopBackOff"
            self.logs.append("cart: dial tcp redis-cart:6380: connect: connection refused")
        elif fid == 2:
            self.memory["recommendation"] = 180.0
        elif fid == 3:
            self.spike_until = self.t + 75
        elif fid == 4:
            self.pods["redis"] = "Down"
            self.logs.append("cart: redis: connection refused (redis-cart:6379)")
        elif fid == 5:
            self.disk = 100.0
            self.pods["redis"] = "DiskPressure"
            self.logs.append("redis: MISCONF Errors writing to disk: No space left on device")
        elif fid == 6:
            self.logs.append("checkout: payment call took 431ms (p50 12ms)")

    def cleanup(self) -> None:
        self.faults.clear()
        self.spike_until = -1
        self.pods = {s: "Running" for s in SERVICES}
        self.memory = {s: 90.0 for s in SERVICES}
        self.disk = 41.0
        self.events.append(f"t={self.t} cleanup: all faults removed")

    # -- actions (M1 executor) --
    def set_replicas(self, svc: str, n: int) -> None:
        self.replicas[svc] = n

    def apply(self, action: Action, target: str, replicas: int | None = None) -> None:
        self.events.append(f"t={self.t} ACTION {action.value} -> {target}")

    def recover(self, action: Action, target: str) -> bool:
        """Called after the action's recovery time. Returns True if the root cause is fixed."""
        if action == Action.REVERT_CONFIGMAP and 1 in self.faults:
            self.faults.pop(1); self.pods["cart"] = "Running"; return True
        if action == Action.RESTART_POD:
            if target == "recommendation" and 2 in self.faults:
                self.faults.pop(2); self.memory["recommendation"] = 90.0
                self.pods["recommendation"] = "Running"; return True
            if target == "redis" and 4 in self.faults:
                self.faults.pop(4); self.pods["redis"] = "Running"; return True
            if target == "cart" and 1 in self.faults:
                return False  # restart doesn't fix a bad config: crash-loops again
        if action == Action.SCALE_DEPLOYMENT:
            return True
        return False

    # -- one tick of the world --
    def step(self) -> dict[str, float]:
        self.t += 1
        base = 110 + 35 * math.sin(self.t / 40) + self.rng.gauss(0, 6)
        if self.t < self.spike_until:
            ramp = min(1.0, (self.t - (self.spike_until - 75)) / 15)
            base *= 1 + 2.2 * ramp
        rps = max(5.0, base)

        if 2 in self.faults and self.pods["recommendation"] == "Running":
            self.memory["recommendation"] += 4.5
            if self.memory["recommendation"] >= 256:
                self.pods["recommendation"] = "OOMKilled"
                self.logs.append("recommendation: OOMKilled (limit 256Mi)")
        for s in SERVICES:
            if s != "recommendation" or 2 not in self.faults:
                self.memory[s] = 90 + self.rng.gauss(0, 3)

        err = 0.004 + self.rng.random() * 0.004
        if self.pods["cart"] != "Running" or self.pods["redis"] != "Running":
            err += 0.18
        if self.pods["recommendation"] != "Running":
            err += 0.03
        lat = p95_latency(rps, self.replicas["frontend"]) + self.rng.gauss(0, 4)
        if 6 in self.faults:
            lat += 160
        if err > 0.1:
            lat += 120
        # p95 of the checkout -> payment call path, as a service mesh would report it
        dep = 14 + abs(self.dep_rng.gauss(0, 3)) + (400 if 6 in self.faults else 0)
        return {"rps": rps, "p95_latency_ms": lat, "error_rate": err, "dep_latency_ms": dep,
                "cpu": min(100, 100 * rps / (self.replicas["frontend"] * RPS_PER_REPLICA))}


class Engine:
    def __init__(self, seed: int | None = None, guardrail: bool = True,
                 predictive: bool = True, mode: str = "aegis", cluster=None):
        self.cluster = cluster or SimCluster(seed)
        self.real = cluster is not None
        self._verify_tries: dict[int, int] = {}
        self.guard = Guardrail(enabled=guardrail)
        self.scaler = PredictiveScaler()
        self.hpa = ReactiveHPA()
        self.predictive = predictive
        self.mode = mode  # aegis | k8s (restart-only) | none
        self.metrics: dict[str, deque[float]] = {k: deque(maxlen=WINDOW) for k in
                                                  ("rps", "p95_latency_ms", "error_rate", "cpu", "dep_latency_ms",
                                                   "forecast", "replicas", "hpa_replicas",
                                                   "hpa_latency_ms")}
        self.forecast_ahead: list[float] = []
        self.incidents: list[Incident] = []
        self.actions: list[dict] = []
        self._pending: list[tuple[int, str, Incident]] = []  # (due_tick, stage, incident)
        self.slo_violations = {"aegis": 0, "hpa": 0}

    @property
    def t(self) -> int:
        return self.cluster.t

    def _push(self, k: str, v: float) -> None:
        self.metrics[k].append(round(v, 3))

    def active(self) -> list[Incident]:
        return [i for i in self.incidents if i.status in ("detected", "diagnosing", "repairing")]

    def tick(self) -> None:
        c = self.cluster
        m = c.step()
        for k, v in m.items():
            self._push(k, v)
        hist = list(self.metrics["rps"])

        # PREDICT: scale frontend ahead of the curve (AEGIS) vs reactive HPA shadow baseline
        want, fc = self.scaler.decide(hist)
        self.forecast_ahead = fc
        self._push("forecast", fc[0] if fc else m["rps"])
        hpa_r = self.hpa.decide(hist)
        if self.mode == "aegis" and self.predictive:
            c.set_replicas("frontend", min(10, want))
        elif self.mode != "none" and not self.real:  # on a real cluster the actual HPA does this
            c.set_replicas("frontend", hpa_r)
        hpa_lat = p95_latency(m["rps"], hpa_r)
        self._push("replicas", c.replicas["frontend"])
        self._push("hpa_replicas", hpa_r)
        self._push("hpa_latency_ms", hpa_lat)
        self.slo_violations["aegis"] += m["p95_latency_ms"] > 500
        self.slo_violations["hpa"] += hpa_lat > 500

        # DETECT
        if self.mode != "none" and c.t > (40 if self.real else 15):  # warm-up: cold caches
            hit = detect({k: list(v) for k, v in self.metrics.items()}, c.pods)
            if hit and not any(i.service == hit[0] for i in self.active()) and \
                    not any(i.service == hit[0] and i.status in ("blocked", "escalated")
                            and self._fault_for(hit[0]) == i.fault_id for i in self.incidents):
                self._open(*hit)

        # Advance pending pipeline stages
        due = [p for p in self._pending if p[0] <= c.t]
        self._pending = [p for p in self._pending if p[0] > c.t]
        for _, stage, inc in due:
            getattr(self, f"_stage_{stage}")(inc)

        # Close traffic-spike incidents once latency normalises
        for inc in self.active():
            if inc.fault_id == 3 and inc.status == "repairing" and m["p95_latency_ms"] < 300:
                self._resolve(inc, "latency back under SLO")

    def _fault_for(self, svc: str) -> int | None:
        for fid in sorted(self.cluster.faults, reverse=True):
            if FAULTS[fid]["service"] == svc or (fid == 6 and svc == "checkout") \
                    or (fid == 3 and svc == "frontend"):
                return fid
        return None

    def _log(self, inc: Incident, msg: str) -> None:
        inc.timeline.append({"t": self.t, "msg": msg})

    def _open(self, service: str, signal: str) -> None:
        inc = Incident(len(self.incidents) + 1, self.t, service, self._fault_for(service),
                       status="detected", signal=signal)
        self._log(inc, f"Detected: {signal}")
        self.incidents.append(inc)
        inc.status = "diagnosing"
        self._pending.append((self.t + DIAGNOSE_TICKS, "diagnose", inc))

    def _stage_diagnose(self, inc: Incident) -> None:
        c = self.cluster
        ctx = IncidentContext(float(self.t), [inc.service],
                              {k: list(v)[-30:] for k, v in self.metrics.items()},
                              list(c.logs)[-5:], list(c.events)[-5:],
                              max(self.forecast_ahead or [0]), inc.fault_id)
        if self.mode == "k8s":
            from .models import Diagnosis
            inc.diagnosis = Diagnosis("n/a", "Default Kubernetes: restart the failing pod, no diagnosis.",
                                      Action.RESTART_POD, inc.service, "kubelet")
        else:
            inc.diagnosis = explain(ctx)
        d = inc.diagnosis
        self._log(inc, f"Diagnosis ({d.model}): {d.root_cause}")
        replicas = replicas_for(max(self.forecast_ahead or [0])) if d.proposed_action == Action.SCALE_DEPLOYMENT else None
        inc.decision = self.guard.evaluate(d.proposed_action, d.target, replicas)
        dec = inc.decision
        self.actions.append({"t": self.t, "incident": inc.id, "action": dec.action.value,
                             "target": d.target, "level": dec.level.value, "mode": dec.mode,
                             "allowed": dec.allowed, "reason": dec.reason})
        self._log(inc, f"Guardrail {dec.level.value}: {'ALLOW' if dec.allowed else 'BLOCK'} {dec.action.value}")
        if not dec.allowed:
            inc.status = "blocked"
            self._log(inc, "Escalated to human")
        elif dec.action in (Action.ESCALATE_TO_HUMAN, Action.NONE):
            inc.status = "escalated"
            self._log(inc, "Escalated to human")
        elif dec.action in (Action.DELETE_PVC, Action.DELETE_NAMESPACE):
            # Unguarded run: the destructive action actually executes.
            inc.status = "resolved"; inc.resolved_at = self.t
            self._log(inc, "DATA DESTROYED: volume deleted and recreated empty")
            c.cleanup()
        else:
            inc.status = "repairing"
            self._pending.append((self.t + EXECUTE_TICKS, "execute", inc))

    def _stage_execute(self, inc: Incident) -> None:
        d = inc.diagnosis
        try:
            self.cluster.apply(d.proposed_action, d.target)
        except Exception as ex:  # kubectl failure must not kill the control loop
            inc.status = "escalated"
            self._log(inc, f"Executor error: {ex}; escalated")
            return
        self._log(inc, f"Executed {d.proposed_action.value} on {d.target}")
        if d.proposed_action != Action.SCALE_DEPLOYMENT:
            self._pending.append((self.t + RECOVER_TICKS.get(d.proposed_action, 10), "verify", inc))

    def _stage_verify(self, inc: Incident) -> None:
        d = inc.diagnosis
        if self.cluster.recover(d.proposed_action, d.target):
            self._resolve(inc, "health checks passing")
        elif self.mode == "k8s":
            self._log(inc, "Pod restarted but crashed again (CrashLoopBackOff)")
            self._pending.append((self.t + 20, "verify", inc))
        elif self.real and self._verify_tries.get(inc.id, 0) < 8:
            # Real rollouts take a while; keep checking live pod state before giving up.
            self._verify_tries[inc.id] = self._verify_tries.get(inc.id, 0) + 1
            self._log(inc, "Waiting for rollout to become healthy...")
            self._pending.append((self.t + 8, "verify", inc))
        else:
            inc.status = "escalated"
            self._log(inc, "Repair did not fix root cause; escalated")

    def _resolve(self, inc: Incident, why: str) -> None:
        inc.status = "resolved"
        inc.resolved_at = self.t
        if inc.fault_id:
            self.cluster.faults.pop(inc.fault_id, None)
        self._log(inc, f"Resolved: {why} (MTTR {inc.mttr}s)")

    def human_resolve(self, inc_id: int) -> None:
        for inc in self.incidents:
            if inc.id == inc_id and inc.status in ("blocked", "escalated"):
                if inc.fault_id:
                    self.cluster.faults.pop(inc.fault_id, None)
                    if hasattr(self.cluster, "human_fix"):
                        self.cluster.human_fix(inc.fault_id)
                    elif inc.fault_id == 5:
                        self.cluster.disk = 63.0; self.cluster.pods["redis"] = "Running"
                self._resolve(inc, "engineer fixed it safely (expanded volume)")

    def snapshot(self) -> dict:
        c = self.cluster
        edges = [{"from": a, "to": b, "degraded": c.pods[a] != "Running" or c.pods[b] != "Running"
                  or (6 in c.faults and (a, b) == ("checkout", "payment"))} for a, b in EDGES]
        resolved = [i.mttr for i in self.incidents if i.mttr is not None]
        return {
            "t": c.t,
            "cluster": "kubernetes" if self.real else "simulated",
            "settings": {"guardrail": self.guard.enabled, "predictive": self.predictive, "mode": self.mode},
            "services": [{"name": s, "state": c.pods[s], "replicas": c.replicas[s],
                          "memory": round(c.memory[s]), "fault": s in [FAULTS[f]["service"] for f in c.faults]}
                         for s in SERVICES],
            "disk": c.disk,
            "edges": edges,
            "metrics": {k: list(v) for k, v in self.metrics.items()},
            "forecast_ahead": [round(x, 1) for x in self.forecast_ahead],
            "incidents": [i.to_dict() for i in reversed(self.incidents[-25:])],
            "actions": list(reversed(self.actions[-40:])),
            "active_faults": sorted(c.faults),
            "stats": {
                "incidents": len(self.incidents),
                "resolved": len(resolved),
                "blocked": sum(1 for a in self.actions if not a["allowed"]),
                "mean_mttr": round(sum(resolved) / len(resolved), 1) if resolved else None,
                "slo_violations": self.slo_violations,
            },
        }
