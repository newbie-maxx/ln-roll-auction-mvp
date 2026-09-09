/** 左栏：24 交易时段导航（预测价 / 意向价 / 预估负荷率 24 点合成值；选中高亮） */
import { useWorkbench } from '../store'

const fmt = (v: number | null | undefined, digits = 0) =>
  v === null || v === undefined ? '—' : v.toFixed(digits)

export function PeriodNav() {
  const selectedPeriod = useWorkbench((s) => s.selectedPeriod)
  const selectPeriod = useWorkbench((s) => s.selectPeriod)
  const derived = useWorkbench((s) => s.derived)
  const intents = useWorkbench((s) => s.intents)

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-[#334155] px-3 py-2 text-xs font-semibold text-[#94A3B8]">
        交易时段导航 <span className="ml-1 font-normal">（24 点 · 点击展开时段面板）</span>
      </div>
      <div className="flex-1 overflow-y-auto p-2">
        {Array.from({ length: 24 }, (_, i) => i + 1).map((p) => {
          const price = derived.pricing.final_24[p - 1]
          const intent = intents[p]
          const lr = derived.lr24[p - 1]
          const selected = selectedPeriod === p
          return (
            <button
              key={p}
              onClick={() => selectPeriod(selected ? null : p)}
              className={`mb-1 w-full cursor-pointer rounded-md border px-2 py-1.5 text-left transition-colors duration-150 ${
                selected
                  ? 'border-[#EF4444] bg-[#EF4444]/10'
                  : 'border-transparent bg-[#0E1223] hover:border-[#334155] hover:bg-[#1A1E2F]'
              }`}
            >
              <div className="flex items-baseline justify-between">
                <span className={`mono text-xs font-semibold ${selected ? 'text-[#EF4444]' : 'text-[#F8FAFC]'}`}>
                  {String(p - 1).padStart(2, '0')}:00–{String(p).padStart(2, '0')}:00
                </span>
                <span className="mono text-xs text-[#A855F7]">{fmt(price)}<span className="text-[10px] text-[#94A3B8]"> 元</span></span>
              </div>
              <div className="mt-0.5 flex justify-between text-[10px] text-[#94A3B8]">
                <span>意向 <span className="mono text-[#22C55E]">{fmt(intent?.listPrice ?? null)}</span></span>
                <span>负荷率 <span className="mono text-[#F59E0B]">{fmt(lr, 3)}</span></span>
              </div>
            </button>
          )
        })}
      </div>
    </div>
  )
}
