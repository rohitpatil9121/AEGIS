// Talks to the FastAPI backend when one is reachable; otherwise runs the real Python
// engine in the browser (Pyodide in a Web Worker) so a static deploy still works.
const BASE = (import.meta.env.VITE_API_URL || '').replace(/\/$/, '')
const PROBE_MS = BASE ? 20000 : 4000 // a sleeping free-tier backend needs time to wake

let mode = null
let ready = null
let worker = null
let seq = 0
const waiting = new Map()

const SOURCES = import.meta.glob(['../../backend/aegis/*.py', '!**/api.py', '!**/cluster.py'],
  { query: '?raw', import: 'default', eager: true })
const RESULTS_CSV = Object.values(import.meta.glob('../../backend/results.csv',
  { query: '?raw', import: 'default', eager: true }))[0] || ''

function startDemo() {
  worker = new Worker(new URL('./demo.worker.js', import.meta.url))
  worker.onmessage = ({ data }) => {
    const p = waiting.get(data.id)
    if (!p) return
    waiting.delete(data.id)
    data.error ? p.reject(new Error(data.error)) : p.resolve(data.result)
  }
  const files = Object.fromEntries(Object.entries(SOURCES).map(([p, src]) => [p.split('/').pop(), src]))
  return call('init', { files, results: RESULTS_CSV })
}

function call(method, payload) {
  const id = ++seq
  return new Promise((resolve, reject) => {
    waiting.set(id, { resolve, reject })
    worker.postMessage({ id, method, ...payload })
  })
}

async function probe() {
  try {
    const r = await fetch(BASE + '/api/state', { signal: AbortSignal.timeout(PROBE_MS) })
    if (r.ok) { mode = 'server'; return }
  } catch { /* fall through to the in-browser demo */ }
  mode = 'demo'
  await startDemo()
}

const init = () => (ready ||= probe())

async function request(method, path, body) {
  await init()
  if (mode === 'demo') return call('request', { http: method, path, body: body ?? null })
  const r = await fetch(BASE + path, method === 'GET' ? undefined : {
    method, headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined,
  })
  return r.json()
}

export const apiGet = path => request('GET', path)
export const apiPost = (path, body) => request('POST', path, body)
export const apiMode = async () => { await init(); return mode }
