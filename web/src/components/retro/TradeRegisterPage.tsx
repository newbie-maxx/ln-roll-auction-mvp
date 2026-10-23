/** M-14 登记页：24 行表格 + 「新增」表单 + CSV 批量 + 当日已登记高亮；保存即时刷新。
 *  前端零计算：校验只做镜像预检（后端权威），逐笔收益全部来自后端结算返回。 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { ApiError } from '../../api/client'
import { retroApi, type RegisterRecord, type SettleTotals } from '../../api/retro'
import { DIRECTIONS, RESULT_TYPES, tradingSessionCandidates, validateRegister, type RegisterDraft } from '../../retro/logic'

function todayIso(): string {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

export function TradeRegisterPage() {
  const [runDate, setRunDate] = useState(todayIso())
  const [records, setRecords] = useState<RegisterRecord[]>([])
  const [totals, setTotals] = useState<SettleTotals | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [draft, setDraft] = useState<RegisterDraft>({
    run_date: runDate, t: 10, result_type: '成交', direction: '卖', price: 342, volume_mwh: 120,
  })
  const [tradeDate, setTradeDate] = useState('')   // '' = 自动（最新生效场次）
  const draftErrors = useMemo(() => validateRegister({ ...draft, run_date: runDate }), [draft, runDate])

  const refresh = useCallback(async (date: string) => {
    try {
      setRecords(await retroApi.registerDay(date))
      setError(null)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    }
  }, [])

  useEffect(() => {
    setDraft((d) => ({ ...d, run_date: runDate }))
    void refresh(runDate)
  }, [runDate, refresh])

  const byHour = useMemo(() => {
    const m = new Map<number, RegisterRecord[]>()
    for (const r of records) {
      const list = m.get(r.t) ?? []
      list.push(r)
      m.set(r.t, list)
    }
    return m
  }, [records])

  async function save() {
    if (draftErrors.length > 0) return
    setBusy(true)
    try {
      const out = await retroApi.register({
        ...draft, run_date: runDate, t: draft.t ?? 0, price: draft.price ?? 0, volume_mwh: draft.volume_mwh ?? 0,
        ...(tradeDate ? { trade_date: tradeDate } : {}),
      })
      setTotals(out.settlement.totals)
      setNote(`已登记（v${out.saved.version}${out.saved.is_revision ? '，修订' : ''}），逐笔收益 ${out.saved.pnl ?? '不出金额（现货缺点）'} 元`)
      await refresh(runDate)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function uploadCsv(file: File) {
    const text = await file.text()
    setBusy(true)
    try {
      const out = await retroApi.registerCsv(text)
      if (!out.ok) setNote(`CSV 整批拒绝（${out.n_rejected ?? out.errors?.length ?? 0} 行非法）：${(out.errors ?? []).slice(0, 3).join('；')}`)
      else {
        setNote(`CSV 导入 ${out.n_saved} 笔`)
        await refresh(runDate)
      }
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-4">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="font-semibold text-[#94A3B8]">运行日</span>
        <input type="date" value={runDate} onChange={(e) => setRunDate(e.target.value)}
          className="rounded border border-[#334155] bg-[#0E1223] px-2 py-1 text-[#F8FAFC] outline-none focus:border-[#3B82F6]" />
        <span className="text-[10px] text-[#94A3B8]">登记 24 点（小时）· 量记 MWh · 未登记 ≠ 未成交</span>
        <label className="ml-auto cursor-pointer rounded border border-[#334155] px-2.5 py-1 text-[#F8FAFC] transition-colors hover:border-[#F59E0B] hover:text-[#F59E0B]">
          CSV 批量导入
          <input type="file" accept=".csv,text/csv" className="hidden"
            onChange={(e) => { const f = e.target.files?.[0]; if (f) void uploadCsv(f); e.currentTarget.value = '' }} />
        </label>
      </div>

      {error && <div role="alert" className="rounded border border-[#EF4444]/50 bg-[#EF4444]/10 px-3 py-1.5 text-[11px] text-[#EF4444]">{error} <button className="underline" onClick={() => setError(null)}>关闭</button></div>}
      {note && <div className="rounded border border-[#22C55E]/40 bg-[#22C55E]/10 px-3 py-1.5 text-[11px] text-[#22C55E]">{note} <button className="underline" onClick={() => setNote(null)}>知道了</button></div>}

      {/* 新增表单 */}
      <div className="flex flex-wrap items-end gap-2 rounded border border-[#334155] bg-[#0E1223] p-3">
        <label className="flex flex-col gap-1 text-[10px] text-[#94A3B8]">交易日（场次）
          <select value={tradeDate} onChange={(e) => setTradeDate(e.target.value)}
            className="w-40 rounded border border-[#334155] bg-[#020617] px-2 py-1 text-xs text-[#F8FAFC]">
            <option value="">自动（最新生效场次）</option>
            {tradingSessionCandidates(runDate, todayIso()).map((d) => <option key={d} value={d}>{d.slice(5)}</option>)}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-[10px] text-[#94A3B8]">时段（0–23 时）
          <select value={draft.t ?? ''} onChange={(e) => setDraft({ ...draft, t: Number(e.target.value) })}
            className="w-24 rounded border border-[#334155] bg-[#020617] px-2 py-1 text-xs text-[#F8FAFC]">
            {Array.from({ length: 24 }, (_, h) => <option key={h} value={h}>{String(h).padStart(2, '0')}:00</option>)}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-[10px] text-[#94A3B8]">方向
          <select value={draft.direction} onChange={(e) => setDraft({ ...draft, direction: e.target.value })}
            className="w-20 rounded border border-[#334155] bg-[#020617] px-2 py-1 text-xs text-[#F8FAFC]">
            {DIRECTIONS.map((d) => <option key={d}>{d}</option>)}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-[10px] text-[#94A3B8]">结果（二选一）
          <select value={draft.result_type} onChange={(e) => setDraft({ ...draft, result_type: e.target.value })}
            className="w-24 rounded border border-[#334155] bg-[#020617] px-2 py-1 text-xs text-[#F8FAFC]">
            {RESULT_TYPES.map((r) => <option key={r}>{r === '未成交' ? '报了没成' : r}</option>)}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-[10px] text-[#94A3B8]">价格（元/MWh）
          <input type="number" step="0.1" value={draft.price ?? ''} onChange={(e) => setDraft({ ...draft, price: e.target.value === '' ? null : Number(e.target.value) })}
            className="w-28 rounded border border-[#334155] bg-[#020617] px-2 py-1 text-xs text-[#F8FAFC]" />
        </label>
        <label className="flex flex-col gap-1 text-[10px] text-[#94A3B8]">电量（MWh）
          <input type="number" step="1" value={draft.volume_mwh ?? ''} onChange={(e) => setDraft({ ...draft, volume_mwh: e.target.value === '' ? null : Number(e.target.value) })}
            className="w-28 rounded border border-[#334155] bg-[#020617] px-2 py-1 text-xs text-[#F8FAFC]" />
        </label>
        <button onClick={() => void save()} disabled={busy || draftErrors.length > 0}
          className="cursor-pointer rounded bg-[#22C55E] px-3 py-1.5 text-xs font-semibold text-[#0F172A] transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40">
          {busy ? '保存中…' : '新增登记'}
        </button>
        {draftErrors.length > 0 && <span className="text-[10px] text-[#EF4444]">{draftErrors.join('；')}</span>}
      </div>

      {/* 24 行表格（当日已登记高亮） */}
      <div className="overflow-x-auto rounded border border-[#334155]">
        <table className="w-full border-collapse text-xs">
          <thead>
            <tr className="bg-[#0E1223] text-[10px] text-[#94A3B8]">
              <th className="px-2 py-1.5 text-left">时段</th>
              <th className="px-2 py-1.5 text-left">结果</th>
              <th className="px-2 py-1.5 text-left">方向</th>
              <th className="px-2 py-1.5 text-right">价格（元/MWh）</th>
              <th className="px-2 py-1.5 text-right">电量（MWh）</th>
              <th className="px-2 py-1.5 text-left">场次</th>
              <th className="px-2 py-1.5 text-right">逐笔收益（元）</th>
              <th className="px-2 py-1.5 text-right">版本</th>
            </tr>
          </thead>
          <tbody>
            {Array.from({ length: 24 }, (_, h) => {
              const rows = byHour.get(h) ?? []
              const first = rows[0]
              return (
                <tr key={h} className={`border-t border-[#1E293B] ${first ? 'bg-[#3B82F6]/10' : ''}`}>
                  <td className="px-2 py-1 text-[#F8FAFC]">{String(h).padStart(2, '0')}:00</td>
                  {first ? (
                    <>
                      <td className="px-2 py-1"><span className={first.result_type === '成交' ? 'rounded bg-[#E8A0A0]/20 px-1.5 py-0.5 text-[#E8A0A0]' : 'rounded bg-[#C8E6C9]/20 px-1.5 py-0.5 text-[#C8E6C9]'}>{first.result_type === '未成交' ? '报了没成' : first.result_type}</span>{rows.length > 1 && <span className="ml-1 text-[10px] text-[#94A3B8]">+{rows.length - 1}</span>}</td>
                      <td className="px-2 py-1 text-[#F8FAFC]">{first.direction}</td>
                      <td className="px-2 py-1 text-right mono text-[#F8FAFC]">{first.price}</td>
                      <td className="px-2 py-1 text-right mono text-[#F8FAFC]">{first.volume_mwh}</td>
                      <td className="px-2 py-1 text-[#94A3B8]">{first.snapshot_trade_date ?? '—'}</td>
                      <td className="px-2 py-1 text-right mono text-[#F8FAFC]">{first.pnl ?? '—'}</td>
                      <td className="px-2 py-1 text-right text-[#94A3B8]">v{first.version}</td>
                    </>
                  ) : (
                    <td colSpan={7} className="px-2 py-1 text-[10px] text-[#475569]">未登记（不推断为「没做单」）</td>
                  )}
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {totals && (
        <div className="rounded border border-[#334155] bg-[#0E1223] p-3 text-xs">
          <div className="mb-1 font-semibold text-[#94A3B8]">当日小计（两行分开，不合并；依据手工登记，未经回单核对）</div>
          <div className="mono flex flex-wrap gap-4 text-[#F8FAFC]">
            <span>已成交溢价合计：{totals.filled.amount} 元（{totals.filled.n} 笔 / {totals.filled.volume} MWh）</span>
            <span>机会成本估算合计：{totals.unfilled.amount} 元（{totals.unfilled.n} 笔 / {totals.unfilled.volume} MWh）</span>
            {totals.day_execution_premium !== null && <span>两者相加＝当日执行溢价：{totals.day_execution_premium} 元</span>}
          </div>
        </div>
      )}
    </div>
  )
}
