/** 时段右滑面板：仅覆盖中栏（左右栏常驻）。全天 96 点曲线 + 数值格（该小时 4 点可改标红、余 92 只读）、
 *  落点概率/期望/灰度量价（含意向录入：草稿本地编辑，点「确认并计算」才提交重算）、
 *  只读明细（该时段 空间/负荷率/预测电价 96×4 + 24 点对照）。 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { LineChart } from './LineChart'
import { TIME_LABELS_96, PERIOD_LABELS_24 } from './BoundarySection'
import { latestRevisionAt, originalBoundary, useWorkbench, type IntentEntry } from '../store'
import { BOUNDARY_KEYS, BOUNDARY_LABELS, HOUR_EXPAND_KEYS, type BoundaryKey } from '../calc/types'

const fmt = (v: number | null | undefined, d = 1) => (v === null || v === undefined ? '—' : v.toFixed(d))

function SlideCellEditor({ boundary, t, current, anchor, onClose }: { boundary: BoundaryKey; t: number; current: number | null; anchor: { left: number; top: number }; onClose: () => void }) {
  const left = Math.min(Math.max(anchor.left, 8), window.innerWidth - 248)   // 与格子左缘对齐：必在中栏内，不遮左栏
  const top = anchor.top + 190 > window.innerHeight ? Math.max(anchor.top - 196, 8) : anchor.top + 8
  const modifyBoundary = useWorkbench((s) => s.modifyBoundary)
  const [value, setValue] = useState(current === null ? '' : String(current))
  const [reason, setReason] = useState('')
  const [error, setError] = useState<string | null>(null)
  const period = Math.ceil(t / 4)
  const save = () => {
    const num = Number(value)
    if (!Number.isFinite(num)) { setError('请输入有效数值'); return }
    const err = modifyBoundary(boundary, period, [{ t, value: num }], reason)
    if (err) { setError(err); return }
    onClose()
  }
  return (
    <div className="fixed z-50 w-56 rounded-md border border-[#334155] bg-[#0E1223] p-3 shadow-lg" style={{ left, top }}>
      <div className="mb-2 text-xs text-[#94A3B8]">修改 {boundary} · t={t}（{TIME_LABELS_96[t - 1]}）</div>
      <input className="mono mb-2 w-full rounded border border-[#334155] bg-[#020617] px-2 py-1 text-sm text-[#F8FAFC] outline-none focus:border-[#3B82F6]" value={value} onChange={(e) => setValue(e.target.value)} placeholder="新值（MW）" autoFocus />
      <input className="mb-2 w-full rounded border border-[#334155] bg-[#020617] px-2 py-1 text-xs text-[#F8FAFC] outline-none focus:border-[#3B82F6]" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="修改理由（必填，不限字数）" />
      {error && <div id={`cell-edit-error`} role="alert" className="mb-2 text-xs text-[#EF4444]">{error}</div>}
      <div className="flex justify-end gap-2">
        <button onClick={onClose} className="cursor-pointer rounded px-2 py-1 text-xs text-[#94A3B8] hover:text-[#F8FAFC]">取消</button>
        <button onClick={save} className="cursor-pointer rounded bg-[#22C55E] px-3 py-1 text-xs font-semibold text-[#0F172A] hover:opacity-90">保存并重算</button>
      </div>
    </div>
  )
}

/** 意向录入（草稿态）：本地字符串编辑、可删空；点「确认并计算」才提交 store 并触发重算。
 *  外部变更（助手工具/他处确认）经 committed 比对后同步进草稿，自身确认的回显不覆盖未提交输入。 */
function IntentEditor({ period }: { period: number }) {
  const intent = useWorkbench((s) => s.intents[period])
  const setIntent = useWorkbench((s) => s.setIntent)
  const [draft, setDraft] = useState(() => ({
    list: String(intent?.listPrice ?? ''), lift: String(intent?.liftPrice ?? ''), vol: String(intent?.volume ?? ''),
  }))
  const committed = useRef<IntentEntry>({
    listPrice: intent?.listPrice ?? null, liftPrice: intent?.liftPrice ?? null, volume: intent?.volume ?? null,
  })
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const cur: IntentEntry = {
      listPrice: intent?.listPrice ?? null, liftPrice: intent?.liftPrice ?? null, volume: intent?.volume ?? null,
    }
    const last = committed.current
    if (cur.listPrice === last.listPrice && cur.liftPrice === last.liftPrice && cur.volume === last.volume) return
    committed.current = cur
    setDraft({ list: String(cur.listPrice ?? ''), lift: String(cur.liftPrice ?? ''), vol: String(cur.volume ?? '') })
  }, [intent])

  const dirty = draft.list !== String(committed.current.listPrice ?? '')
    || draft.lift !== String(committed.current.liftPrice ?? '')
    || draft.vol !== String(committed.current.volume ?? '')

  const confirm = () => {
    const patch: Partial<IntentEntry> = {}
    const fields: Array<[keyof typeof draft, keyof IntentEntry, string]> = [
      ['list', 'listPrice', '挂牌价'], ['lift', 'liftPrice', '摘牌价'], ['vol', 'volume', '交易量'],
    ]
    for (const [dk, ik, label] of fields) {
      const s = draft[dk].trim()
      if (s === '') { patch[ik] = null; continue }
      const n = Number(s)
      if (!Number.isFinite(n) || n <= 0) { setError(`${label}须为正数`); return }
      patch[ik] = n
    }
    setError(null)
    setIntent(period, patch)
    committed.current = {
      listPrice: patch.listPrice ?? null, liftPrice: patch.liftPrice ?? null, volume: patch.volume ?? null,
    }
  }

  const inputCls = 'mono ml-1 w-24 rounded border border-[#334155] bg-[#020617] px-2 py-1 text-[#F8FAFC] outline-none focus:border-[#3B82F6]'
  return (
    <div className="rounded-lg border border-[#334155] bg-[#020617] p-3">
      <div className="mb-2 flex flex-wrap items-center gap-3 text-[11px] text-[#94A3B8]">
        <label>挂牌价 <input type="number" className={inputCls} value={draft.list}
          onChange={(e) => setDraft((d) => ({ ...d, list: e.target.value }))}
          onKeyDown={(e) => e.key === 'Enter' && dirty && confirm()} /> 元/MWh</label>
        <label>摘牌价 <input type="number" className={inputCls} value={draft.lift}
          onChange={(e) => setDraft((d) => ({ ...d, lift: e.target.value }))}
          onKeyDown={(e) => e.key === 'Enter' && dirty && confirm()} /> 元/MWh</label>
        <label>交易量 <input type="number" className={inputCls} value={draft.vol}
          onChange={(e) => setDraft((d) => ({ ...d, vol: e.target.value }))}
          onKeyDown={(e) => e.key === 'Enter' && dirty && confirm()} /> MWh</label>
        <button onClick={confirm} disabled={!dirty}
          className={`rounded px-3 py-1 text-xs font-semibold transition-colors duration-150 ${
            dirty ? 'cursor-pointer bg-[#22C55E] text-[#0F172A] hover:opacity-90' : 'cursor-not-allowed bg-[#1A1E2F] text-[#94A3B8]/60'
          }`}>确认并计算</button>
        <span className="text-[10px] text-[#94A3B8]">{dirty ? '有未确认修改（不参与计算）' : '输入后需确认才重算'}</span>
      </div>
      {error && <div role="alert" className="text-xs text-[#EF4444]">{error}</div>}
    </div>
  )
}

export function PeriodSlidePanel({ period, onClose }: { period: number; onClose: () => void }) {
  const derived = useWorkbench((s) => s.derived)
  const revisions = useWorkbench((s) => s.revisions)
  const params = useWorkbench((s) => s.params)
  const intents = useWorkbench((s) => s.intents)
  const [boundary, setBoundary] = useState<BoundaryKey>('负荷')
  const [editing, setEditing] = useState<number | null>(null)
  const [editAnchor, setEditAnchor] = useState<{ left: number; top: number } | null>(null)
  const intent = intents[period]
  const hourIdx = Array.from({ length: 4 }, (_, j) => (period - 1) * 4 + j)   // 0-based

  const { base, effective, hasRevision } = useMemo(() => {
    const b = originalBoundary(boundary)
    const e = b.slice()
    for (const rev of revisions) {
      if (rev.boundary === boundary && !rev.rolledBack) e[rev.t - 1] = rev.newValue
    }
    return { base: b, effective: e, hasRevision: revisions.some((r) => r.boundary === boundary && !r.rolledBack) }
  }, [boundary, revisions])

  const spec = useMemo(() => ({
    labels: TIME_LABELS_96,
    markAreaIndex: [hourIdx[0], hourIdx[3]] as [number, number],
    yName: 'MW',
    height: 220,
    series: [
      { name: hasRevision ? '原值（披露）' : '披露值', data: base, color: '#3B82F6', faded: hasRevision },
      ...(hasRevision ? [{ name: '修订后', data: effective, color: '#22C55E' }] : []),
    ],
  }), [base, effective, hasRevision, hourIdx])

  const st = derived.landing[period - 1]
  const grey = derived.grey[period - 1]

  return (
    <div className="absolute inset-0 z-20 flex flex-col overflow-y-auto border-l border-[#334155] bg-[#0E1223]/98 pl-2 backdrop-blur-sm">
      <div className="sticky top-0 z-10 flex items-center gap-2 border-b border-[#334155] bg-[#0E1223] px-3 py-2">
        <span className="text-sm font-semibold text-[#EF4444]">时段 {period}（{PERIOD_LABELS_24[period - 1]}）</span>
        <span className="text-[10px] text-[#94A3B8]">该小时 4 个 96 点可改（红），其余 92 点只读</span>
        <button onClick={onClose} className="ml-auto cursor-pointer rounded border border-[#334155] px-2 py-0.5 text-xs text-[#94A3B8] hover:border-[#EF4444] hover:text-[#EF4444]">收起 ✕</button>
      </div>

      <div className="space-y-3 p-3">
        {/* 可改边界：全天 96 点，仅该小时 4 点可改 */}
        <section className="rounded-lg border border-[#334155] bg-[#020617] p-2">
          <div className="mb-2 flex flex-wrap items-center gap-1">
            <span className="mr-1 text-[11px] font-semibold text-[#94A3B8]">可改边界（全天 96 点）</span>
            {HOUR_EXPAND_KEYS.includes(boundary) && (
              <span className="rounded bg-[#22C55E]/15 px-1.5 py-0.5 text-[10px] text-[#22C55E]">24 点输入（正=买入/受入，负=卖出/送出）：编辑该小时任一点即 4 点同值</span>
            )}
            {BOUNDARY_KEYS.map((k) => (
              <button
                key={k}
                onClick={() => { setBoundary(k); setEditing(null) }}
                className={`cursor-pointer rounded px-1.5 py-0.5 text-[10px] transition-colors duration-150 ${
                  boundary === k ? 'bg-[#3B82F6] font-semibold text-white' : 'text-[#94A3B8] hover:bg-[#1A1E2F] hover:text-[#F8FAFC]'
                }`}
              >
                {BOUNDARY_LABELS[k] ?? k}
              </button>
            ))}
          </div>
          <LineChart spec={spec} />
          <div className="relative mt-2 grid grid-cols-12 gap-px">
            {effective.map((v, i) => {
              const t = i + 1
              const inHour = hourIdx.includes(i)
              const rev = latestRevisionAt(revisions, boundary, t)
              const editable = inHour && editing === t
              return (
                <div key={t} className="relative">
                  <button
                    onClick={(e) => {
                      if (!inHour) return
                      if (editing === t) { setEditing(null); return }
                      const r = (e.currentTarget as HTMLElement).getBoundingClientRect()
                      setEditAnchor({ left: r.left, top: r.bottom })
                      setEditing(t)
                    }}
                    disabled={!inHour}
                    className={`mono w-full px-1 py-1 text-left text-[10px] ${
                      editable ? 'bg-[#EF4444]/25 text-[#F8FAFC] ring-1 ring-[#EF4444]'
                        : rev ? 'bg-[#22C55E]/15 text-[#22C55E]'
                        : inHour ? 'bg-[#EF4444]/10 text-[#F8FAFC]'
                        : 'cursor-not-allowed bg-[#1A1E2F] text-[#94A3B8]/60'
                    } ${inHour ? 'cursor-pointer' : ''}`}
                    title={inHour ? '该小时 4 点可改（标红）' : '其余 92 点只读（防误改其它时段）'}
                  >
                    {v === null ? '缺' : v >= 10000 ? (v / 1000).toFixed(1) + 'k' : v.toFixed(0)}
                  </button>
                  {editable && editAnchor && <SlideCellEditor boundary={boundary} t={t} current={v} anchor={editAnchor} onClose={() => setEditing(null)} />}
                </div>
              )
            })}
          </div>
        </section>

        {/* 落点概率 / 期望 / 灰度量价（自中栏底部移入；意向录入草稿+确认后重算） */}
        <section className="rounded-lg border border-[#334155] bg-[#020617] p-3">
          <div className="mb-2 flex flex-wrap items-center gap-2 text-[11px]">
            <span className="font-semibold text-[#94A3B8]">落点概率 / 期望 / 灰度量价（24 点 · 本时段）</span>
            <span className="rounded bg-[#A855F7]/20 px-1.5 py-0.5 text-[10px] font-semibold text-[#A855F7]">输出·只读</span>
          </div>
          <IntentEditor key={period} period={period} />
          <div className="mt-3 grid grid-cols-2 gap-3">
            <div>
              <div className="mb-1 text-[11px] font-semibold text-[#F8FAFC]">实时出清价落档（相似运行日 ±{(params.相似日阈值 * 100).toFixed(0)}%）</div>
              {st.enough ? (
                <table className="w-full text-[10px]">
                  <thead>
                    <tr className="text-[#94A3B8]">
                      <th className="py-0.5 text-left font-normal">档位（元/MWh）</th>
                      <th className="text-right font-normal">分子/分母</th>
                      <th className="text-right font-normal">概率</th>
                    </tr>
                  </thead>
                  <tbody className="mono">
                    {st.bins.filter((b) => b.count > 0).map((b) => (
                      <tr key={b.lo} className={`border-t border-[#1A1E2F] ${(b.lo === params.现货下限 || b.hi === params.现货上限) ? 'text-[#22C55E]' : 'text-[#F8FAFC]'}`}>
                        <td className="py-0.5">[{b.lo}, {b.hi}]</td>
                        <td className="text-right text-[#94A3B8]">{b.count}/{st.n}</td>
                        <td className="text-right">{(b.prob * 100).toFixed(1)}%</td>
                      </tr>
                    ))}
                    <tr className="border-t border-[#334155] font-semibold text-[#A855F7]">
                      <td className="py-1">电价期望 = Σ(档中值×概率)</td>
                      <td />
                      <td className="text-right">{fmt(st.expectation)}</td>
                    </tr>
                  </tbody>
                </table>
              ) : (
                <div className="rounded border border-dashed border-[#334155] p-3 text-center text-[11px] text-[#94A3B8]">样本不足（N={st.n} &lt; 3，不出概率）</div>
              )}
              <div className="mt-1 text-[10px] text-[#94A3B8]">统计范围：{st.scope}｜成员：{st.members.map((d) => d.slice(5)).join('、') || '无'}</div>
            </div>

            <div>
              <div className="mb-1 text-[11px] font-semibold text-[#F8FAFC]">灰度量价（意向价 − 贴限首/末档 × 量）</div>
              {grey.evaluated ? (
                <div className="space-y-1.5 text-[10px]">
                  <div className="rounded border border-[#334155] bg-[#020617] p-2">
                    <div className="text-[#94A3B8]">最低落点档 [{grey.lowestBin.lo}, {grey.lowestBin.hi}]（概率 {(grey.probRisk * 100).toFixed(1)}%）｜最高落点档 [{grey.highestBin.lo}, {grey.highestBin.hi}]（概率 {(grey.probGain * 100).toFixed(1)}%）</div>
                  </div>
                  <div className="rounded border border-[#22C55E]/40 bg-[#22C55E]/5 p-2">
                    <div className="text-[#22C55E]">最大收益（卖方视角·贴上限）：{fmt(grey.maxGainPrice?.[0])} ~ {fmt(grey.maxGainPrice?.[1])} 元/MWh × {fmt(intent?.volume ?? null, 0)} MWh = <span className="font-semibold">{fmt(grey.maxGainAmount?.[0], 0)} ~ {fmt(grey.maxGainAmount?.[1], 0)} 元</span>（概率 {(grey.probGain * 100).toFixed(1)}%）</div>
                  </div>
                  <div className="rounded border border-[#EF4444]/40 bg-[#EF4444]/5 p-2">
                    <div className="text-[#EF4444]">最大风险（卖方视角·贴下限）：{fmt(grey.maxRiskPrice?.[0])} ~ {fmt(grey.maxRiskPrice?.[1])} 元/MWh × {fmt(intent?.volume ?? null, 0)} MWh = <span className="font-semibold">{fmt(grey.maxRiskAmount?.[0], 0)} ~ {fmt(grey.maxRiskAmount?.[1], 0)} 元</span>（概率 {(grey.probRisk * 100).toFixed(1)}%）</div>
                  </div>
                  <div className="text-[10px] text-[#94A3B8]">方向语义（挂牌卖/摘牌买符号）待业务方按滚撮"价差撮合"口径校正（登记项）</div>
                </div>
              ) : (
                <div className="rounded border border-dashed border-[#334155] p-3 text-center text-[11px] text-[#94A3B8]">{grey.reason ?? '不评估'}</div>
              )}
            </div>
          </div>
        </section>

        {/* 只读明细 */}
        <section className="rounded-lg border border-[#334155] bg-[#020617] p-3 text-[11px]">
          <div className="mb-2 text-[11px] font-semibold text-[#94A3B8]">只读明细（输出/派生 · 仅联动刷新）</div>
          <table className="w-full">
            <tbody className="mono text-[#F8FAFC]">
              <tr className="border-b border-[#1A1E2F]">
                <td className="py-1 text-[#94A3B8]">火电竞价空间（4 点）</td>
                <td className="text-right">{hourIdx.map((i) => fmt(derived.space96[i])).join(' / ')} MW</td>
              </tr>
              <tr className="border-b border-[#1A1E2F]">
                <td className="py-1 text-[#94A3B8]">负荷率 96 点（4 点）</td>
                <td className="text-right text-[#F59E0B]">{hourIdx.map((i) => fmt(derived.lr96[i], 3)).join(' / ')}</td>
              </tr>
              <tr className="border-b border-[#1A1E2F]">
                <td className="py-1 text-[#94A3B8]">负荷率 24 点合成</td>
                <td className="text-right text-[#F59E0B]">{fmt(derived.lr24[period - 1], 3)}</td>
              </tr>
              <tr className="border-b border-[#1A1E2F]">
                <td className="py-1 text-[#94A3B8]">预测电价 96 点（最终 · 4 点）</td>
                <td className="text-right text-[#A855F7]">{hourIdx.map((i) => fmt(derived.pricing.final_96[i])).join(' / ')} 元</td>
              </tr>
              <tr className="border-b border-[#1A1E2F]">
                <td className="py-1 text-[#94A3B8]">预测电价 24 点（最终 · 对照）</td>
                <td className="text-right text-[#A855F7]">{fmt(derived.pricing.final_24[period - 1])} 元/MWh</td>
              </tr>
            </tbody>
          </table>
        </section>
      </div>
    </div>
  )
}
