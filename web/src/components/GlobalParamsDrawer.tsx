/** 全局参数抽屉：全参数表单（校验）+ 改动留痕列表；改动即时全链重算（理由 ≥5 字） */
import { useEffect, useState } from 'react'
import { useWorkbench } from '../store'
import type { Params } from '../calc/params'

const NUM_FIELDS: { key: keyof Params | '开机常量'; label: string; step?: string; hint?: string }[] = [
  { key: '调频容量', label: '调频容量（MW）' },
  { key: '正备用', label: '正备用（MW）' },
  { key: '负备用', label: '负备用（MW）' },
  { key: '受阻系数', label: '受阻系数', step: '0.01', hint: '默认 0.10' },
  { key: '新能源平衡系数', label: '新能源平衡系数', step: '0.05', hint: '平衡时段 10–12/14–16/19–21，风/光 ×0.8' },
  { key: '零价点负荷率', label: '零价点负荷率', step: '0.01', hint: '低于该负荷率 → 按现货下限定价的跳变临界' },
  { key: '最小开机方式', label: '最小开机方式（MW）' },
  { key: '辽宁装机', label: '辽宁装机（MW）', hint: '开机硬上限 = 装机 − 检修' },
  { key: '检修计划', label: '检修计划（MW）' },
  { key: '现货上限', label: '现货出清上限（元/MWh）' },
  { key: '现货下限', label: '现货出清下限（元/MWh）' },
  { key: '区间宽度', label: '落点区间宽度（元）', hint: '正整数' },
  { key: '相似日阈值', label: '相似日阈值（±）', step: '0.01', hint: '默认 0.05' },
  { key: '开机常量', label: '开机常量（MW）· demo 简化', hint: '默认 08-31 日前开机均值；正式版为 11 步推演' },
]

export function GlobalParamsDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const params = useWorkbench((s) => s.params)
  const apiBusy = useWorkbench((s) => s.apiBusy)
  const unitOn = useWorkbench((s) => s.unitOn)
  const paramLog = useWorkbench((s) => s.paramLog)
  const setParams = useWorkbench((s) => s.setParams)

  const makeDraft = (): Record<string, string> => ({
    ...Object.fromEntries(Object.entries(params).map(([k, v]) => [k, typeof v === 'number' ? String(v) : ''])),
    开机常量: String(unitOn),
  })
  const [draft, setDraft] = useState<Record<string, string>>(makeDraft)

  // 每次打开抽屉重同步当前生效参数（live 模式下来自后端 /api/state，可能已非默认值）
  useEffect(() => {
    if (open) setDraft(makeDraft())
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, params, unitOn])
  const [reason, setReason] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState<string | null>(null)

  const save = () => {
    const patch: Record<string, number> = {}
    for (const f of NUM_FIELDS) {
      const raw = draft[f.key]
      if (raw === undefined || raw === '') continue
      const num = Number(raw)
      if (!Number.isFinite(num)) { setError(`${f.label} 非数值`); return }
      patch[f.key] = num
    }
    const err = setParams(patch as Partial<Params> & { 开机常量?: number }, reason)
    if (err) { setError(err); setSaved(null); return }
    setError(null)
    setSaved(new Date().toLocaleTimeString('zh-CN'))
  }

  if (!open) return null

  return (
    <div className="fixed inset-0 z-40 flex">
      <div className="flex-1 bg-black/50 backdrop-blur-[2px]" onClick={onClose} />
      <div className="flex h-full w-[420px] flex-col overflow-y-auto border-l border-[#334155] bg-[#0E1223] p-4">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-[#F8FAFC]">全局参数（一经改动影响全天，即时触发全链重算）</h2>
          <button onClick={onClose} className="cursor-pointer text-xs text-[#94A3B8] hover:text-[#F8FAFC]">关闭 ✕</button>
        </div>

        <div className="grid grid-cols-2 gap-2">
          {NUM_FIELDS.map((f) => (
            <label key={f.key} className="block">
              <span className="mb-0.5 block text-[10px] text-[#94A3B8]">{f.label}</span>
              <input
                className="mono w-full rounded border border-[#334155] bg-[#020617] px-2 py-1 text-xs text-[#F8FAFC] outline-none focus:border-[#3B82F6]"
                value={draft[f.key] ?? ''}
                step={f.step}
                onChange={(e) => setDraft((prev) => ({ ...prev, [f.key]: e.target.value }))}
              />
              {f.hint && <span className="mt-0.5 block text-[10px] text-[#94A3B8]">{f.hint}</span>}
            </label>
          ))}
        </div>

        <div className="mt-3">
          <label htmlFor="param-reason" className="mb-0.5 block text-[10px] text-[#94A3B8]">修改理由（≥5 字，必填）</label>
          <input
            id="param-reason"
            aria-describedby={error ? 'param-error' : undefined}
            aria-invalid={error ? true : undefined}
            className={`w-full rounded border px-2 py-1.5 text-xs text-[#F8FAFC] outline-none focus:border-[#3B82F6] ${error ? 'border-[#EF4444]' : 'border-[#334155]'} bg-[#020617]`}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="例如：现货上限政策调整，钳制与落点档联动"
          />
        </div>
        {error && <div id="param-error" role="alert" className="mt-2 rounded border border-[#EF4444]/50 bg-[#EF4444]/10 p-2 text-xs text-[#EF4444]">{error}</div>}
        {saved && <div className="mt-2 text-xs text-[#22C55E]">已于 {saved} 保存并重算</div>}
        <button
          onClick={save}
          disabled={apiBusy}
          className="mt-3 inline-flex cursor-pointer items-center gap-2 rounded bg-[#22C55E] px-4 py-1.5 text-xs font-semibold text-[#0F172A] transition-opacity hover:opacity-90 disabled:opacity-45"
        >
          {apiBusy ? '保存中…' : '保存并重算'}
        </button>

        <div className="mt-5">
          <h3 className="mb-1 text-xs font-semibold text-[#94A3B8]">参数改动留痕（{paramLog.length}）</h3>
          <div className="space-y-1">
            {paramLog.slice().reverse().map((c, i) => (
              <div key={i} className="rounded border border-[#1A1E2F] bg-[#020617] p-2 text-[10px]">
                <div className="mono text-[#F8FAFC]">{c.changes}</div>
                <div className="text-[#94A3B8]">理由：{c.reason}｜{c.time}</div>
              </div>
            ))}
            {paramLog.length === 0 && <div className="text-[10px] text-[#94A3B8]">暂无改动</div>}
          </div>
        </div>
      </div>
    </div>
  )
}
