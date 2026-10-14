"""HTTP API consumed by the dashboard. Run: uvicorn aegis.api:app --port 8000"""
from __future__ import annotations

import asyncio
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import experiments
from .engine import Engine
from .models import FAULTS

REAL = os.getenv("AEGIS_CLUSTER", "sim") == "k8s"


def new_engine(**kw) -> Engine:
    if REAL:
        from .cluster import K8sCluster
        cluster = state.get("cluster") or K8sCluster()
        state["cluster"] = cluster
        return Engine(cluster=cluster, **kw)
    return Engine(**kw)


state: dict = {"speed": 1.0, "error": None}
state["engine"] = new_engine()


async def loop() -> None:
    while True:
        started = time.monotonic()
        try:
            await asyncio.to_thread(state["engine"].tick)
            state["error"] = None
        except Exception as ex:  # cluster hiccup: keep the loop alive, surface it in the UI
            state["error"] = str(ex)
        await asyncio.sleep(max(0.05, 1.0 / state["speed"] - (time.monotonic() - started)))


@asynccontextmanager
async def lifespan(_: FastAPI):
    task = asyncio.create_task(loop())
    yield
    task.cancel()


app = FastAPI(title="AEGIS", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class Settings(BaseModel):
    guardrail: bool | None = None
    predictive: bool | None = None
    speed: float | None = None


@app.get("/api/state")
def get_state():
    return {**state["engine"].snapshot(), "speed": state["speed"], "error": state["error"],
            "faults": {k: v for k, v in FAULTS.items()}}


@app.post("/api/fault/{fid}")
def inject(fid: int):
    if fid not in FAULTS:
        raise HTTPException(404, "unknown fault")
    try:
        state["engine"].cluster.inject(fid)
    except Exception as ex:
        raise HTTPException(400, str(ex))
    return {"ok": True}


@app.post("/api/cleanup")
def cleanup():
    state["engine"].cluster.cleanup()
    return {"ok": True}


@app.post("/api/reset")
def reset():
    e = state["engine"]
    state["engine"] = new_engine(guardrail=e.guard.enabled, predictive=e.predictive)
    return {"ok": True}


@app.post("/api/incident/{iid}/resolve")
def human_resolve(iid: int):
    state["engine"].human_resolve(iid)
    return {"ok": True}


@app.post("/api/settings")
def settings(s: Settings):
    e = state["engine"]
    if s.guardrail is not None:
        e.guard.enabled = s.guardrail
    if s.predictive is not None:
        e.predictive = s.predictive
    if s.speed is not None:
        state["speed"] = max(0.5, min(5.0, s.speed))
    return {"ok": True}


@app.get("/api/results")
def results():
    return experiments.summarize()


@app.post("/api/experiments/run")
async def run_experiments():
    rows = await asyncio.to_thread(experiments.run_all)
    return experiments.summarize(rows)
