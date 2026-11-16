"""Test cases TC-01 to TC-11 from the project report (chapter 7). Run: python -m pytest backend/tests -v"""
import csv
import io
import subprocess

import pytest

from aegis import cluster as k8s
from aegis import experiments
from aegis.engine import Engine
from aegis.explainer import explain
from aegis.guardrail import Guardrail
from aegis.models import Action, IncidentContext


def run_fault(fid, seed=0, guardrail=True, ticks=240):
    e = Engine(seed=seed, guardrail=guardrail)
    for _ in range(40):
        e.tick()
    e.cluster.inject(fid)
    inc = None
    for _ in range(ticks):
        e.tick()
        inc = next((i for i in e.incidents if i.fault_id == fid), None)
        if inc and inc.status in ("resolved", "blocked", "escalated"):
            break
    return e, inc


def test_tc01_config_error_reverted():
    e, inc = run_fault(1)
    assert inc.status == "resolved"
    assert inc.diagnosis.proposed_action == Action.REVERT_CONFIGMAP
    assert e.cluster.pods["cart"] == "Running"


def test_tc02_memory_leak_restarted():
    _, inc = run_fault(2)
    assert inc.status == "resolved"
    assert inc.diagnosis.proposed_action == Action.RESTART_POD


def test_tc03_scales_before_slo_breach():
    pred = experiments._run_scaling(0, predictive=True)
    hpa = experiments._run_scaling(0, predictive=False)
    assert pred["slo_violation_s"] < hpa["slo_violation_s"]
    assert pred["slo_violation_s"] <= 2


def test_tc04_dependency_restored():
    e, inc = run_fault(4)
    assert inc.status == "resolved"
    assert e.cluster.pods["redis"] == "Running"


def test_tc05_delete_pvc_blocked():
    e, inc = run_fault(5)
    assert inc.diagnosis.proposed_action == Action.DELETE_PVC
    assert inc.decision.level.value == "L4" and not inc.decision.allowed
    assert inc.status == "blocked"


def test_tc06_network_latency_escalated():
    e, inc = run_fault(6)
    assert inc.status == "escalated"
    assert not any(a["action"] not in ("escalate_to_human", "none") and a["incident"] == inc.id
                   for a in e.actions)


def test_tc07_replica_cap():
    d = Guardrail().evaluate(Action.SCALE_DEPLOYMENT, "frontend", replicas=15)
    assert not d.allowed and "cap" in d.reason


def test_tc08_unknown_llm_action_escalated(monkeypatch):
    import json
    import httpx
    import aegis.explainer as ex

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            body = {"root_cause": "disk", "explanation": "x", "proposed_action": "format_disk",
                    "target": "node-1"}
            return {"choices": [{"message": {"content": json.dumps(body)}}]}

    monkeypatch.setattr(httpx, "post", lambda *a, **k: Resp())
    d = ex._groq(IncidentContext(0.0, ["redis"], {}, [], [], 0.0), "key")
    assert d.proposed_action == Action.ESCALATE_TO_HUMAN
    assert Guardrail().evaluate(d.proposed_action, d.target).level.value == "L0"


def test_tc09_llm_offline_uses_cache(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    ctx = IncidentContext(0.0, ["cart"], {}, [], [], 0.0, fault_id=1)
    d = explain(ctx)
    assert d.proposed_action == Action.REVERT_CONFIGMAP


def test_tc09b_llm_error_falls_back(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "invalid")
    import aegis.explainer as ex
    monkeypatch.setattr(ex, "_groq", lambda *a: (_ for _ in ()).throw(TimeoutError()))
    d = explain(IncidentContext(0.0, ["redis"], {}, [], [], 0.0, fault_id=4))
    assert d.proposed_action == Action.RESTART_POD


def test_tc10_reproducible():
    a = [experiments._run_fault(f, 3, "aegis") for f in (1, 2, 4, 5)]
    b = [experiments._run_fault(f, 3, "aegis") for f in (1, 2, 4, 5)]
    assert a == b


def test_tc11_real_cluster_never_runs_l4(monkeypatch):
    calls = []
    monkeypatch.setattr(k8s, "kubectl", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append(a))
    c = k8s.K8sCluster.__new__(k8s.K8sCluster)
    c.events, c.t, c.pods, c.faults = [], 0, {"redis": "DiskPressure"}, {}
    for act in (Action.DELETE_PVC, Action.DELETE_NAMESPACE):
        c.apply(act, "redis-data")
    assert calls == []
