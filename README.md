# AEGIS — AI-Driven Self-Healing & Predictive Auto-Scaling for Cloud-Native Apps

Observe → Predict → Detect → Explain (LLM) → **Guardrail** → Act, with a live dashboard.

## Quick start (no Kubernetes needed)
New to AEGIS, or on Windows without `make`? Follow the [step-by-step tutorial](docs/first-repair.md).

```
make setup
make backend      # terminal 1 — control loop + API on :8000 (simulated 6-service cluster)
make dashboard    # terminal 2 — open http://localhost:5173
```
Click a fault in the dashboard (or `make fault-1` … `make fault-6`) and watch AEGIS detect it, explain it and repair it.
Set `GROQ_API_KEY` to use the real LLM (`llama-3.1-8b-instant`); without a key, cached explanations are used so the demo never fails offline.

## Layout (one owner per folder — never two people on the same file)
| Path | Owner | What |
|---|---|---|
| `backend/aegis/models.py` | all | The contracts: `IncidentContext`, `Action` enum, 6 faults |
| `backend/aegis/engine.py` (`SimCluster`), `k8s/` , `Makefile` | M1 | Cluster, fault injection, executor |
| `backend/aegis/forecaster.py` | M2 | Holt forecaster, predictive scaler, reactive-HPA baseline |
| `backend/aegis/explainer.py`, `guardrail.py` | M3 | Anomaly rules, LLM root cause, L0–L4 guardrail |
| `frontend/`, `backend/aegis/experiments.py` | M4 | Dashboard, experiment runner → `results.csv` |

## Experiments
`make experiments` runs E1 (MTTR), E3 (scaling), E4 (safety) × 5 seeds and writes `backend/results.csv`; the dashboard's **Run experiments** button does the same and charts mean ± std.
Current simulated results: MTTR 92s (K8s) → 20s (AEGIS); SLO breach 29s → 0.6s at +0.7 replicas; unsafe actions executed 17% → 0%.

## Real Kubernetes cluster
```
make cluster        # kind + Online Boutique v0.10.5 + metrics-server + Chaos Mesh (needs Docker, kind, kubectl, helm)
make backend-k8s    # AEGIS drives the cluster in your current kubectl context
make dashboard      # badge in the top bar shows "Live Kubernetes"
make cluster-down
```
`backend/aegis/cluster.py` (`K8sCluster`) replaces the simulator with live signals:

| Signal | Source |
|---|---|
| Pod health | `kubectl get pods` — CrashLoopBackOff, OOMKilled, Down, NotReady > 25s |
| p95 latency, errors | active HTTP probe of the storefront through `kubectl port-forward` |
| Request rate | Prometheus if `AEGIS_PROM_URL` is set, otherwise frontend CPU ÷ `AEGIS_CPU_M_PER_RPS` |
| Memory, disk | metrics-server; `du` of redis `/data` |

| Fault | Real injection | Real repair |
|---|---|---|
| 1 Config error | `cart-config` ConfigMap → port 6380 + restart | revert ConfigMap + rollout restart |
| 2 Memory leak | Chaos Mesh StressChaos | remove stress + rollout restart |
| 3 Traffic spike | loadgenerator `USERS=250` | `kubectl scale deploy/frontend` (capped by `AEGIS_MAX_REPLICAS`, default 4) |
| 4 Dependency down | scale `redis-cart` to 0 | scale back to 1 |
| 5 Disk full | fill redis `/data` | **blocked** (delete PVC); engineer button removes the filler file |
| 6 Network latency | Chaos Mesh NetworkChaos 400ms | escalated |

L4 actions are never executed against a real cluster, even with the guardrail switched off. The experiment matrix (`make experiments`) still uses the simulator, so it can run 5 seeds in seconds.
