/** 中栏·输出区（全部只读）：预测电价 96+24 平铺（最终/预测1/预测2 可切）、负荷率 96+24 平铺。
 *  落点概率/期望/灰度量价已移入时段面板（PeriodSlidePanel）。 */
import { useMemo, useState } from 'react'
import { LineChart } from './LineChart'
import { useWorkbench } from '../store'
import { TIME_LABELS_96, PERIOD_LABELS_24 } from './BoundarySection'

type PriceView = 'final' | 'pred1' | 'pred2'

const fmt = (v: number | null | undefined, d = 1) => (v === null || v === undefined ? '—' : v.toFixed(d))

function OutputBadge({ label }: { label: string }) {
  return <span className="rounded bg-[#A855F7]/20 px-1.5 py-0.5 text-[10px] font-semibold text-[#A855F7]">{label}</span>
}

function PriceChart() {
  const derived = useWorkbench((s) => s.derived)
  const selectedPeriod = useWorkbench((s) => s.selectedPeriod)
  const params = useWorkbench((s) => s.params)
  const [view, setView] = useState<PriceView>('final')
  const [gran, setGran] = useState<96 | 24>(96)

  const labels = gran === 96 ? TIME_LABELS_96 : PERIOD_LABELS_24
  const series96 = view === 'final' ? derived.pricing.final_96 : view === 'pred1' ? derived.pricing.pred1_96 : derived.pricing.pred2_96
  const series24 = view === 'final' ? derived.pricing.final_24 : view === 'pred1' ? derived.pricing.pred1_24 : derived.pricing.pred2_24
  const data = gran === 96 ? series96 : series24
  const markIdx: [number, number] | null = selectedPeriod
    ? gran === 96
      ? [(selectedPeriod - 1) * 4, selectedPeriod * 4 - 1]
      : [selectedPeriod - 1, selectedPeriod - 1]
    : null

  const spec = useMemo(() => ({
    labels,
    markAreaIndex: markIdx,
    yName: '元/MWh',
    height: 200,
    series: [{
      name: `预测电价·${view === 'final' ? '最终' : view === 'pred1' ? '预测1' : '预测2'}（${gran} 点）`,
      data,
      color: '#A855F7',
      area: view === 'final',
    }],
    thresholds: [
      { yAxis: params.现货下限, name: '下限', color: '#EF4444' },
      { yAxis: params.现货上限, name: '上限', color: '#22C55E' },
    ],
  }), [data, gran, markIdx, view, labels, params.现货下限, params.现货上限])

  return (
    <section className="rounded-lg border border-[#334155] bg-[#0E1223]">
      <div className="flex flex-wrap items-center gap-2 border-b border-[#334155] px-3 py-2">
        <span className="text-xs font-semibold text-[#94A3B8]">预测电价（电价预测 · 只读输出）</span>
        <OutputBadge label="输出·只读" />
        <div className="ml-auto flex gap-1">
          {(['final', 'pred1', 'pred2'] as const).map((v) => (
            <button
              key={v}
              onClick={() => setView(v)}
              className={`cursor-pointer rounded px-2 py-0.5 text-[11px] transition-colors duration-150 ${
                view === v ? 'bg-[#A855F7] font-semibold text-white' : 'text-[#94A3B8] hover:bg-[#1A1E2F] hover:text-[#F8FAFC]'
              }`}
            >
              {v === 'final' ? '最终' : v === 'pred1' ? '预测1' : '预测2'}
            </button>
          ))}
          <span className="mx-1 w-px bg-[#334155]" />
          {([96, 24] as const).map((g) => (
            <button
              key={g}
              onClick={() => setGran(g)}
              className={`cursor-pointer rounded px-2 py-0.5 text-[11px] transition-colors duration-150 ${
                gran === g ? 'bg-[#334155] font-semibold text-[#F8FAFC]' : 'text-[#94A3B8] hover:text-[#F8FAFC]'
              }`}
            >
              {g} 点
            </button>
          ))}
        </div>
      </div>
      <div className="px-3 py-1 text-[10px] text-[#94A3B8]">
        钳制 [{params.现货下限}, {params.现货上限}]｜拟合：M1={fmt(derived.pricing.M1, 4)} C1={fmt(derived.pricing.C1, 2)}（A=08-30 放缩 k={fmt(derived.pricing.k, 4)}）、
        M2={fmt(derived.pricing.M2, 4)} C2={fmt(derived.pricing.C2, 2)}（A-1={derived.a1Day.slice(5)} 不缩放，电价≤0 点 {derived.a1NonPos}）｜
        -100 临界空间（96）= {fmt(derived.pricing.criticalSpace96, 1)} MW｜步骤四{derived.pricing.usedPred2_96 ? '整体取预测2（预测1 最小 <10）' : '取预测1'}
      </div>
      <div className="p-2"><LineChart spec={spec} /></div>
      <div className="grid grid-cols-12 gap-px border-t border-[#334155] px-3 py-2">
        {data.map((v, i) => {
          const inSelected = selectedPeriod !== null && (gran === 96
            ? i >= (selectedPeriod - 1) * 4 && i < selectedPeriod * 4
            : i === selectedPeriod - 1)
          return (
            <div
              key={i}
              className={`mono px-1 py-0.5 text-center text-[10px] ${inSelected ? 'rounded bg-[#EF4444]/15 text-[#F8FAFC]' : 'text-[#94A3B8]'}`}
            >
              {v === null ? '缺' : v.toFixed(0)}
            </div>
          )
        })}
      </div>
    </section>
  )
}

function LoadRateSection() {
  const derived = useWorkbench((s) => s.derived)
  const selectedPeriod = useWorkbench((s) => s.selectedPeriod)

  const zeroLR = useWorkbench((s) => s.params.零价点负荷率)
  const spec96 = {
    labels: TIME_LABELS_96,
    markAreaIndex: selectedPeriod ? ([(selectedPeriod - 1) * 4, selectedPeriod * 4 - 1] as [number, number]) : null,
    yName: '负荷率',
    height: 150,
    series: [{ name: '全省火电负荷率（96 点 · 测算）', data: derived.lr96, color: '#F59E0B', dashed: true }],
    thresholds: [{ yAxis: zeroLR, name: '零价点', color: '#94A3B8' }],
  }
  const spec24 = {
    labels: PERIOD_LABELS_24,
    markAreaIndex: selectedPeriod ? ([selectedPeriod - 1, selectedPeriod - 1] as [number, number]) : null,
    yName: '负荷率',
    height: 150,
    series: [{ name: '24 点合成（每 4 点平均）', data: derived.lr24, color: '#F59E0B', dashed: true }],
    thresholds: [{ yAxis: zeroLR, name: '零价点', color: '#94A3B8' }],
  }

  return (
    <section className="rounded-lg border border-[#334155] bg-[#0E1223]">
      <div className="flex items-center gap-2 border-b border-[#334155] px-3 py-2 text-xs">
        <span className="font-semibold text-[#94A3B8]">全省火电负荷率（派生值 · 不算边界 · 只读）</span>
        <span className="rounded bg-[#F59E0B]/15 px-1.5 py-0.5 text-[10px] font-semibold text-[#F59E0B]">测算·虚线</span>
        <span className="ml-auto text-[10px] text-[#94A3B8]">
          {useWorkbench.getState().mode === 'live'
            ? `开机：11 步推演（上半日 ${fmt(derived.on96[0], 1)} / 下半日 ${fmt(derived.on96[95], 1)} MW）`
            : `demo 简化：开机常量 ${fmt(derived.unitOn, 1)} MW（实时计算模式下为 11 步推演）`}
        </span>
      </div>
      <div className="grid grid-cols-2 gap-2 p-2">
        <div><LineChart spec={spec96} /></div>
        <div><LineChart spec={spec24} /></div>
      </div>
    </section>
  )
}

export function OutputSection() {
  return (
    <div className="space-y-3">
      <PriceChart />
      <LoadRateSection />
    </div>
  )
}
