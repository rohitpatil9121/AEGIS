/* In-browser AEGIS: runs backend/aegis (engine, forecaster, explainer, guardrail,
   experiments) under Pyodide and answers the same /api routes as api.py. */
importScripts('https://cdn.jsdelivr.net/pyodide/v0.26.4/full/pyodide.js')

const GLUE = `
import json, sys
sys.path.insert(0, "/aegis_pkg")
from aegis.engine import Engine
from aegis.models import FAULTS
from aegis import experiments

S = {"speed": 1.0, "engine": Engine()}

def handle(method, path, body):
    body = json.loads(body) if body else {}
    e = S["engine"]
    parts = path.strip("/").split("/")[1:]
    if method == "GET" and parts == ["state"]:
        out = {**e.snapshot(), "speed": S["speed"], "error": None,
               "faults": {k: v for k, v in FAULTS.items()}}
    elif method == "GET" and parts == ["results"]:
        out = experiments.summarize()
    elif parts[:1] == ["fault"]:
        fid = int(parts[1])
        if fid not in FAULTS:
            out = {"detail": "unknown fault"}
        else:
            e.cluster.inject(fid); out = {"ok": True}
    elif parts == ["cleanup"]:
        e.cluster.cleanup(); out = {"ok": True}
    elif parts == ["reset"]:
        S["engine"] = Engine(guardrail=e.guard.enabled, predictive=e.predictive); out = {"ok": True}
    elif parts[:1] == ["incident"]:
        e.human_resolve(int(parts[1])); out = {"ok": True}
    elif parts == ["settings"]:
        if body.get("guardrail") is not None: e.guard.enabled = body["guardrail"]
        if body.get("predictive") is not None: e.predictive = body["predictive"]
        if body.get("speed") is not None: S["speed"] = max(0.5, min(5.0, body["speed"]))
        out = {"ok": True}
    elif parts == ["experiments", "run"]:
        out = experiments.summarize(experiments.run_all())
    else:
        out = {"detail": "not found"}
    return json.dumps(out)

def tick():
    S["engine"].tick()
    return S["speed"]
`

let py = null

async function init(files, results) {
  py = await loadPyodide()
  py.FS.mkdirTree('/aegis_pkg/aegis')
  for (const [name, src] of Object.entries(files)) py.FS.writeFile(`/aegis_pkg/aegis/${name}`, src)
  if (results) py.FS.writeFile('/aegis_pkg/results.csv', results)
  py.runPython(GLUE)
  const handle = py.globals.get('handle')
  const tick = py.globals.get('tick')
  const loop = () => {
    let speed = 1
    try { speed = tick() } catch (err) { console.error(err) }
    setTimeout(loop, 1000 / speed)
  }
  loop()
  return handle
}

let handle = null

self.onmessage = async ({ data }) => {
  try {
    let result
    if (data.method === 'init') { handle = await init(data.files, data.results); result = { ok: true } }
    else result = JSON.parse(handle(data.http, data.path, data.body ? JSON.stringify(data.body) : ''))
    self.postMessage({ id: data.id, result })
  } catch (err) {
    self.postMessage({ id: data.id, error: String(err) })
  }
}
