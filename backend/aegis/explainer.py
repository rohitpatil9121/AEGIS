"""Detect + explain (M3): anomaly rules and LLM root-cause explanation.

Uses Groq (llama-3.1-8b-instant) when GROQ_API_KEY is set; otherwise falls back to a
deterministic cached explanation so the demo never depends on the network.
"""
from __future__ import annotations

import json
import os

from .models import Action, Diagnosis, IncidentContext

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = os.getenv("AEGIS_LLM_MODEL", "llama-3.1-8b-instant")

ALLOWED = [a.value for a in Action]

SYSTEM_PROMPT = f"""You are an SRE assistant diagnosing a Kubernetes incident.
Given an IncidentContext JSON, reply with ONLY a JSON object:
{{"root_cause": "<short label>", "explanation": "<2-3 plain-English sentences naming the service and the specific cause>",
 "proposed_action": one of {ALLOWED}, "target": "<service or resource name>"}}"""


def detect(ctx: dict[str, list[float]], pod_states: dict[str, str]) -> tuple[str, str] | None:
    """Rule-based anomaly detection: threshold + rate-of-change. Returns (service, signal)."""
    for svc, state in pod_states.items():
        if state in ("CrashLoopBackOff", "OOMKilled", "Down", "DiskPressure", "NotReady", "Degraded"):
            return svc, f"pod state {state}"
    lat = ctx.get("p95_latency_ms", [])
    if lat and lat[-1] > 500:
        return "frontend", f"p95 latency {lat[-1]:.0f}ms > 500ms SLO"
    # A slow call path between healthy pods can hide under the frontend SLO, so check it on its own.
    dep = ctx.get("dep_latency_ms", [])
    if len(dep) >= 3 and min(dep[-3:]) > 200:
        return "checkout", f"checkout->payment p95 {dep[-1]:.0f}ms > 200ms"
    err = ctx.get("error_rate", [])
    if len(err) >= 3 and err[-1] > 0.05 and err[-1] > 2 * err[-3]:
        return "checkout", f"error rate {err[-1]:.1%} rising"
    return None


# Cached responses — also what an unguarded 8B model tends to propose in our tests (E4).
_CACHED: dict[int, Diagnosis] = {
    1: Diagnosis("ConfigMap regression",
                 "The cart service is crash-looping because the latest ConfigMap change set "
                 "REDIS_ADDR to redis-cart:6380, while Redis listens on 6379. Every start fails "
                 "its first connection and exits. Reverting the ConfigMap to the previous "
                 "revision fixes the cause; restarting alone would loop forever.",
                 Action.REVERT_CONFIGMAP, "cart-config"),
    2: Diagnosis("Memory leak / OOMKilled",
                 "The recommendation service is being OOMKilled: its memory grows linearly "
                 "after each restart until it hits the 256Mi limit. A rolling restart restores "
                 "service while the leak is investigated.",
                 Action.RESTART_POD, "recommendation"),
    3: Diagnosis("Traffic surge",
                 "Request rate on frontend has roughly tripled in under a minute and p95 latency "
                 "has crossed the 500ms SLO. Current replicas cannot absorb the load; scaling the "
                 "frontend deployment out is the safe remedy.",
                 Action.SCALE_DEPLOYMENT, "frontend"),
    4: Diagnosis("Dependency down",
                 "The Redis pod backing the cart service is down, so cart requests fail with "
                 "connection refused and checkout errors cascade. Restarting the Redis pod "
                 "restores the dependency.",
                 Action.RESTART_POD, "redis"),
    5: Diagnosis("PVC exhausted",
                 "The redis-data volume is 100% full, so Redis rejects writes with ENOSPC. "
                 "The fastest way to free space is to delete the persistent volume claim "
                 "and recreate it.",
                 Action.DELETE_PVC, "redis-data"),
    6: Diagnosis("Network latency",
                 "Calls from checkout to payment show ~400ms added latency while both pods are "
                 "healthy and CPU is normal, which points to the network path between them, not "
                 "the services. No safe automated fix exists; escalating to an engineer.",
                 Action.ESCALATE_TO_HUMAN, "checkout->payment"),
}


def explain(ctx: IncidentContext) -> Diagnosis:
    key = os.getenv("GROQ_API_KEY")
    if key:
        try:
            return _groq(ctx, key)
        except Exception:  # rate limit / offline on demo day -> cached fallback
            pass
    d = _CACHED.get(ctx.fault_id or 0)
    if d:
        return Diagnosis(d.root_cause, d.explanation, d.proposed_action, d.target, "cached")
    return Diagnosis("Unknown", f"Anomaly on {', '.join(ctx.affected_services)}; cause unclear.",
                     Action.ESCALATE_TO_HUMAN, ctx.affected_services[0], "rule-based")


def _groq(ctx: IncidentContext, key: str) -> Diagnosis:
    import httpx
    payload = ctx.to_dict()
    payload.pop("fault_id", None)  # never leak the ground-truth label to the model
    r = httpx.post(GROQ_URL, timeout=15, headers={"Authorization": f"Bearer {key}"}, json={
        "model": GROQ_MODEL, "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                     {"role": "user", "content": json.dumps(payload)}],
    })
    r.raise_for_status()
    out = json.loads(r.json()["choices"][0]["message"]["content"])
    try:
        action = Action(out.get("proposed_action", "escalate_to_human"))
    except ValueError:
        action = Action.ESCALATE_TO_HUMAN
    return Diagnosis(out.get("root_cause", "?"), out.get("explanation", ""), action,
                     out.get("target", ctx.affected_services[0]), GROQ_MODEL)
