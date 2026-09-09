/** 决策工作台主页面（一屏三栏）：左 280 时段导航 / 中 展示区（边界+输出）/ 右 360 助手。
 *  顶栏：标题 + 日期角色徽标 + M1→M8 stepper + 全局参数/导出。≥1440 布局 + 1280 降级。 */
import { useEffect, useState } from 'react'
import { PeriodNav } from './components/PeriodNav'
import { BoundarySection } from './components/BoundarySection'
import { OutputSection } from './components/OutputSection'
import { AssistantPanel } from './components/AssistantPanel'
import { GlobalParamsDrawer } from './components/GlobalParamsDrawer'
import { DataManageDrawer } from './components/DataManageDrawer'
import { PeriodSlidePanel } from './components/PeriodSlidePanel'
import { D_DAY as D_DAY_BASE, A_DAY as A_DAY_BASE, useWorkbench } from './store'
import { api, ApiError } from './api/client'

const STEPS = ['边界数据装载', '分布式新能源推断', '相似日检索', '联络线基线', '省间交易总数', '预测联络线', '开机推演与负荷率', '电价预测与落点']

export default function App() {
  const selectedPeriod = useWorkbench((s) => s.selectedPeriod)
  const selectPeriod = useWorkbench((s) => s.selectPeriod)
  const mode = useWorkbench((s) => s.mode)
  const revisions = useWorkbench((s) => s.revisions)
  const outputsFreshAt = useWorkbench((s) => s.outputsFreshAt)
  const [paramsOpen, setParamsOpen] = useState(false)
  const [dataOpen, setDataOpen] = useState(false)
  const dayInfo = useWorkbench((s) => s.dayInfo)
  const setTargetDayAction = useWorkbench((s) => s.setTargetDay)
  const D_DAY = dayInfo?.target_day ?? D_DAY_BASE
  const A_DAY = dayInfo?.a_day ?? A_DAY_BASE
  const [exportNote, setExportNote] = useState<string | null>(null)
  const initLive = useWorkbench((s) => s.initLive)
  const lastError = useWorkbench((s) => s.lastError)
  const clearError = useWorkbench((s) => s.clearError)
  const apiBusy = useWorkbench((s) => s.apiBusy)

  useEffect(() => { void initLive() }, [initLive])   // 后端可达 → live；否则降级 demo（横幅区分）

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-[#020617] text-[#F8FAFC]">
      {/* 顶栏 */}
      <header className="flex flex-wrap items-center gap-3 border-b border-[#334155] bg-[#0E1223] px-4 py-2">
        <h1 className="text-sm font-bold tracking-wide">辽宁电力滚搓交易 · 决策工作台</h1>
        <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${mode === 'demo' ? 'bg-[#F59E0B]/20 text-[#F59E0B]' : 'bg-[#22C55E]/20 text-[#22C55E]'}`}>
          {mode === 'demo' ? 'demo 数据 · demo 计算' : '实时计算'}
        </span>
        <div className="flex items-center gap-1 text-[10px]">
          <span className="rounded bg-[#EF4444]/20 px-1.5 py-0.5 font-semibold text-[#EF4444]">滚撮日 {D_DAY.slice(5)}（预测对象）</span>
          <span className="rounded bg-[#3B82F6]/20 px-1.5 py-0.5 text-[#3B82F6]">A 日 {A_DAY.slice(5)}</span>
          <span className="rounded bg-[#94A3B8]/20 px-1.5 py-0.5 text-[#94A3B8]">历史 {dayInfo ? `${dayInfo.history_count} 日（该日之前）` : '08-01…08-30'}</span>
          <select
            aria-label="选择滚撮日"
            className="mono ml-1 cursor-pointer rounded border border-[#334155] bg-[#0E1223] px-1.5 py-0.5 text-[10px] text-[#F8FAFC] outline-none focus:border-[#3B82F6]"
            value={D_DAY}
            onChange={(e) => setTargetDayAction(e.target.value)}
          >
            {(dayInfo?.available_days ?? []).slice().reverse().map((d) => (
              <option key={d} value={d}>{d.slice(5)}</option>
            ))}
          </select>
        </div>
        <nav className="ml-2 hidden flex-1 items-center gap-0.5 xl:flex">
          {STEPS.map((s, i) => (
            <span key={s} className="flex items-center" title={`M${i + 1}（PRD §6 模块编号）`}>
              <span className="rounded bg-[#1A1E2F] px-1.5 py-0.5 text-[10px] text-[#94A3B8]">{s}</span>
              {i < STEPS.length - 1 && <span className="text-[#334155]">→</span>}
            </span>
          ))}
        </nav>
        <div className="ml-auto flex items-center gap-2">
          <span className="hidden text-[10px] text-[#94A3B8] lg:inline">修订 {revisions.length} 条 · 输出刷新 {outputsFreshAt.slice(11, 19)}</span>
          <button
            onClick={() => setDataOpen(true)}
            className="cursor-pointer rounded border border-[#334155] px-2.5 py-1 text-xs text-[#F8FAFC] transition-colors duration-150 hover:border-[#F59E0B] hover:text-[#F59E0B]"
          >
            数据管理
          </button>
          <button
            onClick={() => setParamsOpen(true)}
            className="cursor-pointer rounded border border-[#334155] px-2.5 py-1 text-xs text-[#F8FAFC] transition-colors duration-150 hover:border-[#3B82F6] hover:text-[#3B82F6]"
          >
            全局参数
          </button>
          <button
            onClick={async () => {
              if (mode === 'live') {
                try {
                  const r = await api.exportReport()
                  setExportNote(`已导出：${r.path}（披露/测算标注 + 来源日，修订随导出留痕）`)
                } catch (e) {
                  setExportNote(`导出失败：${e instanceof ApiError ? e.message : String(e)}`)
                }
              } else {
                setExportNote(`demo 模式：导出走后端 /api/export；当前 ${revisions.length} 条修订将随导出留痕`)
              }
            }}
            className="cursor-pointer rounded bg-[#22C55E] px-2.5 py-1 text-xs font-semibold text-[#0F172A] transition-opacity duration-150 hover:opacity-90"
          >
            导出
          </button>
        </div>
      </header>
      {lastError && (
        <div role="alert" className="flex items-center gap-2 border-b border-[#EF4444]/50 bg-[#EF4444]/10 px-4 py-1.5 text-[11px] text-[#EF4444]">
          <span className="font-semibold">操作失败</span>
          <span className="flex-1">{lastError}</span>
          <button onClick={clearError} className="cursor-pointer underline hover:text-[#F8FAFC]">知道了</button>
        </div>
      )}
      {apiBusy && (
        <div className="flex items-center gap-2 border-b border-[#3B82F6]/40 bg-[#3B82F6]/10 px-4 py-1 text-[11px] text-[#3B82F6]">
          <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-[#3B82F6] border-t-transparent" aria-hidden="true" />
          正在联动重算（边界→联络线→开机→电价全链）…
        </div>
      )}
      {exportNote && (
        <div className="border-b border-[#F59E0B]/40 bg-[#F59E0B]/10 px-4 py-1 text-[11px] text-[#F59E0B]">
          {exportNote} <button className="ml-2 cursor-pointer underline" onClick={() => setExportNote(null)}>知道了</button>
        </div>
      )}

      {/* 三栏 */}
      <div className="flex min-h-0 flex-1">
        <aside className="w-[280px] shrink-0 border-r border-[#334155]">
          <PeriodNav />
        </aside>
        <main className={`relative min-w-0 flex-1 p-3 ${paramsOpen || selectedPeriod !== null ? 'overflow-hidden' : 'overflow-y-auto'}`}>
          <div className="space-y-3">
            <BoundarySection />
            <OutputSection />
          </div>
          {selectedPeriod !== null && (
            <PeriodSlidePanel period={selectedPeriod} onClose={() => selectPeriod(null)} />
          )}
        </main>
        <aside className="w-[360px] shrink-0 border-l border-[#334155] max-[1280px]:w-[320px]">
          <AssistantPanel />
        </aside>
      </div>

      <GlobalParamsDrawer open={paramsOpen} onClose={() => setParamsOpen(false)} />
      <DataManageDrawer open={dataOpen} onClose={() => setDataOpen(false)} />
    </div>
  )
}
