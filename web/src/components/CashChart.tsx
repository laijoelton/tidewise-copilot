import { Bar, CartesianGrid, ComposedChart, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { Json } from '../lib/api'
import { rm, shortDate } from '../lib/api'
import { C, tick, tooltipStyle } from '../lib/theme'

const NAMES: Record<string, string> = { closing: 'Closing balance', alt: 'What-if balance', inflow: 'Money in', outflow: 'Money out' }

export function CashChart({ weekly, buffer, compare, height = 280 }: { weekly: Json[]; buffer: number; compare?: Json[]; height?: number }) {
  const rows = weekly.map((w, i) => ({
    name: shortDate(w.start), closing: w.closing, inflow: w.inflow, outflow: -w.outflow,
    alt: compare ? compare[i]?.closing : undefined,
  }))
  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart data={rows} margin={{ top: 8, right: 8, left: 4, bottom: 0 }}>
        <CartesianGrid stroke={C.grid} vertical={false} />
        <XAxis dataKey="name" tick={tick} axisLine={false} tickLine={false} />
        <YAxis tick={tick} axisLine={false} tickLine={false} width={52} tickFormatter={(v: number) => `${Math.round(v / 1000)}k`} />
        <Tooltip contentStyle={tooltipStyle} formatter={(v, n) => [rm(Number(v)), NAMES[String(n)] || String(n)]} />
        <ReferenceLine y={0} stroke={C.bad} strokeOpacity={0.5} />
        <ReferenceLine y={buffer} stroke={C.warn} strokeDasharray="5 4" strokeOpacity={0.8}
          label={{ value: 'your floor', fill: C.warn, fontSize: 11, position: 'insideTopRight' }} />
        <Bar dataKey="inflow" fill={C.ok} fillOpacity={0.28} radius={[3, 3, 0, 0]} barSize={9} />
        <Bar dataKey="outflow" fill={C.bad} fillOpacity={0.22} radius={[0, 0, 3, 3]} barSize={9} />
        <Line type="monotone" dataKey="closing" stroke={C.brand} strokeWidth={2.5} dot={{ r: 2.5, fill: C.brand, strokeWidth: 0 }} />
        {compare && <Line type="monotone" dataKey="alt" stroke={C.warn} strokeWidth={2.2} strokeDasharray="6 4" dot={false} />}
      </ComposedChart>
    </ResponsiveContainer>
  )
}
