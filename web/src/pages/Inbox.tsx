import { Badge, Loading, PageHead, cx } from '../components/ui'
import { api, shortDate, type Json } from '../lib/api'
import { useLoad } from '../lib/hooks'

export default function Inbox() {
  const { data, error } = useLoad(api.inbox)
  if (!data) return <Loading error={error} />
  return (
    <>
      <PageHead title="Inbox guard" sub="Everything from outside is data, never instructions. Messages are screened for lookalike senders, payment-redirection fraud and prompt injection before the agent reads them; anything derived from a quarantined message is blocked by policy." />
      <div className="flex flex-col gap-3">
        {data.map((m: Json) => (
          <div key={m.message_id} className={cx('panel p-5', m.trust === 'quarantined' && 'border-bad/35')}>
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={m.trust}>{m.trust}</Badge>
              <span className="font-semibold">{m.subject}</span>
              <span className="ml-auto num text-[12px] text-ink3">{m.message_id} · {shortDate(m.received)}</span>
            </div>
            <div className="mt-1 text-[12.5px] text-ink3">
              {m.display_name} &lt;{m.sender}&gt;{m.counterparty && <> · registered as <span className="text-ink2">{m.counterparty}</span></>}
              {m.ml?.label && <> · ML: <span className={m.ml.threat >= 0.6 ? 'font-semibold text-bad' : 'text-ink2'}>{m.ml.label.replace('_', ' ')} {Math.round(m.ml.threat * 100)}% threat</span></>}
            </div>
            <div className="mt-3 whitespace-pre-line rounded-lg border border-edge bg-[#fbf8f0] p-3 text-[13px] text-ink2">{m.masked_body}</div>
            {m.findings.length > 0 && (
              <div className="mt-3 flex flex-col gap-1">
                {m.findings.map((f: Json, i: number) => (
                  <div key={i} className="flex items-start gap-2 text-[12.5px]">
                    <Badge tone={f.severity}>{f.check.replaceAll('_', ' ')}</Badge>
                    <span className="text-ink2">{f.detail}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </>
  )
}
