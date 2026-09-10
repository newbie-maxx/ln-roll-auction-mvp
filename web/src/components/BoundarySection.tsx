/** 中栏·边界区：tab 9 项 + 96 点曲线（dataZoom）+ 可编辑数值格（原值/新值/理由 ≥5 字；修订绿与原蓝并存） */
import { useMemo, useState } from 'react'

import { LineChart } from './LineChart'
import { BOUNDARY_KEYS, BOUNDARY_LABELS, type BoundaryKey, type Series96 } from '../calc/types'
import { latestRevisionAt, originalBoundary, useWorkbench } from '../store'

const TAB_LABELS: { key: string; label: string; derived?: boolean }[] = [
  ...BOUNDARY_KEYS.map((k) => ({ key: k, label: BOUNDARY_LABELS[k] ?? k })),
  { key: '实时联络线预测', label: '实时联络线预测' },   // 只读派生：无编辑器、无徽标
  { key: '火电开机', label: '火电开机', derived: true },
]

const TIME_LABELS_96 = Array.from({ length: 96 }, (_, i) => {
  const m = i * 15 + 15
  return `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`
})
const PERIOD_LABELS_24 = Array.from({ length: 24 }, (_, i) => `${String(i).padStart(2, '0')}时`)

function effectiveSeries(key: BoundaryKey, base: Series96, revisions: { boundary: BoundaryKey; t: number; newValue: number; rolledBack?: boolean }[]): Series96 {
  const s = base.slice()
  for (const rev of revisions) {
    if (rev.boundary === key && !rev.rolledBack) s[rev.t - 1] = rev.newValue
  }
  return s
}

/** 单个数值格编辑弹层（fixed 定位、视口内钳制；理由 ≥5 字强制） */
function CellEditor({ boundary, t, current, anchor, onClose }: { boundary: BoundaryKey; t: number; current: number | null; anchor: { left: number; top: number }; onClose: () => void }) {
  const left = Math.min(Math.max(anchor.left, 8), window.innerWidth - 248)   // 与格子左缘对齐：必在中栏内，不遮左栏
  const top = anchor.top + 190 > window.innerHeight ? Math.max(anchor.top - 196, 8) : anchor.top + 8
  const modifyBoundary = useWorkbench((s) => s.modifyBoundary)
  const [value, setValue] = useState(current === null ? '' : String(current))
  const [reason, setReason] = useState('')
  const [error, setError] = useState<string | null>(null)

  const save = () => {
    const num = Number(value)
    if (!Number.isFinite(num)) { setError('请输入有效数值'); return }
    const err = modifyBoundary(boundary, Math.ceil(t / 4), [{ t, value: num }], reason)
    if (err) { setError(err); return }
    onClose()
  }

  return (
    <div className="fixed z-50 w-56 rounded-md border border-[#334155] bg-[#0E1223] p-3 shadow-lg" style={{ left, top }}>
      <div className="mb-2 text-xs text-[#94A3B8]">修改 {boundary} · t={t}（{TIME_LABELS_96[t - 1]}）</div>
      <input
        aria-label="新值（MW）"
        aria-invalid={error ? true : undefined}
        className={`mono mb-2 w-full rounded border bg-[#020617] px-2 py-1 text-sm text-[#F8FAFC] outline-none focus:border-[#3B82F6] ${error ? 'border-[#EF4444]' : 'border-[#334155]'}`}
        value={value} onChange={(e) => setValue(e.target.value)} placeholder="新值（MW）" autoFocus
      />
      <input
        aria-label="修改理由（必填）"
        aria-describedby={error ? `cell-edit-error` : undefined}
        aria-invalid={error ? true : undefined}
        className={`mb-2 w-full rounded border bg-[#020617] px-2 py-1 text-xs text-[#F8FAFC] outline-none focus:border-[#3B82F6] ${error ? 'border-[#EF4444]' : 'border-[#334155]'}`}
        value={reason} onChange={(e) => setReason(e.target.value)} placeholder="修改理由（必填，不限字数）"
      />
      {error && <div id={`cell-edit-error`} role="alert" className="mb-2 text-xs text-[#EF4444]">{error}</div>}
      <div className="flex justify-end gap-2">
        <button onClick={onClose} className="cursor-pointer rounded px-2 py-1 text-xs text-[#94A3B8] hover:text-[#F8FAFC]">取消</button>
        <button onClick={save} className="cursor-pointer rounded bg-[#22C55E] px-3 py-1 text-xs font-semibold text-[#0F172A] transition-opacity hover:opacity-90">保存并重算</button>
      </div>
    </div>
  )
}

export function BoundarySection() {
  const [tab, setTab] = useState<string>('负荷')
  const [editing, setEditing] = useState<number | null>(null)
  const [editAnchor, setEditAnchor] = useState<{ left: number; top: number } | null>(null)
  const revisions = useWorkbench((s) => s.revisions)
  const derived = useWorkbench((s) => s.derived)
  const mode = useWorkbench((s) => s.mode)
  const selectedPeriod = useWorkbench((s) => s.selectedPeriod)

  const isBoundary = (BOUNDARY_KEYS as readonly string[]).includes(tab)
  const isRealtimeTie = tab === '实时联络线预测'
  const realtimeTie = derived.realtimeTie96
  /** 实时联络线预测的修改前序列 = 未修订联络线基线 − 未修订省间交易总量（默认 0） */
  const rtOriginal = useMemo(() => {
    if (!isRealtimeTie) return null
    const tie0 = originalBoundary('联络线')
    const tot0 = originalBoundary('省间交易总量')
    return tie0.map((v, i) => {
      const t = tot0[i]
      return v !== null && t !== null ? v + t : null
    })
  }, [isRealtimeTie])
  /** 两个输入边界任一有有效修订 → 实时联络线预测显示 修改前/修改后 并存 */
  const rtHasRev = useMemo(
    () => revisions.some((r) => !r.rolledBack && (r.boundary === '联络线' || r.boundary === '省间交易总量')),
    [revisions],
  )
  const base = useMemo(() => {
    if (isBoundary) return originalBoundary(tab as BoundaryKey)
    if (isRealtimeTie) return realtimeTie
    return derived.on96                       // 火电开机：live=11 步推演（上/下半日恒值）；demo=常量
  }, [tab, isBoundary, isRealtimeTie, derived.on96, realtimeTie])
  const effective = useMemo(
    () => (isBoundary ? effectiveSeries(tab as BoundaryKey, base, revisions) : base),
    [tab, isBoundary, base, revisions],
  )
  const hasRevision = useMemo(
    () => (isRealtimeTie ? rtHasRev : revisions.some((r) => r.boundary === tab && !r.rolledBack)),
    [revisions, tab, isRealtimeTie, rtHasRev],
  )

  const markIdx: [number, number] | null = selectedPeriod
    ? [(selectedPeriod - 1) * 4, selectedPeriod * 4 - 1]
    : null

  const spec = useMemo(() => ({
    labels: TIME_LABELS_96,
    markAreaIndex: markIdx,
    yName: 'MW',
    series: isRealtimeTie
      ? (rtHasRev && rtOriginal
          ? [
              { name: '修改前（原值）', data: rtOriginal, color: '#3B82F6', faded: true },
              { name: '修改后', data: base, color: '#22C55E' },
            ]
          : [{ name: '实时联络线预测', data: base, color: '#22C55E', dashed: true }])
      : [
          { name: hasRevision ? '原值（披露）' : '披露值', data: base, color: '#3B82F6', faded: hasRevision },
          ...(hasRevision ? [{ name: '修订后', data: effective, color: '#22C55E' }] : []),
        ],
  }), [tab, isRealtimeTie, base, effective, hasRevision, markIdx, rtHasRev, rtOriginal])

  return (
    <section className="rounded-lg border border-[#334155] bg-[#0E1223]">
      <div className="flex flex-wrap items-center gap-1 border-b border-[#334155] px-3 py-2">
        <span className="mr-2 text-xs font-semibold text-[#94A3B8]">可改边界（96 点）</span>
        {TAB_LABELS.map((t) => (
          <button
            key={t.key}
            onClick={() => { setTab(t.key); setEditing(null) }}
            className={`cursor-pointer rounded px-2 py-0.5 text-xs transition-colors duration-150 ${
              tab === t.key ? 'bg-[#3B82F6] font-semibold text-white' : 'text-[#94A3B8] hover:bg-[#1A1E2F] hover:text-[#F8FAFC]'
            }`}
          >
            {t.label}
            {t.derived && <span className="ml-1 text-[10px] text-[#F59E0B]">常量</span>}
          </button>
        ))}
      </div>

      {tab === '实时联络线预测' && (
        <div className="border-b border-[#334155] bg-[#1A1E2F]/40 px-3 py-1.5 text-[10px] text-[#22C55E]">
          实时联络线预测 = 联络线基线 + 交易员预测省间交易总量（正=买入/受入，负=卖出/送出）｜派生值 · 只读，随上两项边界修订联动刷新；有修订时显示 修改前/修改后 双曲线并存｜运行日火电竞价空间按此计算
        </div>
      )}

      {tab === '省间交易总量' && (
        <div className="border-b border-[#334155] bg-[#1A1E2F]/40 px-3 py-1.5 text-[10px] text-[#22C55E]">
          交易员预测（省间滚搓 + 省间现货）：正 = 买入/受入，负 = 卖出/送出；24 点输入，默认全 0 待更新——编辑任一点即整小时 4 点同值；计算时自动展开 96 点（功率值，不除以 4）
        </div>
      )}

      {tab === '火电开机' && (
        <div className="border-b border-[#334155] bg-[#1A1E2F]/40 px-3 py-1.5 text-[10px] text-[#F59E0B]">
          {mode === 'live'
            ? `开机由 11 步推演产出（正/负备用校验 + 上/下半日各取段内最大）：上半日 ${derived.on96[0]?.toFixed(1)} MW｜下半日 ${derived.on96[95]?.toFixed(1)} MW；只读，改「全局参数」中调频/备用/受阻/平衡系数/零价点负荷率/最小开机/装机−检修即可重算`
            : `demo 简化：开机 = 全天常量（${derived.unitOn.toFixed(1)} MW，默认 08-31 日前开机均值）；启动后端进入实时计算模式即为 11 步推演`}
        </div>
      )}

      <div className="p-2">
        <LineChart spec={spec} />
      </div>

      <div className="border-t border-[#334155] px-3 py-2">
        <div className="mb-1 flex items-center justify-between text-[10px] text-[#94A3B8]">
          <span>数值格（点击可改；修订格绿色 = 原值→新值 + 理由；单位 MW）</span>
          <span>当前边界：{tab}｜96 点</span>
        </div>
        <div className="relative grid grid-cols-12 gap-px">
          {effective.map((v, i) => {
            const t = i + 1
            const rev = isRealtimeTie
              ? (latestRevisionAt(revisions, '联络线', t) ?? latestRevisionAt(revisions, '省间交易总量', t))
              : isBoundary ? latestRevisionAt(revisions, tab as BoundaryKey, t) : undefined
            const inSelected = selectedPeriod !== null && t > (selectedPeriod - 1) * 4 && t <= selectedPeriod * 4
            return (
              <div key={t} className="relative">
                <button
                  onClick={(e) => {
              if (!isBoundary) return
              if (editing === t) { setEditing(null); return }
              const r = (e.currentTarget as HTMLElement).getBoundingClientRect()
              setEditAnchor({ left: r.left, top: r.bottom })
              setEditing(t)
            }}
                  className={`mono w-full cursor-pointer px-1 py-1 text-left text-[10px] transition-colors duration-150 ${
                    rev
                      ? 'bg-[#22C55E]/15 text-[#22C55E]'
                      : inSelected
                        ? 'bg-[#EF4444]/10 text-[#F8FAFC]'
                        : 'bg-[#1A1E2F] text-[#94A3B8] hover:bg-[#334155]/50 hover:text-[#F8FAFC]'
                  } ${!isBoundary ? 'cursor-default' : ''}`}
                  title={rev ? `${rev.oldValue} → ${rev.newValue}｜理由：${rev.reason}` : `${TIME_LABELS_96[i]}：${v ?? '缺输入'}`}
                >
                  {v === null ? '缺' : v >= 10000 ? (v / 1000).toFixed(1) + 'k' : v.toFixed(0)}
                </button>
                {editing === t && isBoundary && editAnchor && (
                  <CellEditor boundary={tab as BoundaryKey} t={t} current={v} anchor={editAnchor} onClose={() => setEditing(null)} />
                )}
              </div>
            )
          })}
        </div>
        {hasRevision && (
          <div className="mt-2 max-h-24 overflow-y-auto rounded border border-[#334155] bg-[#020617] p-2 text-[10px]">
            {revisions.filter((r) => !r.rolledBack && (isRealtimeTie
              ? r.boundary === '联络线' || r.boundary === '省间交易总量'
              : r.boundary === tab)).slice().reverse().map((r) => (
              <div key={r.id} className="flex items-center justify-between py-0.5">
                <span className="text-[#94A3B8]">
                  [{r.boundary}] t={r.t}：<span className="text-[#3B82F6]">{r.oldValue?.toFixed(1)}</span> → <span className="text-[#22C55E]">{r.newValue.toFixed(1)}</span>
                  <span className="ml-2 text-[#F59E0B]">理由：{r.reason}</span>
                </span>
                <button
                  onClick={() => useWorkbench.getState().rollback(r.id)}
                  className="cursor-pointer rounded border border-[#334155] px-1.5 text-[#EF4444] hover:border-[#EF4444]"
                >
                  回退
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </section>
  )
}

export { TIME_LABELS_96, PERIOD_LABELS_24 }
