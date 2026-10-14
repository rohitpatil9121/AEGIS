"""K8sCluster (M1): the real-cluster twin of SimCluster. Same surface:
`t, pods, replicas, memory, disk, faults, events, logs, step(), inject(), cleanup(),
set_replicas(), apply(), recover()`.

Talks to the cluster through `kubectl` (current context) so there is no extra client
dependency. Signals:
  * pod states       <- kubectl get pods (CrashLoopBackOff / OOMKilled / Down / NotReady)
  * latency, errors  <- active HTTP probe of the frontend through a port-forward
  * request rate     <- Prometheus if AEGIS_PROM_URL is set, else frontend CPU as a proxy
  * memory           <- metrics-server (kubectl top)
  * disk             <- du of redis /data against its emptyDir sizeLimit
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

import re
from concurrent.futures import ThreadPoolExecutor

import httpx

from .models import FAULTS, Action

NS = os.getenv("AEGIS_NAMESPACE", "default")
CHAOS_DIR = Path(__file__).resolve().parents[2] / "k8s" / "chaos"
PROBE_PORT = int(os.getenv("AEGIS_PROBE_PORT", "8089"))
PROM_URL = os.getenv("AEGIS_PROM_URL")
PROM_RPS_QUERY = os.getenv("AEGIS_PROM_RPS_QUERY",
                           'sum(rate(http_requests_total{app="frontend"}[1m]))')
MAX_REPLICAS = int(os.getenv("AEGIS_MAX_REPLICAS", "4"))
CPU_M_PER_RPS = float(os.getenv("AEGIS_CPU_M_PER_RPS", "1.5"))  # calibrate per machine
REDIS_DISK_LIMIT_MB = 64
BASE_USERS, SPIKE_USERS = "10", os.getenv("AEGIS_SPIKE_USERS", "250")

# AEGIS service name -> Online Boutique deployment / app label
DEPLOY = {"frontend": "frontend", "cart": "cartservice", "checkout": "checkoutservice",
          "recommendation": "recommendationservice", "payment": "paymentservice",
          "redis": "redis-cart"}
LOG_SIGNALS = {"cart": re.compile(r"RedisConnection|connect.*redis|6380", re.I),
               "checkout": re.compile(r"connection refused|deadline exceeded", re.I)}
CHAOS = {2: "fault-2-memory-leak.yaml", 6: "fault-6-network-latency.yaml"}


def kubectl(*args: str, input: str | None = None, timeout: int = 20, check: bool = False) -> str:
    r = subprocess.run(["kubectl", "-n", NS, *args], input=input, capture_output=True,
                       text=True, timeout=timeout)
    if check and r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or r.stdout.strip())
    return r.stdout


class K8sCluster:
    def __init__(self, seed: int | None = None):
        if not shutil.which("kubectl"):
            raise RuntimeError("kubectl not found on PATH")
        kubectl("get", "deploy", "frontend", check=True)  # fail fast if app isn't deployed
        self.t = 0
        self._t0 = time.monotonic()
        self.pods = {s: "Running" for s in DEPLOY}
        self.replicas = {s: 1 for s in DEPLOY}
        self.memory = {s: 0.0 for s in DEPLOY}
        self.disk = 0.0
        self.faults: dict[int, int] = {}
        self.events: deque[str] = deque(maxlen=20)
        self.logs: deque[str] = deque(maxlen=20)
        self._not_ready_since: dict[str, int] = {}
        self._restarts: dict[str, int] = {}
        self._oom_seen: dict[str, int] = {}
        self._cpu_m = 0.0
        self._last_scale = -999
        self._pf: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._ensure_port_forward()

    # ---------- observe ----------
    def _ensure_port_forward(self) -> None:
        if self._pf and self._pf.poll() is None:
            return
        self._pf = subprocess.Popen(
            ["kubectl", "-n", NS, "port-forward", "svc/frontend", f"{PROBE_PORT}:80"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1.5)

    def _probe(self) -> tuple[float, float]:
        """Hit 3 storefront pages concurrently; return (worst latency ms, error ratio)."""
        def one(path: str) -> tuple[float, int]:
            t0 = time.perf_counter()
            try:
                r = httpx.get(f"http://127.0.0.1:{PROBE_PORT}{path}", timeout=2)
                bad = int(r.status_code >= 500)
            except httpx.HTTPError:
                bad = 1
            return (time.perf_counter() - t0) * 1000, bad
        with ThreadPoolExecutor(3) as ex:
            res = list(ex.map(one, ("/", "/cart", "/product/OLJCESPC7Z")))
        if all(b for _, b in res):
            self._ensure_port_forward()
        return max(l for l, _ in res), sum(b for _, b in res) / 3

    def _scan_logs(self) -> None:
        """Pods can look healthy while failing every request (cartservice does this on a
        bad REDIS_ADDR). When the probe sees errors, look for dependency errors in logs."""
        for svc, pattern in LOG_SIGNALS.items():
            out = kubectl("logs", f"deploy/{DEPLOY[svc]}", "--since=20s", "--tail=50", timeout=8)
            hit = next((l.strip() for l in out.splitlines() if pattern.search(l)), None)
            if hit and self.pods[svc] == "Running":
                self.pods[svc] = "Degraded"
                self.logs.append(f"{svc}: {hit[:160]}")

    def _read_pods(self) -> None:
        data = json.loads(kubectl("get", "pods", "-o", "json") or '{"items": []}')
        by_app: dict[str, list] = {}
        for p in data["items"]:
            by_app.setdefault(p["metadata"].get("labels", {}).get("app", ""), []).append(p)
        for svc, dep in DEPLOY.items():
            pods = [p for p in by_app.get(dep, []) if not p["metadata"].get("deletionTimestamp")]
            state = "Running"
            if not pods:
                state = "Down"
            restarts = 0
            ready = 0
            for p in pods:
                for cs in p["status"].get("containerStatuses", []):
                    restarts += cs.get("restartCount", 0)
                    ready += cs.get("ready", False)
                    waiting = cs.get("state", {}).get("waiting", {}).get("reason")
                    last = cs.get("lastState", {}).get("terminated", {}).get("reason")
                    if waiting == "CrashLoopBackOff":
                        state = "OOMKilled" if last == "OOMKilled" else "CrashLoopBackOff"
                    elif last == "OOMKilled" and restarts > self._oom_seen.get(svc, 0):
                        state = "OOMKilled"
            if state == "OOMKilled":
                self._oom_seen[svc] = restarts
            if pods and ready == 0 and state == "Running":
                since = self._not_ready_since.setdefault(svc, self.t)
                if self.t - since > 25:  # tolerate normal rollouts
                    state = "NotReady"
            else:
                self._not_ready_since.pop(svc, None)
            if svc == "redis" and state == "Running" and self.disk >= 90:
                state = "DiskPressure"
            if state != self.pods[svc]:
                self.events.append(f"t={self.t} {svc}: {self.pods[svc]} -> {state}")
            self.pods[svc] = state
            self._restarts[svc] = restarts
            if svc != "frontend":
                self.replicas[svc] = max(1, len(pods))

    def _read_top(self) -> None:
        out = kubectl("top", "pods", "--no-headers", timeout=10)
        cpu: dict[str, float] = {}
        for line in out.splitlines():
            parts = line.split()
            if len(parts) < 3:
                continue
            for svc, dep in DEPLOY.items():
                if parts[0].startswith(dep + "-"):
                    cpu[svc] = cpu.get(svc, 0) + float(parts[1].rstrip("m") or 0)
                    self.memory[svc] = float(parts[2].rstrip("Mi") or 0)
        self._cpu_m = cpu.get("frontend", self._cpu_m)

    def _read_disk(self) -> None:
        out = kubectl("exec", "deploy/redis-cart", "--", "du", "-sm", "/data", timeout=10)
        try:
            self.disk = round(100 * float(out.split()[0]) / REDIS_DISK_LIMIT_MB, 1)
        except (IndexError, ValueError):
            pass

    def _rps(self) -> float:
        if PROM_URL:
            try:
                r = httpx.get(f"{PROM_URL}/api/v1/query", params={"query": PROM_RPS_QUERY}, timeout=2)
                return float(r.json()["data"]["result"][0]["value"][1])
            except Exception:
                pass
        return self._cpu_m / CPU_M_PER_RPS

    def step(self) -> dict[str, float]:
        with self._lock:
            self.t = int(time.monotonic() - self._t0)  # wall-clock seconds: MTTR is real time
            self._read_pods()
            if self.t - getattr(self, "_slow_at", -99) >= 5:
                self._slow_at = self.t
                self._read_top()
                self._read_disk()
            lat, err = self._probe()
            if err > 0:
                self._scan_logs()
            rps = self._rps()
            return {"rps": rps, "p95_latency_ms": lat, "error_rate": err,
                    "cpu": min(100.0, self._cpu_m / max(1, self.replicas["frontend"]) / 2)}

    # ---------- fault injection ----------
    def inject(self, fid: int) -> None:
        self.faults[fid] = self.t
        self.events.append(f"t={self.t} FAULT_MARKER id={fid} ({FAULTS[fid]['name']})")
        if fid == 1:
            kubectl("patch", "configmap", "cart-config", "--type", "merge",
                    "-p", '{"data":{"REDIS_ADDR":"redis-cart:6380"}}', check=True)
            kubectl("rollout", "restart", "deploy/cartservice", check=True)
        elif fid == 3:
            kubectl("set", "env", "deploy/loadgenerator", f"USERS={SPIKE_USERS}", check=True)
        elif fid == 4:
            kubectl("scale", "deploy/redis-cart", "--replicas=0", check=True)
        elif fid == 5:
            kubectl("exec", "deploy/redis-cart", "--", "sh", "-c",
                    f"dd if=/dev/zero of=/data/aegis-fill bs=1M count={REDIS_DISK_LIMIT_MB - 2} 2>/dev/null; true",
                    timeout=60)
            self.disk = 97.0
        elif fid in CHAOS:
            out = subprocess.run(["kubectl", "apply", "-f", str(CHAOS_DIR / CHAOS[fid])],
                                 capture_output=True, text=True)
            if out.returncode:
                self.faults.pop(fid, None)
                raise RuntimeError(f"Chaos Mesh required for fault {fid}: {out.stderr.strip()}")

    def cleanup(self) -> None:
        kubectl("patch", "configmap", "cart-config", "--type", "merge",
                "-p", '{"data":{"REDIS_ADDR":"redis-cart:6379"}}')
        if 1 in self.faults:
            kubectl("rollout", "restart", "deploy/cartservice")
        kubectl("set", "env", "deploy/loadgenerator", f"USERS={BASE_USERS}")
        kubectl("scale", "deploy/redis-cart", "--replicas=1")
        kubectl("exec", "deploy/redis-cart", "--", "rm", "-f", "/data/aegis-fill")
        for f in CHAOS.values():
            subprocess.run(["kubectl", "delete", "--ignore-not-found", "-f", str(CHAOS_DIR / f)],
                           capture_output=True)
        self.faults.clear()
        self.disk = 0.0
        self.events.append(f"t={self.t} cleanup: all faults removed")

    # ---------- act (executor) ----------
    def set_replicas(self, svc: str, n: int) -> None:
        n = min(n, MAX_REPLICAS)
        if n == self.replicas[svc] or self.t - self._last_scale < 15:
            return
        kubectl("scale", f"deploy/{DEPLOY[svc]}", f"--replicas={n}")
        self.events.append(f"t={self.t} scale {svc} {self.replicas[svc]} -> {n}")
        self.replicas[svc] = n
        self._last_scale = self.t

    def apply(self, action: Action, target: str, replicas: int | None = None) -> None:
        self.events.append(f"t={self.t} ACTION {action.value} -> {target}")
        svc = next((s for s in DEPLOY if target.startswith(s)), target)
        dep = DEPLOY.get(svc, target)
        if action == Action.REVERT_CONFIGMAP:
            kubectl("patch", "configmap", "cart-config", "--type", "merge",
                    "-p", '{"data":{"REDIS_ADDR":"redis-cart:6379"}}', check=True)
            kubectl("rollout", "restart", "deploy/cartservice", check=True)
        elif action == Action.ROLLBACK_DEPLOYMENT:
            kubectl("rollout", "undo", f"deploy/{dep}", check=True)
        elif action == Action.RESTART_POD:
            if svc == "redis" and self.pods["redis"] == "Down":
                kubectl("scale", "deploy/redis-cart", "--replicas=1", check=True)
            if svc == "recommendation" and 2 in self.faults:
                subprocess.run(["kubectl", "delete", "--ignore-not-found", "-f",
                                str(CHAOS_DIR / CHAOS[2])], capture_output=True)
            kubectl("rollout", "restart", f"deploy/{dep}", check=True)
        elif action == Action.SCALE_DEPLOYMENT:
            self._last_scale = -999
            self.set_replicas(svc if svc in DEPLOY else "frontend", replicas or MAX_REPLICAS)
        # L4 actions only reach here in an unguarded ablation run; we still refuse to run
        # them against a real cluster and just record that they *would* have executed.

    def recover(self, action: Action, target: str) -> bool:
        """Verify against live pod state rather than assuming success."""
        self._read_pods()
        if self._probe()[1] > 0:
            self._scan_logs()
        svc = next((s for s in DEPLOY if target.startswith(s)), "cart")
        if action == Action.REVERT_CONFIGMAP:
            svc = "cart"
        healthy = self.pods.get(svc) == "Running"
        if healthy:
            for fid, f in FAULTS.items():
                if f["service"] == svc:
                    self.faults.pop(fid, None)
        return healthy

    def human_fix(self, fid: int) -> None:
        if fid == 5:
            kubectl("exec", "deploy/redis-cart", "--", "rm", "-f", "/data/aegis-fill")
            self.disk = 0.0
            self.pods["redis"] = "Running"
        elif fid in CHAOS:
            subprocess.run(["kubectl", "delete", "--ignore-not-found", "-f",
                            str(CHAOS_DIR / CHAOS[fid])], capture_output=True)

    def close(self) -> None:
        if self._pf:
            self._pf.terminate()
