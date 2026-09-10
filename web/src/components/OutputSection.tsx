/** 中栏·输出区（全部只读）：负荷率 96+24 平铺、预测电价 96+24 平铺（最终/预测1/预测2 可切）、
 *  落点概率表 + 期望 + 灰度量价卡片（分子/分母/统计范围/N；N<3 → 样本不足） */
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

function RealtimeTieSection() {
  const derived = useWorkbench((s) => s.derived)
  const selectedPeriod = useWorkbench((s) => s.selectedPeriod)
  if (!derived.realtimeTie96 || derived.realtimeTie96.length === 0) return null
  const spec = {
    labels: TIME_LABELS_96,
    markAreaIndex: selectedPeriod ? ([(selectedPeriod - 1) * 4, selectedPeriod * 4 - 1] as [number, number]) : null,
    yName: 'MW',
    height: 170,
    series: [{ name: '实时联络线预测（= 联络线基线 − 省间交易总量）', data: derived.realtimeTie96, color: '#22C55E', dashed: true }],
  }
  return (
    <section className="rounded-lg border border-[#334155] bg-[#0E1223]">
      <div className="flex items-center gap-2 border-b border-[#334155] px-3 py-2 text-xs">
        <span className="font-semibold text-[#94A3B8]">实时联络线预测（派生值 · 只读）</span>
        <OutputBadge label="输出·只读" />
        <span className="ml-auto text-[10px] text-[#94A3B8]">= 联络线基线 − 交易员预测省间交易总量（省间滚搓 + 省间现货；24 点自动展开 96 点）｜运行日火电竞价空间按此计算</span>
      </div>
      <div className="p-2"><LineChart spec={spec} /></div>
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
        <span className="ml-auto text-[10px] text-[#94A3B8]">开机常量 {fmt(derived.unitOn, 1)} MW（demo 简化，非 11 步推演）</span>
      </div>
      <div className="grid grid-cols-2 gap-2 p-2">
        <div><LineChart spec={spec96} /></div>
        <div><LineChart spec={spec24} /></div>
      </div>
    </section>
  )
}

function LandingSection() {
  const derived = useWorkbench((s) => s.derived)
  const selectedPeriod = useWorkbench((s) => s.selectedPeriod)
  const intents = useWorkbench((s) => s.intents)
  const setIntent = useWorkbench((s) => s.setIntent)
  const params = useWorkbench((s) => s.params)

  const p = selectedPeriod ?? 10
  const st = derived.landing[p - 1]
  const grey = derived.grey[p - 1]
  const intent = intents[p]

  return (
    <section className="rounded-lg border border-[#334155] bg-[#0E1223]">
      <div className="flex flex-wrap items-center gap-2 border-b border-[#334155] px-3 py-2 text-xs">
        <span className="font-semibold text-[#94A3B8]">落点概率 / 期望 / 灰度量价（24 点 · 只读输出）</span>
        <OutputBadge label="输出·只读" />
        <span className="text-[10px] text-[#94A3B8]">当前时段 {p}（{PERIOD_LABELS_24[p - 1]}）{selectedPeriod === null && ' · 未选中，默认展示 10'}</span>
      </div>

      <div className="grid grid-cols-2 gap-3 p-3">
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
          <div className="mb-2 flex items-center gap-2 text-[10px] text-[#94A3B8]">
            <span>时段 {p} 意向挂牌价</span>
            <input
              type="number"
              className="mono w-20 rounded border border-[#334155] bg-[#020617] px-1.5 py-0.5 text-[#F8FAFC] outline-none focus:border-[#3B82F6]"
              value={intent?.listPrice ?? ''}
              onChange={(e) => setIntent(p, { listPrice: e.target.value === '' ? null : Number(e.target.value) })}
            />
            <span>交易量</span>
            <input
              type="number"
              className="mono w-20 rounded border border-[#334155] bg-[#020617] px-1.5 py-0.5 text-[#F8FAFC] outline-none focus:border-[#3B82F6]"
              value={intent?.volume ?? ''}
              onChange={(e) => setIntent(p, { volume: e.target.value === '' ? null : Number(e.target.value) })}
            />
            <span className="rounded bg-[#1A1E2F] px-1 text-[#22C55E]">意向</span>
          </div>
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
  )
}

export function OutputSection() {
  return (
    <div className="space-y-3">
      <PriceChart />
      <RealtimeTieSection />
      <LoadRateSection />
      <LandingSection />
    </div>
  )
}
