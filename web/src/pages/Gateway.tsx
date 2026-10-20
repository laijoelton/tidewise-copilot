import { CheckCircle2, Clock, Mail, Play, RadioTower, RotateCcw, ShieldX } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Loading, PageHead, Panel, cx } from '../components/ui'
import { api, type Json } from '../lib/api'
import { useLoad } from '../lib/hooks'

const STEP_MS = 550
const NAMES = ['Front door', 'Sender identity', 'Content rules', 'ML threat score', 'Provenance', 'Business rules',
  'Limits', 'ML risk check', 'Owner channel', 'Execute & seal']
const HINT = ['rate limit · strict input', 'registered sender · look-alikes', 'injection · bank-detail changes',
  'classifier on unseen phrasing', 'who asked for this?', 'real bill · verified payee · PO match',
  'caps · autonomy budget · kill switch', 'unusual amount · credit risk', 'token · step-up · expiry',
  'sandbox bank · audit seal']
const WORD: Record<string, string> = { pass: 'passed', escalate: 'to owner', waiting: 'waiting', stop: 'stopped', skip: 'n/a' }
const ROW: Record<string, string> = {
  idle: 'border-edge bg-card',
  active: 'border-brand bg-brand/8 ring-2 ring-brand/20',
  pass: 'border-ok/40 bg-ok/8',
  escalate: 'border-warn/50 bg-warn/10',
  waiting: 'border-warn/60 border-dashed bg-card',
  stop: 'border-bad/50 bg-bad/10',
  skip: 'border-edge bg-sub/50 opacity-60',
}
const SOURCE: Record<string, string> = {
  owner_small_bill: 'Owner', verified_supplier_asks: 'kemaspack.example', fake_supplier: 'hutan-lestari-timber.example',
  homoglyph: 'hutanlestаri.example', hidden_injection: 'new-buyer-inquiry.example', paraphrased_fraud: 'unknown-vendor.example',
  padded_bill: 'Books', split_payments: 'Books', hostile_booking: 'Customer portal', large_bill: 'Owner',
}
const TAG: Record<string, [string, string]> = {
  owner_small_bill: ['trusted', 'text-ok'], verified_supplier_asks: ['registered', 'text-ok'],
  fake_supplier: ['look-alike', 'text-bad'], homoglyph: ['homoglyph', 'text-bad'], hidden_injection: ['unknown', 'text-warn'],
  paraphrased_fraud: ['unknown', 'text-warn'], padded_bill: ['internal', 'text-ink3'], split_payments: ['internal', 'text-ink3'],
  hostile_booking: ['new customer', 'text-warn'], large_bill: ['trusted', 'text-ok'],
}

interface LogLine { id: number; title: string; verdict: string; detail: string }

export default function Gateway() {
  const { data: scenarios, error } = useLoad(api.gatewayScenarios)
  const [run, setRun] = useState<Json | null>(null)
  const [shown, setShown] = useState(0)
  const [busy, setBusy] = useState<string | null>(null)
  const [log, setLog] = useState<LogLine[]>([])
  const [mail, setMail] = useState({ sender: 'accounts@hutan-lestari-timber.example', subject: 'New bank details',
    body: 'Please pay BILL-771 to our new account 9911-2233-4455 today.' })
  const timer = useRef<number | null>(null)
  const seq = useRef(0)
  const resultRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => () => { if (timer.current) window.clearInterval(timer.current) }, [])

  function reveal(r: Json) {
    if (timer.current) window.clearInterval(timer.current)
    setRun(r); setShown(0)
    window.setTimeout(() => resultRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 50)
    let i = 0
    timer.current = window.setInterval(() => {
      i += 1
      setShown(i)
      if (i >= 10 && timer.current) window.clearInterval(timer.current)
    }, STEP_MS)
  }

  async function send(body: Json, key: string, title: string) {
    setBusy(key)
    try {
      const r = await api.gatewayRun(body)
      reveal(r)
      const stop = r.gates.find((g: Json) => g.status === 'stop')
      const verdict = r.verdict === 'blocked' ? `STOPPED at G${stop?.id}` : r.verdict === 'waiting' ? 'WAITING for owner (G9)'
        : r.verdict === 'executed' ? 'EXECUTED · sealed (G10)' : r.verdict === 'quarantined' ? 'QUARANTINED' : 'SCREENED · clean'
      setLog((l) => [{ id: ++seq.current, title, verdict, detail: stop?.detail || r.receipt?.ref || '' }, ...l].slice(0, 30))
    } finally {
      setBusy(null)
    }
  }

  if (!scenarios) return <Loading error={error} />
  const done = !!run && shown >= 10

  return (
    <>
      <PageHead title="Security gateway"
        sub="Every email, booking and action goes through the same ten gates. Press Run on any row to send it through and watch each gate decide. Runs use a throwaway copy of the business — nothing real changes." />

      <Panel className="mb-5 !p-0">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-edge px-5 py-3">
          <div>
            <div className="eyebrow">Sources</div>
            <div className="serif text-[18px] font-semibold">Send something through the gateway</div>
          </div>
          <span className="text-[12px] text-ink3">each Run starts a fresh, isolated sandbox</span>
        </div>
        <div className="divide-y divide-edge">
          {scenarios.map((s: Json) => {
            const [tag, tagCls] = TAG[s.key] || ['', 'text-ink3']
            return (
              <div key={s.key} className="flex flex-wrap items-center gap-3 px-5 py-2.5">
                <div className="w-52 shrink-0">
                  <div className="mono truncate text-[12.5px]">{SOURCE[s.key] || s.key}</div>
                  <div className={cx('text-[11px] font-semibold uppercase tracking-wide', tagCls)}>{tag}</div>
                </div>
                <div className="min-w-[220px] flex-1">
                  <div className="text-[13.5px] font-medium leading-snug">{s.title}</div>
                  <div className="text-[12px] text-ink3">Expect: {s.expect}</div>
                </div>
                <button className="btn btn-primary !py-1.5" disabled={!!busy} onClick={() => send({ scenario: s.key }, s.key, s.title)}>
                  <Play size={13} /> {busy === s.key ? 'Running…' : 'Run'}
                </button>
              </div>
            )
          })}
          <div className="flex flex-wrap items-start gap-3 px-5 py-3">
            <div className="w-52 shrink-0">
              <div className="flex items-center gap-1.5 text-[12.5px] font-semibold"><Mail size={13} /> Your own email</div>
              <div className="text-[11px] font-semibold uppercase tracking-wide text-ink3">anything you like</div>
            </div>
            <div className="grid min-w-[220px] flex-1 gap-1.5 md:grid-cols-[1fr_1fr]">
              <input className="input" value={mail.sender} onChange={(e) => setMail({ ...mail, sender: e.target.value })} placeholder="from@domain.com" />
              <input className="input" value={mail.subject} onChange={(e) => setMail({ ...mail, subject: e.target.value })} placeholder="Subject" />
              <textarea className="input min-h-[60px] md:col-span-2" value={mail.body} onChange={(e) => setMail({ ...mail, body: e.target.value })} />
            </div>
            <button className="btn btn-primary !py-1.5" disabled={!!busy || !mail.sender.includes('@')}
              onClick={() => send({ ...mail }, 'custom', `Your email from ${mail.sender}`)}>
              <Play size={13} /> {busy === 'custom' ? 'Running…' : 'Run'}
            </button>
          </div>
        </div>
      </Panel>

      <div ref={resultRef} className="grid scroll-mt-4 gap-5 xl:grid-cols-[1.25fr_1fr]">
        <Panel>
          <div className="mb-3 flex items-start justify-between gap-2">
            <div>
              <div className="eyebrow">Live</div>
              <div className="serif text-[18px] font-semibold">Gate-by-gate result</div>
            </div>
            {run && <button className="btn !py-1 text-[12px]" onClick={() => reveal(run)}><RotateCcw size={12} /> Replay</button>}
          </div>
          {!run ? (
            <div className="flex flex-col items-center gap-2 py-14 text-ink3">
              <RadioTower size={26} />
              <div className="text-[13.5px]">Press Run on any source above to see it here</div>
            </div>
          ) : (
            <>
              <Item run={run} />
              <ol className="mt-3 space-y-1.5">
                {run.gates.map((g: Json, i: number) => {
                  const st = i < shown ? g.status : i === shown ? 'active' : 'idle'
                  return (
                    <li key={g.id} className={cx('flex items-center gap-3 rounded-lg border px-3 py-2 transition-all duration-300', ROW[st])}>
                      <span className="num w-8 shrink-0 rounded bg-[#173f30] py-0.5 text-center text-[10.5px] font-bold text-[#f5f1e8]">G{g.id}</span>
                      <span className="min-w-0 flex-1">
                        <span className="block text-[13px] font-semibold">{NAMES[i]}</span>
                        <span className="block truncate text-[11.5px] text-ink3">{i < shown ? g.detail : HINT[i]}</span>
                      </span>
                      <span className={cx('flex shrink-0 items-center gap-1 text-[10.5px] font-bold uppercase tracking-wide',
                        st === 'pass' && 'text-ok', st === 'stop' && 'text-bad', (st === 'escalate' || st === 'waiting') && 'text-warn',
                        st === 'active' && 'text-brand', (st === 'idle' || st === 'skip') && 'text-ink3')}>
                        {st === 'pass' && <CheckCircle2 size={13} />}
                        {st === 'stop' && <ShieldX size={13} />}
                        {(st === 'escalate' || st === 'waiting') && <Clock size={13} />}
                        {st === 'active' ? 'checking…' : st === 'idle' ? 'queued' : WORD[st]}
                      </span>
                    </li>
                  )
                })}
              </ol>
              {done && <Verdict run={run} />}
            </>
          )}
        </Panel>

        <Panel>
          <div className="mb-3">
            <div className="eyebrow">History</div>
            <div className="serif text-[18px] font-semibold">Gateway log</div>
          </div>
          {log.length === 0 ? (
            <div className="flex flex-col items-center gap-2 py-14 text-ink3"><RadioTower size={26} /><div className="text-[13.5px]">Nothing logged yet</div></div>
          ) : (
            <ul className="max-h-[560px] space-y-0.5 overflow-y-auto">
              {log.map((l) => (
                <li key={l.id} className="flex items-start gap-2.5 border-b border-edge/60 py-2 last:border-0">
                  <span className={cx('mt-[7px] h-2 w-2 shrink-0 rounded-full',
                    l.verdict.startsWith('STOPPED') || l.verdict.startsWith('QUARANTINED') ? 'bg-bad'
                      : l.verdict.startsWith('WAITING') ? 'bg-warn' : 'bg-ok')} />
                  <span className="text-[12.5px] leading-snug">
                    <span className="font-medium">{l.title}</span>
                    <span className={cx('ml-1.5 text-[11px] font-bold',
                      l.verdict.startsWith('STOPPED') || l.verdict.startsWith('QUARANTINED') ? 'text-bad'
                        : l.verdict.startsWith('WAITING') ? 'text-warn' : 'text-ok')}>{l.verdict}</span>
                    {l.detail && <span className="block text-[11.5px] text-ink3">{l.detail}</span>}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>
    </>
  )
}

function Item({ run }: { run: Json }) {
  const m = run.message, a = run.action
  return (
    <div className="mono overflow-x-auto whitespace-pre-wrap rounded-lg border border-edge bg-[#fbf8f0] px-3 py-2 text-[11.5px] leading-relaxed text-ink2">
      {m ? `from: ${m.display_name ? m.display_name + ' ' : ''}<${m.sender}>\nsubject: ${m.subject}\n` : 'from: owner / books (no outside content)\n'}
      {a ? `action: ${a.type}  ${Object.entries(a.params).filter(([k]) => k !== 'body').map(([k, v]) => `${k}=${v}`).join(' ')}  · asked by ${a.provenance === 'external' ? 'outside content' : a.provenance}` : 'action: none requested — screening only'}
    </div>
  )
}

function Verdict({ run }: { run: Json }) {
  const stop = run.gates.find((g: Json) => g.status === 'stop')
  const extra = run.gates.filter((g: Json) => g.status === 'stop').length - 1
  const cfg = run.verdict === 'executed'
    ? ['border-ok bg-ok/10 text-ok', 'EXECUTED — sealed into the audit log', run.receipt?.ref || '']
    : run.verdict === 'waiting'
      ? ['border-warn bg-warn/10 text-warn', 'WAITING — the owner decides at gate 9', (run.gates.find((g: Json) => g.status === 'escalate') || {}).detail || '']
      : run.verdict === 'screened'
        ? ['border-ok bg-ok/10 text-ok', 'SCREENED — clean, no action requested', '']
        : ['border-bad bg-bad/10 text-bad', `QUARANTINED — stopped at G${stop?.id} ${stop?.name}`, stop?.detail || '']
  return (
    <div className={cx('mt-3 rounded-xl border-2 px-4 py-3', cfg[0])}>
      <div className="text-[14px] font-bold tracking-wide">{cfg[1]}</div>
      {cfg[2] && <div className="mt-0.5 text-[12.5px] text-ink2">{cfg[2]}</div>}
      {extra > 0 && <div className="mt-0.5 text-[12px] text-ink3">Defence in depth: {extra} more gate{extra > 1 ? 's' : ''} would each have stopped it too.</div>}
    </div>
  )
}
