import { Bar, CartesianGrid, ComposedChart, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { Json } from '../lib/api'
import { rm, shortDate } from '../lib/api'

export function CashChart({ weekly, buffer, height = 280 }: { weekly: Json[]; buffer: number; height?: number }) {
  const rows = weekly.map((w) => ({ name: shortDate(w.start), closing: w.closing, inflow: w.inflow, outflow: -w.outflow }))
  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart data={rows} margin={{ top: 8, right: 8, left: 8, bottom: 0 }}>
        <CartesianGrid stroke="rgba(157,179,189,0.08)" vertical={false} />
        <XAxis dataKey="name" tick={{ fill: '#6a8592', fontSize: 11 }} axisLine={false} tickLine={false} />
        <YAxis tick={{ fill: '#6a8592', fontSize: 11 }} axisLine={false} tickLine={false} width={56}
          tickFormatter={(v: number) => `${Math.round(v / 1000)}k`} />
        <Tooltip
          contentStyle={{ background: '#0d1c25', border: '1px solid #24404f', borderRadius: 8, fontSize: 12 }}
          formatter={(v, n) => [rm(Number(v)), n === 'closing' ? 'Closing balance' : n === 'inflow' ? 'Money in' : 'Money out']}
        />
        <ReferenceLine y={0} stroke="#f87171" strokeOpacity={0.6} />
        <ReferenceLine y={buffer} stroke="#fbbf24" strokeDasharray="4 4" strokeOpacity={0.7}
          label={{ value: 'your floor', fill: '#fbbf24', fontSize: 11, position: 'insideTopRight' }} />
        <Bar dataKey="inflow" fill="#34d399" fillOpacity={0.35} radius={[3, 3, 0, 0]} barSize={10} />
        <Bar dataKey="outflow" fill="#f87171" fillOpacity={0.3} radius={[0, 0, 3, 3]} barSize={10} />
        <Line type="monotone" dataKey="closing" stroke="#2dd4bf" strokeWidth={2.5} dot={{ r: 2.5, fill: '#2dd4bf' }} />
      </ComposedChart>
    </ResponsiveContainer>
  )
}
