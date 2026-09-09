/** 数据管理抽屉：当前数据底账（日期范围/滚撮来源/版本）+ 上传数据表（三表契约见说明）。
 *  上传成功 → 按日期合并（后传覆盖同日）→ 全链重算；格式校验失败给逐项错误。 */
import { useRef, useState } from 'react'
import { useWorkbench } from '../store'

export function DataManageDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const dayInfo = useWorkbench((s) => s.dayInfo)
  const apiBusy = useWorkbench((s) => s.apiBusy)
  const uploadData = useWorkbench((s) => s.uploadData)
  const setTargetDay = useWorkbench((s) => s.setTargetDay)
  const fileRef = useRef<HTMLInputElement>(null)
  const [result, setResult] = useState<{ ok: boolean; message?: string } | null>(null)

  if (!open) return null

  const pick = async (f: File | undefined) => {
    if (!f) return
    setResult(null)
    const r = await uploadData(f)
    setResult(r)
    if (fileRef.current) fileRef.current.value = ''
  }

  const days = dayInfo?.all_days ?? []
  return (
    <div className="fixed inset-0 z-40 flex">
      <div className="flex-1 bg-black/50 backdrop-blur-[2px]" onClick={onClose} />
      <div className="flex h-full w-[460px] flex-col overflow-y-auto border-l border-[#334155] bg-[#0E1223] p-4">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-[#F8FAFC]">数据管理（上传与滚撮日）</h2>
          <button onClick={onClose} className="cursor-pointer text-xs text-[#94A3B8] hover:text-[#F8FAFC]">关闭 ✕</button>
        </div>

        {/* 当前底账 */}
        <section className="mb-4 rounded-lg border border-[#334155] bg-[#020617] p-3 text-[11px]">
          <div className="mb-1 font-semibold text-[#F8FAFC]">当前数据底账</div>
          <div className="text-[#94A3B8]">
            库内日期：<span className="mono text-[#F8FAFC]">{days.length ? `${days[0]} … ${days[days.length - 1]}` : '—'}</span>（{days.length} 日）<br />
            可选滚撮日：<span className="mono text-[#F8FAFC]">{dayInfo?.available_days.length ?? 0}</span> 日（8 项边界齐全）<br />
            当前滚撮日：<span className="mono text-[#EF4444]">{dayInfo?.target_day ?? '—'}</span>｜A 日：<span className="mono text-[#3B82F6]">{dayInfo?.a_day ?? '—'}</span>｜历史范围：该日之前 {dayInfo?.history_count ?? 0} 日<br />
            省间滚撮来源：<span className="text-[#F59E0B]">{dayInfo?.roll_source?.slice(0, 60) ?? '—'}</span><br />
            数据版本：ds{dayInfo?.dataset_version ?? 0}（版本/滚撮日变更后旧修订隔离保留可审计）
          </div>
          <div className="mt-2 flex items-center gap-2">
            <label className="text-[#94A3B8]">切换滚撮日</label>
            <select
              aria-label="选择滚撮日"
              className="mono cursor-pointer rounded border border-[#334155] bg-[#020617] px-2 py-1 text-xs text-[#F8FAFC] outline-none focus:border-[#3B82F6]"
              value={dayInfo?.target_day ?? ''}
              onChange={(e) => { if (e.target.value) setTargetDay(e.target.value) }}
            >
              {(dayInfo?.available_days ?? []).slice().reverse().map((d) => (
                <option key={d} value={d}>{d}{d === days[days.length - 1] ? '（最新）' : ''}</option>
              ))}
            </select>
          </div>
        </section>

        {/* 上传 */}
        <section className="rounded-lg border border-[#334155] bg-[#020617] p-3 text-[11px]">
          <div className="mb-1 font-semibold text-[#F8FAFC]">上传数据表（xlsx，≤50MB）</div>
          <div className="mb-2 text-[#94A3B8] leading-relaxed">
            按 sheet 签名自动识别：<br />
            · <span className="text-[#F8FAFC]">日前边界表</span>——必需 sheet：负荷/水电/核电/地方燃煤/风电/光伏/联络线/非市场化/日前电价（可选：检修容量/日前开机/集中式与分散式风光/24点平均日前负荷率）；96 点列<br />
            · <span className="text-[#F8FAFC]">实时边界表</span>——必需 sheet：实时电价/联络线（可选：24点平均日前负荷率/实时开机 等）；96 点列<br />
            · <span className="text-[#F8FAFC]">省间滚撮表</span>——sheet：成交量/价格；24 点列<br />
            布局统一：首行表头，首列日期，其后数值列。<span className="text-[#F59E0B]">按日期合并，后传覆盖同日</span>；上传成功即全链重算。
          </div>
          <input
            ref={fileRef}
            type="file"
            accept=".xlsx"
            aria-label="选择数据表文件"
            className="block w-full cursor-pointer rounded border border-[#334155] bg-[#0E1223] p-2 text-xs text-[#F8FAFC] file:mr-3 file:cursor-pointer file:rounded file:border-0 file:bg-[#22C55E] file:px-3 file:py-1 file:text-xs file:font-semibold file:text-[#0F172A]"
            onChange={(e) => void pick(e.target.files?.[0])}
          />
          {apiBusy && <div className="mt-2 flex items-center gap-2 text-[#3B82F6]">
            <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-[#3B82F6] border-t-transparent" aria-hidden="true" />
            正在校验并重建数据底账、全链重算…
          </div>}
          {result && (
            <div role="alert" className={`mt-2 rounded border p-2 ${result.ok ? 'border-[#22C55E]/50 bg-[#22C55E]/10 text-[#22C55E]' : 'border-[#EF4444]/50 bg-[#EF4444]/10 text-[#EF4444]'}`}>
              {result.ok ? '✓ ' : '✗ '}{result.message}
            </div>
          )}
        </section>
      </div>
    </div>
  )
}
