import { useEffect, useState, useCallback, useRef } from 'react'
import {
  ResponsiveContainer, LineChart, Line, XAxis, YAxis, Tooltip,
  ReferenceLine, BarChart, Bar, Cell, CartesianGrid, ErrorBar, LabelList,
} from 'recharts'
import {
  Shield, Activity, Zap, Bug, Brain, ShieldCheck, ShieldX, Timer, RotateCcw,
  Eraser, FlaskConical, UserCheck, AlertTriangle, CheckCircle2, Gauge, WifiOff,
  Server, Cpu, Keyboard, ChevronRight,
} from 'lucide-react'
import { apiGet, apiPost, apiMode } from './api.js'

// Chart series palette: validated with the dataviz skill's validator (dark, surface #121722).
// AEGIS = blue, forecast = orange; baselines are neutral (gray, dashed) so identity never
// depends on colour alone. Status colours (good/warn/bad) are reserved for state.
const C = {
  ink: '#e6e9ef', ink2: '#b4bccc', muted: '#8a93a6', line: '#232b3b', grid: '#1c2331',
  aegis: '#3987e5', forecast: '#d95926', baseline: '#7d869a',
  good: '#3ecf8e', warn: '#f5b84a', bad: '#ff5d5d',
}
const LEVELS = {
  L0: ['Read', C.muted], L1: ['Restart', C.good], L2: ['Scale', C.aegis],
  L3: ['Config', C.warn], L4: ['Destructive', C.bad],
}
const EXPECT = { auto: ['Auto-repair', 'p-good'], blocked: ['Must block', 'p-bad'], diagnose: ['Diagnose only', 'p-warn'] }
const STATUS = {
  detected: ['Detected', 'p-warn', AlertTriangle], diagnosing: ['Diagnosing', 'p-info', Brain],
  repairing: ['Repairing', 'p-info', Activity], resolved: ['Resolved', 'p-good', CheckCircle2],
  blocked: ['Blocked', 'p-bad', ShieldX], escalated: ['Escalated', 'p-warn', UserCheck],
}
const OPEN = ['detected', 'diagnosing', 'repairing']

const post = apiPost

export default function App() {
  const [s, setS] = useState(null)
  const [offline, setOffline] = useState(false)
  const [results, setResults] = useState(null)
  const [running, setRunning] = useState(false)
  const [toast, setToast] = useState(null)
  const [picked, setPicked] = useState(null)
  const [busy, setBusy] = useState(null)
  const toastTimer = useRef()

  const refresh = useCallback(() =>
    apiGet('/api/state').then(d => { setS(d); setOffline(false) })
      .catch(() => setOffline(true)), [])

  useEffect(() => { refresh(); const id = setInterval(refresh, 1000); return () => clearInterval(id) }, [refresh])
  useEffect(() => { apiGet('/api/results').then(d => d.E1 && setResults(d)).catch(() => {}) }, [])

  const say = (msg, kind = 'info') => {
    clearTimeout(toastTimer.current)
    setToast({ msg, kind })
    toastTimer.current = setTimeout(() => setToast(null), 3500)
  }
  useEffect(() => {
    apiMode().then(m => m === 'demo' && say('No backend reachable: running the AEGIS engine in your browser'))
  }, [])
  const act = async (url, body, msg, key = url) => {
    setBusy(key)
    try {
      const r = await post(url, body)
      if (r?.detail) say(r.detail, 'bad'); else if (msg) say(msg)
    } catch { say('Backend unreachable', 'bad') }
    finally { setBusy(null); refresh() }
  }

  useEffect(() => {
    const onKey = e => {
      if (e.target.closest('input, textarea, select') || e.metaKey || e.ctrlKey || e.altKey) return
      if (/^[1-6]$/.test(e.key)) act(`/api/fault/${e.key}`, null, `Injected fault ${e.key}: ${s?.faults[e.key]?.name}`)
      if (e.key === 'c') act('/api/cleanup', null, 'All faults cleaned up')
      if (e.key === 'Escape') setPicked(null)
    }
    window.addEventListener('keydown', onKey); return () => window.removeEventListener('keydown', onKey)
  })
  const runExp = async () => {
    setRunning(true)
    try { setResults(await post('/api/experiments/run')); say('Experiment matrix complete — results.csv updated') }
    catch { say('Experiment run failed', 'bad') } finally { setRunning(false) }
  }

  if (!s) return <Skeleton offline={offline} />

  const active = s.incidents.find(i => OPEN.includes(i.status))
  const focus = s.incidents.find(i => i.id === picked)
    || active || s.incidents.find(i => i.status === 'blocked' || i.status === 'escalated') || s.incidents[0]
  const alert = !!active || s.services.some(x => x.state !== 'Running')

  return (
    <div className="app">
      <a href="#main" className="skip">Skip to dashboard</a>
      <TopBar s={s} alert={alert} offline={offline} act={act} />
      {!s.settings.guardrail && (
        <div className="banner bad" role="alert"><ShieldX size={16} />
          <span><b>Guardrail disabled — ablation mode.</b> Destructive (L4) proposals will be marked as executed.
            {s.cluster === 'kubernetes' && ' On a real cluster they are still never run.'}</span>
        </div>
      )}
      {s.error && <div className="banner warn" role="alert"><AlertTriangle size={16} /><span><b>Cluster error:</b> {s.error}</span></div>}
      <main id="main">
        <Kpis s={s} />
        <div className="grid">
          <div className="col">
            <FaultPanel s={s} act={act} busy={busy} />
            <Topology s={s} />
          </div>
          <div className="col">
            <LoadChart s={s} />
            <LatencyChart s={s} />
            <ActionLog actions={s.actions} />
          </div>
          <div className="col">
            <IncidentCard inc={focus} now={s.t} act={act} busy={busy} pinned={!!picked && focus?.id === picked} unpin={() => setPicked(null)} />
            <IncidentFeed incidents={s.incidents} focusId={focus?.id} pick={setPicked} />
          </div>
        </div>
        <Experiments results={results} running={running} run={runExp} />
      </main>
      <footer className="foot"><Keyboard size={13} /> <kbd>1</kbd>–<kbd>6</kbd> inject fault · <kbd>C</kbd> clean up · <kbd>Esc</kbd> follow live incident</footer>
      <div className="toast-region" role="status" aria-live="polite">
        {toast && <div className={`toast ${toast.kind}`}>{toast.kind === 'bad' ? <AlertTriangle size={14} /> : <CheckCircle2 size={14} />}{toast.msg}</div>}
      </div>
    </div>
  )
}

function Skeleton({ offline }) {
  return (
    <div className="app">
      {offline && (
        <div className="banner bad" role="alert"><WifiOff size={16} />
          <span><b>Cannot reach the AEGIS backend on :8000.</b> Start it with <code>make backend</code> (simulated) or <code>make backend-k8s</code> (real cluster).</span>
        </div>
      )}
      <div className="kpis">{[0, 1, 2, 3].map(i => <div key={i} className="card sk" style={{ height: 104 }} />)}</div>
      <div className="grid">{[0, 1, 2].map(i => <div key={i} className="card sk" style={{ height: 420 }} />)}</div>
    </div>
  )
}

function Switch({ label, on, onChange, danger, hint }) {
  return (
    <label className="toggle" title={hint}>
      {label}
      <button role="switch" aria-checked={on} aria-label={label}
        className={`switch ${danger ? 'danger' : ''} ${on ? 'on' : ''}`} onClick={onChange} />
    </label>
  )
}

function TopBar({ s, alert, offline, act }) {
  const { guardrail, predictive } = s.settings
  const real = s.cluster === 'kubernetes'
  return (
    <header className="top">
      <div className="brand">
        <div className="logo" aria-hidden><Shield size={18} strokeWidth={2.5} /></div>
        <div><h1>AEGIS</h1><p>Self-healing &amp; predictive auto-scaling</p></div>
      </div>
      <span className={`chip ${real ? 'live' : ''}`} title={real ? 'Driving the cluster in your kubectl context' : 'In-process simulator — no cluster needed'}>
        {real ? <Server size={13} /> : <Cpu size={13} />}{real ? 'Live Kubernetes' : 'Simulated cluster'}
      </span>
      {offline
        ? <span className="status stale"><WifiOff size={13} />Reconnecting… data may be stale</span>
        : <span className={`status ${alert ? 'alert' : ''}`}><span className="dot" />{alert ? 'Incident in progress' : 'All systems healthy'}</span>}
      <div className="spacer" />
      <div className="controls" role="group" aria-label="Control plane settings">
        <Switch label="Guardrail" on={guardrail} danger hint="Blocks destructive (L4) repair actions"
          onChange={() => act('/api/settings', { guardrail: !guardrail }, `Guardrail ${guardrail ? 'disabled' : 'enabled'}`)} />
        <Switch label="Predictive scaling" on={predictive} hint="Scale on the 10s forecast instead of current load"
          onChange={() => act('/api/settings', { predictive: !predictive }, `Predictive scaling ${predictive ? 'off' : 'on'}`)} />
        {!real && (
          <div className="seg" role="radiogroup" aria-label="Simulation speed">
            {[1, 2, 4].map(v => (
              <button key={v} role="radio" aria-checked={s.speed === v} className={s.speed === v ? 'on' : ''}
                onClick={() => act('/api/settings', { speed: v })}>{v}×</button>
            ))}
          </div>
        )}
        <span className="clock mono" aria-label={`Elapsed ${s.t} seconds`}>t+{fmtT(s.t)}</span>
      </div>
    </header>
  )
}

const niceMax = m => { const p = 10 ** Math.floor(Math.log10(m || 1)); return Math.ceil((m * 1.1) / (p / 2)) * (p / 2) }
const fmtT = t => `${Math.floor(t / 60)}:${String(t % 60).padStart(2, '0')}`

function Spark({ data, color = C.ink2 }) {
  if (data.length < 2) return null
  const w = 96, h = 28, max = Math.max(...data), min = Math.min(...data), r = max - min || 1
  const pts = data.map((v, i) => `${(i / (data.length - 1)) * w},${h - 2 - ((v - min) / r) * (h - 4)}`).join(' ')
  return <svg className="spark" width={w} height={h} aria-hidden><polyline points={pts} fill="none" stroke={color} strokeWidth="1.5" strokeLinejoin="round" /></svg>
}

function Kpi({ icon: I, label, value, unit, sub, tone, children }) {
  return (
    <div className={`card kpi ${tone ? `tone-${tone}` : ''}`}>
      <div className="label"><I size={14} aria-hidden />{label}</div>
      <div className="kpi-body">
        <div className="value">{value ?? '—'}{value != null && unit && <small> {unit}</small>}</div>
        {children}
      </div>
      <div className="sub">{sub}</div>
    </div>
  )
}

function Kpis({ s }) {
  const st = s.stats
  const rps = s.metrics.rps
  const { aegis, hpa } = st.slo_violations
  return (
    <div className="kpis">
      <Kpi icon={Activity} label="Request rate" value={rps.at(-1)?.toFixed(0)} unit="rps"
        sub={`${s.services[0].replicas} frontend replicas · last 60s`}>
        <Spark data={rps.slice(-60)} />
      </Kpi>
      <Kpi icon={Timer} label="Mean time to recover" value={st.mean_mttr} unit="s" tone={st.resolved ? 'good' : null}
        sub={st.incidents ? `${st.resolved} of ${st.incidents} incidents auto-healed` : 'No incidents yet'} />
      <Kpi icon={ShieldX} label="Unsafe actions blocked" value={st.blocked} tone={st.blocked ? 'bad' : null}
        sub="L4 destructive proposals refused" />
      <Kpi icon={Gauge} label="SLO breach (p95 > 500ms)" value={aegis} unit="s"
        sub={<>vs <b className="mono">{hpa}s</b> for reactive HPA on the same traffic</>}>
        <Compare a={aegis} b={hpa} />
      </Kpi>
    </div>
  )
}

function Compare({ a, b }) {
  const max = Math.max(a, b, 1)
  return (
    <div className="compare" aria-hidden>
      <div><span style={{ width: `${(a / max) * 100}%`, background: C.aegis }} /></div>
      <div><span style={{ width: `${(b / max) * 100}%`, background: C.baseline }} /></div>
    </div>
  )
}

function FaultPanel({ s, act, busy }) {
  return (
    <section className="card" aria-labelledby="h-chaos">
      <h2 id="h-chaos"><Bug size={14} aria-hidden />Chaos · inject fault</h2>
      <div className="faults">
        {Object.entries(s.faults).map(([id, f]) => {
          const live = s.active_faults.includes(+id)
          const [txt, cls] = EXPECT[f.expect]
          const key = `/api/fault/${id}`
          return (
            <button key={id} className={`fault ${live ? 'live' : ''}`} disabled={live || busy === key}
              onClick={() => act(key, null, `Injected fault ${id}: ${f.name}`)}
              aria-label={`Inject fault ${id}, ${f.name} on ${f.service}. Expected: ${txt}${live ? '. Currently active' : ''}`}>
              <kbd className="num">{id}</kbd>
              <span className="meta"><span className="name">{f.name}</span><span className="svc mono">{f.service}</span></span>
              {live ? <span className="pill p-bad"><span className="blip" />Active</span> : <span className={`pill ${cls}`}>{txt}</span>}
            </button>
          )
        })}
      </div>
      <div className="btnrow">
        <button className="btn" disabled={busy === '/api/cleanup'} onClick={() => act('/api/cleanup', null, 'All faults cleaned up')}><Eraser size={13} />Clean up</button>
        <button className="btn" disabled={busy === '/api/reset'} onClick={() => act('/api/reset', null, 'Dashboard state reset')}><RotateCcw size={13} />Reset</button>
      </div>
    </section>
  )
}

const POS = { frontend: [150, 34], cart: [62, 120], checkout: [150, 120], recommendation: [240, 120], payment: [150, 206], redis: [62, 206] }
const stateTone = st => st === 'Running' ? C.good : ['DiskPressure', 'Degraded', 'NotReady'].includes(st) ? C.warn : C.bad

function Topology({ s }) {
  const down = s.services.filter(x => x.state !== 'Running')
  return (
    <section className="card topo" aria-labelledby="h-topo">
      <h2 id="h-topo"><Activity size={14} aria-hidden />Service topology
        <span className="right">{down.length ? <span className="pill p-bad">{down.length} unhealthy</span> : <span className="pill p-good">all healthy</span>}</span>
      </h2>
      <svg viewBox="0 0 300 250" role="img"
        aria-label={`Service dependency graph. ${down.length ? down.map(d => `${d.name} is ${d.state}`).join(', ') : 'All services running'}.`}>
        {s.edges.map(e => {
          const [x1, y1] = POS[e.from], [x2, y2] = POS[e.to]
          return <line key={e.from + e.to} x1={x1} y1={y1} x2={x2} y2={y2} stroke={e.degraded ? C.bad : C.line}
            strokeWidth={e.degraded ? 2 : 1.5} strokeDasharray={e.degraded ? '4 3' : undefined} />
        })}
        {s.services.map(sv => {
          const [x, y] = POS[sv.name], col = stateTone(sv.state), ok = sv.state === 'Running'
          return (
            <g key={sv.name}>
              <title>{`${sv.name}: ${sv.state}, ${sv.replicas} replica(s)`}</title>
              {!ok && <circle className="ring" cx={x} cy={y} r="24" fill={col} opacity=".15" />}
              <circle cx={x} cy={y} r="17" fill="#171d2a" stroke={col} strokeWidth="2" />
              <text x={x} y={y + 4} textAnchor="middle" className="node-rep">{sv.replicas}×</text>
              <text x={x} y={y + 31} textAnchor="middle" className="node-label">{sv.name}</text>
              {!ok && <text x={x} y={y + 43} textAnchor="middle" className="node-sub" style={{ fill: col }}>{sv.state}</text>}
            </g>
          )
        })}
      </svg>
      <div className="legend"><span><i className="dot-l" style={{ background: C.good }} />Running</span><span><i className="dot-l" style={{ background: C.warn }} />Degraded</span><span><i className="dot-l" style={{ background: C.bad }} />Failing</span><span>N× = replicas</span></div>
    </section>
  )
}

const tip = {
  contentStyle: { background: '#171d2a', border: `1px solid ${C.line}`, borderRadius: 8, fontSize: 12, color: C.ink },
  labelStyle: { color: C.muted }, itemStyle: { color: C.ink },
  labelFormatter: t => `t+${fmtT(t)}`,
}
const axis = { tick: { fill: C.muted, fontSize: 11 }, tickLine: false, axisLine: false }

function series(s, keys, n = 120) {
  const len = s.metrics.rps.length, start = Math.max(0, len - n)
  return Array.from({ length: len - start }, (_, i) => {
    const o = { t: s.t - (len - start - 1 - i) }
    for (const k of keys) o[k] = s.metrics[k][start + i]
    return o
  })
}

// Direct label at the last point of a series (dataviz: <= 4 series are direct-labelled).
const endLabel = (text, color, lastIndex) => ({ index, x, y }) =>
  index === lastIndex ? <text x={x + 6} y={y + 4} fill={color} fontSize={11} fontWeight={600}>{text}</text> : null

function Legend({ items }) {
  return (
    <div className="legend">
      {items.map(([label, color, dash]) => (
        <span key={label}><svg width="18" height="6" aria-hidden><line x1="0" y1="3" x2="18" y2="3" stroke={color} strokeWidth="2" strokeDasharray={dash} /></svg>{label}</span>
      ))}
    </div>
  )
}

function LoadChart({ s }) {
  const data = series(s, ['rps', 'replicas', 'hpa_replicas']).map(d => ({ ...d, cap: d.replicas * 50, hpaCap: d.hpa_replicas * 50 }))
  const ahead = s.forecast_ahead.map((v, i) => ({ t: s.t + i + 1, ahead: v }))
  if (data.length) ahead.unshift({ t: s.t, ahead: data.at(-1).rps })
  const all = [...data.slice(0, -1), { ...data.at(-1), ...(ahead[0] || {}) }, ...ahead.slice(1)]
  const last = data.length - 1
  const peak = Math.max(...s.forecast_ahead, 0)
  return (
    <section className="card" aria-labelledby="h-load">
      <h2 id="h-load"><Zap size={14} aria-hidden />Traffic vs capacity
        <span className="right muted">forecast peak <b className="mono">{peak.toFixed(0)} rps</b> in next 10s</span>
      </h2>
      <ResponsiveContainer width="100%" height={230}>
        <LineChart data={all} margin={{ top: 8, right: 62, left: -14, bottom: 0 }}>
          <CartesianGrid vertical={false} stroke={C.grid} />
          <XAxis dataKey="t" {...axis} minTickGap={48} tickFormatter={t => `${fmtT(t)}`} />
          <YAxis {...axis} width={44} />
          <Tooltip {...tip} formatter={(v, n) => [`${Math.round(v)} rps`, n]} />
          <ReferenceLine x={s.t} stroke={C.muted} strokeDasharray="2 3" label={{ value: 'now', fill: C.muted, fontSize: 10, position: 'insideTopRight' }} />
          <Line dataKey="hpaCap" name="Reactive HPA capacity" type="stepAfter" stroke={C.baseline} dot={false} strokeWidth={1.5} strokeDasharray="3 3" isAnimationActive={false} />
          <Line dataKey="cap" name="AEGIS capacity" type="stepAfter" stroke={C.aegis} dot={false} strokeWidth={2} isAnimationActive={false}>
            <LabelList content={endLabel('AEGIS', C.aegis, last)} />
          </Line>
          <Line dataKey="rps" name="Actual traffic" stroke={C.ink} dot={false} strokeWidth={2} isAnimationActive={false} />
          <Line dataKey="ahead" name="Forecast" stroke={C.forecast} dot={false} strokeWidth={2} strokeDasharray="5 3" isAnimationActive={false}>
            <LabelList content={endLabel('forecast', C.forecast, all.length - 1)} />
          </Line>
        </LineChart>
      </ResponsiveContainer>
      <Legend items={[['Actual traffic', C.ink], ['Forecast (+10s)', C.forecast, '5 3'], ['AEGIS capacity — scales ahead', C.aegis], ['Reactive HPA capacity — lags', C.baseline, '3 3']]} />
      <p className="sr-only">Current traffic {data.at(-1)?.rps?.toFixed(0)} requests per second; AEGIS capacity {data.at(-1)?.cap}; reactive HPA capacity {data.at(-1)?.hpaCap}.</p>
    </section>
  )
}

function LatencyChart({ s }) {
  const data = series(s, ['p95_latency_ms', 'hpa_latency_ms'])
  const now = data.at(-1)?.p95_latency_ms ?? 0
  const over = now > 500
  return (
    <section className="card" aria-labelledby="h-lat">
      <h2 id="h-lat"><Gauge size={14} aria-hidden />p95 latency
        <span className="right"><span className={`pill ${over ? 'p-bad' : 'p-good'}`}>{over ? <AlertTriangle size={11} /> : <CheckCircle2 size={11} />} now {Math.round(now)} ms</span></span>
      </h2>
      <ResponsiveContainer width="100%" height={180}>
        <LineChart data={data} margin={{ top: 8, right: 62, left: -14, bottom: 0 }}>
          <CartesianGrid vertical={false} stroke={C.grid} />
          <XAxis dataKey="t" {...axis} minTickGap={48} tickFormatter={fmtT} />
          <YAxis {...axis} width={44} domain={[0, m => Math.max(700, Math.ceil(m / 100) * 100)]} />
          <Tooltip {...tip} formatter={(v, n) => [`${Math.round(v)} ms`, n]} />
          <ReferenceLine y={500} stroke={C.bad} strokeDasharray="4 4" label={{ value: 'SLO 500ms', fill: C.bad, fontSize: 10, position: 'insideTopLeft' }} />
          <Line dataKey="hpa_latency_ms" name="Reactive HPA" stroke={C.baseline} dot={false} strokeWidth={1.5} strokeDasharray="3 3" isAnimationActive={false} />
          <Line dataKey="p95_latency_ms" name="AEGIS" stroke={C.aegis} dot={false} strokeWidth={2} isAnimationActive={false}>
            <LabelList content={endLabel('AEGIS', C.aegis, data.length - 1)} />
          </Line>
        </LineChart>
      </ResponsiveContainer>
      <Legend items={[['AEGIS', C.aegis], ['Reactive HPA (shadow baseline)', C.baseline, '3 3']]} />
    </section>
  )
}

function IncidentCard({ inc, now, act, busy, pinned, unpin }) {
  if (!inc) return (
    <section className="card incident-hero ok" aria-labelledby="h-inc">
      <h2 id="h-inc"><Brain size={14} aria-hidden />AI root-cause analysis</h2>
      <div className="empty"><CheckCircle2 size={28} color={C.good} aria-hidden />
        <b>No incidents</b>
        <span>Inject a fault (press <kbd>1</kbd>) to watch AEGIS detect, explain and repair it.</span>
      </div>
    </section>
  )
  const d = inc.diagnosis, dec = inc.decision
  const elapsed = inc.mttr ?? (now - inc.started)
  const [label, cls, Icon] = STATUS[inc.status]
  const blocked = inc.status === 'blocked'
  const fixKey = `/api/incident/${inc.id}/resolve`
  return (
    <section aria-labelledby="h-inc" aria-live="polite" className={`card incident-hero ${blocked ? 'blocked' : inc.status === 'resolved' ? 'ok' : OPEN.includes(inc.status) ? 'live' : ''}`}>
      <h2 id="h-inc"><Brain size={14} aria-hidden />AI root-cause analysis
        <span className="right">{pinned ? <button className="link" onClick={unpin}>Follow live <ChevronRight size={12} /></button> : <span className="mono">#{inc.id}</span>}</span>
      </h2>
      <div className="inc-head">
        <span className="svc">{inc.service}</span>
        <span className={`pill ${cls}`}><Icon size={11} aria-hidden /> {label}</span>
        <span className="timer mono" title={inc.mttr != null ? 'Time to recover' : 'Elapsed since detection'}>{elapsed}<small>s</small></span>
      </div>
      <Pipeline inc={inc} />
      <div className="signal"><AlertTriangle size={12} aria-hidden /> {inc.signal}</div>
      {d ? (
        <div className="explain">
          <div className="who"><Brain size={12} aria-hidden />{d.root_cause}<span className="mono model">{d.model}</span></div>
          <p>{d.explanation}</p>
        </div>
      ) : (
        <div className="explain loading"><div className="who"><Brain size={12} aria-hidden />Analysing…</div>
          <div className="sk-line" /><div className="sk-line" style={{ width: '80%' }} /><div className="sk-line" style={{ width: '55%' }} />
        </div>
      )}
      {dec && (
        <div className={`decision ${dec.allowed ? 'allow' : 'block'}`}>
          {dec.allowed ? <ShieldCheck color={C.good} size={22} aria-hidden /> : <ShieldX color={C.bad} size={22} aria-hidden />}
          <div>
            <div className="t">{dec.allowed ? 'Allowed' : 'Blocked by guardrail'} · <span className="mono">{dec.action}</span> <Level l={dec.level} /></div>
            <div className="r">{dec.reason}</div>
          </div>
        </div>
      )}
      {(blocked || inc.status === 'escalated') && (
        <div className="handoff">
          <span>Needs a human. AEGIS won’t take this action on its own.</span>
          <button className="btn primary" disabled={busy === fixKey} onClick={() => act(fixKey, null, `Incident #${inc.id} resolved by engineer`, fixKey)}><UserCheck size={14} />Apply safe fix</button>
        </div>
      )}
      <details className="tl" open={OPEN.includes(inc.status)}>
        <summary>Timeline · {inc.timeline.length} events</summary>
        <ol className="timeline mono">
          {inc.timeline.map((e, i) => <li key={i}><span>+{e.t - inc.started}s</span><span>{e.msg}</span></li>)}
        </ol>
      </details>
    </section>
  )
}

const Level = ({ l }) => <span className="lvl-pill" style={{ '--c': LEVELS[l][1] }} title={`${l}: ${LEVELS[l][0]}`}>{l} · {LEVELS[l][0]}</span>

const STAGES = ['Detect', 'Explain', 'Guard', 'Act', 'Healed']
function Pipeline({ inc }) {
  const idx = { detected: 0, diagnosing: 1, repairing: 3, resolved: 5, blocked: 2, escalated: 2 }[inc.status]
  const failed = inc.status === 'blocked' || inc.status === 'escalated'
  return (
    <ol className="pipeline" aria-label={`Pipeline stage: ${STATUS[inc.status][0]}`}>
      {STAGES.map((n, i) => {
        const st = i < idx ? 'done' : i === idx ? (failed ? 'fail' : 'now') : ''
        return <li key={n} className={`stage ${st}`} aria-current={st === 'now' || st === 'fail' ? 'step' : undefined}>
          {i === idx && failed ? (inc.status === 'blocked' ? 'Blocked' : 'Human') : n}
        </li>
      })}
    </ol>
  )
}

function IncidentFeed({ incidents, focusId, pick }) {
  return (
    <section className="card" aria-labelledby="h-feed">
      <h2 id="h-feed">Incident history <span className="right muted">{incidents.length} total</span></h2>
      <div className="feed">
        {incidents.length === 0 && <div className="empty small">Incidents will appear here.</div>}
        {incidents.map(i => {
          const [label, cls, Icon] = STATUS[i.status]
          return (
            <button className={`row rowbtn ${i.id === focusId ? 'sel' : ''}`} key={i.id} onClick={() => pick(i.id)} aria-pressed={i.id === focusId}>
              <span className="t mono">#{i.id}</span>
              <span className="grow"><b>{i.service}</b> <span className="muted">· {i.diagnosis?.root_cause ?? i.signal}</span></span>
              <span className="mono muted">{i.mttr != null ? `${i.mttr}s` : ''}</span>
              <span className={`pill ${cls}`}><Icon size={11} aria-hidden /> {label}</span>
            </button>
          )
        })}
      </div>
    </section>
  )
}

function ActionLog({ actions }) {
  const blocked = actions.filter(a => !a.allowed).length
  return (
    <section className="card" aria-labelledby="h-log">
      <h2 id="h-log"><Shield size={14} aria-hidden />Guardrail action log
        <span className="right muted">{actions.length} proposed · {blocked} blocked</span>
      </h2>
      <div className="feed" style={{ maxHeight: 260 }}>
        {actions.length === 0 ? <div className="empty small">Every repair the AI proposes is classified L0–L4 and logged here.</div> : (
          <table>
            <thead><tr><th scope="col">Time</th><th scope="col">Blast radius</th><th scope="col">Action</th><th scope="col">Verdict</th></tr></thead>
            <tbody>
              {actions.map((a, i) => (
                <tr key={i} className={a.allowed ? '' : 'blocked'}>
                  <td className="mono muted">t+{fmtT(a.t)}</td>
                  <td><Level l={a.level} /></td>
                  <td><span className="mono">{a.action}</span> <span className="mono muted">→ {a.target}</span>
                    {!a.allowed && <div className="why">{a.reason}</div>}
                  </td>
                  <td>{a.allowed
                    ? <span className="pill p-good"><ShieldCheck size={11} aria-hidden /> {a.mode}</span>
                    : <span className="pill p-bad"><ShieldX size={11} aria-hidden /> blocked</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </section>
  )
}

function ExpBar({ title, data, unit, hero, heroSub }) {
  return (
    <figure className="exp">
      <figcaption>
        <div className="exp-title">{title}</div>
        <div className="exp-hero">{hero}</div>
        <div className="muted small">{heroSub}</div>
      </figcaption>
      <ResponsiveContainer width="100%" height={190}>
        <BarChart data={data} margin={{ top: 10, right: 8, left: -14, bottom: 4 }} barCategoryGap="28%">
          <CartesianGrid vertical={false} stroke={C.grid} />
          <XAxis dataKey="name" {...axis} interval={0} height={34}
            tick={({ x, y, payload, index }) => (
              <g transform={`translate(${x},${y + 4})`}>
                <text textAnchor="middle" fill={C.muted} fontSize={11} dy={8}>{payload.value}</text>
                <text textAnchor="middle" fill={data[index]?.aegis ? C.ink : C.ink2} fontSize={12} fontWeight={600} dy={23}>{data[index]?.v}{unit}</text>
              </g>
            )} />
          <YAxis {...axis} width={44} domain={[0, m => niceMax(m)]} allowDataOverflow tickCount={5} />
          <Tooltip {...tip} labelFormatter={l => l} cursor={{ fill: '#ffffff08' }} formatter={(v, _, p) => [`${v}${unit} ± ${p.payload.std}${unit}`, 'mean ± std (5 seeds)']} />
          <Bar dataKey="v" radius={[4, 4, 0, 0]} isAnimationActive={false}>
            {data.map((d, i) => <Cell key={i} fill={d.aegis ? C.aegis : C.baseline} fillOpacity={d.aegis ? 1 : 0.55} />)}
            <ErrorBar dataKey="std" width={6} stroke={C.ink2} strokeWidth={1.5} />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </figure>
  )
}

function Experiments({ results: r, running, run }) {
  const pct = x => Math.round((x ?? 0) * 100)
  return (
    <section className="card exp-card" aria-labelledby="h-exp" aria-busy={running}>
      <h2 id="h-exp"><FlaskConical size={14} aria-hidden />Experiment matrix
        <span className="right"><span className="muted small hide-sm">5 seeds · mean ± std · simulator</span>
          <button className="btn primary sm" disabled={running} onClick={run}>{running ? <><span className="spin" aria-hidden />Running…</> : 'Run experiments'}</button>
        </span>
      </h2>
      {!r ? <div className="empty small">No results yet. Run the matrix to generate <code>results.csv</code> and the paper’s figures.</div> : (
        <div className="exp-grid">
          <ExpBar title="E1 · Recovery time" unit="s"
            hero={<>{r.E1.k8s.mttr.mean}s <span className="arrow">→</span> {r.E1.aegis.mttr.mean}s</>}
            heroSub="MTTR, default Kubernetes → AEGIS"
            data={[
              { name: 'No automation', v: r.E1.none.mttr.mean, std: r.E1.none.mttr.std },
              { name: 'K8s self-heal', v: r.E1.k8s.mttr.mean, std: r.E1.k8s.mttr.std },
              { name: 'AEGIS', v: r.E1.aegis.mttr.mean, std: r.E1.aegis.mttr.std, aegis: true },
            ]} />
          <ExpBar title="E3 · Traffic surge" unit="s"
            hero={<>{r.E3.hpa.slo_violation_s.mean}s <span className="arrow">→</span> {r.E3.aegis.slo_violation_s.mean}s</>}
            heroSub={`SLO violation · ${r.E3.aegis.avg_replicas.mean} vs ${r.E3.hpa.avg_replicas.mean} avg replicas`}
            data={[
              { name: 'Reactive HPA', v: r.E3.hpa.slo_violation_s.mean, std: r.E3.hpa.slo_violation_s.std },
              { name: 'AEGIS predictive', v: r.E3.aegis.slo_violation_s.mean, std: r.E3.aegis.slo_violation_s.std, aegis: true },
            ]} />
          <ExpBar title="E4 · Safety" unit="%"
            hero={<>{pct(r.E4.unguarded.unsafe_executed.mean)}% <span className="arrow">→</span> {pct(r.E4.guarded.unsafe_executed.mean)}%</>}
            heroSub={`Destructive actions executed · proposed in ${pct(r.E4.unguarded.unsafe_proposed.mean)}% of incidents`}
            data={[
              { name: 'Unguarded', v: pct(r.E4.unguarded.unsafe_executed.mean), std: pct(r.E4.unguarded.unsafe_executed.std) },
              { name: 'With guardrail', v: pct(r.E4.guarded.unsafe_executed.mean), std: pct(r.E4.guarded.unsafe_executed.std), aegis: true },
            ]} />
        </div>
      )}
    </section>
  )
}
