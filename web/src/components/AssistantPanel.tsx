/** 右栏·智能助手（demo 桩）：上下文 chip「当前时段 N」+ 快捷指令（真实改 store 触发重算 + 确认回复）；
 *  自由输入 → 「演示桩：未接模型」。Phase 8 接 /api/chat（真实 LLM + 工具调用）。 */
import { useState } from 'react'
import { useWorkbench } from '../store'

interface Msg { role: 'user' | 'assistant'; text: string }

export function AssistantPanel() {
  const selectedPeriod = useWorkbench((s) => s.selectedPeriod)
  const derived = useWorkbench((s) => s.derived)
  const revisions = useWorkbench((s) => s.revisions)
  const [msgs, setMsgs] = useState<Msg[]>([
    { role: 'assistant', text: '我是滚搓交易决策助手（演示桩，未接模型）。仅评审不代拟、不产可申报数值；正式版将通过工具调用读写工作台状态，确定性计算在 Python 工具内执行。' },
  ])
  const [input, setInput] = useState('')

  const push = (m: Msg) => setMsgs((prev) => [...prev, m])

  const cmdAdjustLoad = () => {
    const p = selectedPeriod ?? 10
    push({ role: 'user', text: `把 ${p} 时段省调负荷调到 46,000（4 个 96 点），理由：午间负荷上调` })
    const state = useWorkbench.getState()
    const points = [1, 2, 3, 4].map((j) => ({ t: (p - 1) * 4 + j, value: 46000 }))
    const err = state.modifyBoundary('负荷', p, points, '午间负荷上调')
    if (err) {
      push({ role: 'assistant', text: `修改被拒绝：${err}` })
      return
    }
    const d = useWorkbench.getState().derived
    push({
      role: 'assistant',
      text: `已把时段 ${p} 的省调负荷 4 个 96 点改为 46,000 MW（理由：午间负荷上调），并触发全链重算：该时段 24 点预测电价 = ${d.pricing.final_24[p - 1]?.toFixed(1) ?? '缺输入'} 元/MWh，24 点合成负荷率 = ${d.lr24[p - 1]?.toFixed(3) ?? '缺输入'}。修订链现有 ${useWorkbench.getState().revisions.length} 条记录，原值保留可回退。`,
    })
  }

  const cmdExplain = () => {
    const p = selectedPeriod ?? 10
    push({ role: 'user', text: `解释时段 ${p} 的预测电价依据` })
    const { pricing, landing } = derived
    push({
      role: 'assistant',
      text: `时段 ${p} 预测电价依据（demo 计算，数值取自工具返回）：\n` +
        `· 步骤一 −100 临界空间（96 点口径）= ${pricing.criticalSpace96?.toFixed(1) ?? '缺'} MW（负荷率 ≤ 零价点负荷率的点中空间最大值）\n` +
        `· 步骤二 A 日（08-30）放缩拟合：k = ${pricing.k.toFixed(4)}，M1 = ${pricing.M1.toFixed(4)}，C1 = ${pricing.C1.toFixed(2)}；A-1 日（${derived.a1Day.slice(5)}）不缩放拟合：M2 = ${pricing.M2.toFixed(4)}，C2 = ${pricing.C2.toFixed(2)}\n` +
        `· 步骤四：预测1 全序列最小 = ${pricing.minPred1_96?.toFixed(1) ?? '缺'} ${pricing.usedPred2_96 ? '< 10 → 空间 > 临界点整体取预测2' : '≥ 10 → 取预测1'}；该时段 24 点最终价 = ${pricing.final_24[p - 1]?.toFixed(1) ?? '缺输入'} 元/MWh\n` +
        `· 落点：相似运行日 N = ${landing[p - 1].n}（${landing[p - 1].enough ? `期望 = ${landing[p - 1].expectation?.toFixed(1)} 元/MWh` : '样本不足'}）`,
    })
  }

  const cmdSimilar = () => {
    const p = selectedPeriod ?? 10
    push({ role: 'user', text: `检索时段 ${p} 的相似运行日` })
    const st = derived.landing[p - 1]
    push({
      role: 'assistant',
      text: `时段 ${p} 相似运行日（日前口径负荷率 vs 计算负荷率差 ≤ ±5%）：N = ${st.n}${st.members.length ? '，成员：' + st.members.map((d) => d.slice(5)).join('、') : '（无）'}。统计范围：${st.scope}${st.enough ? `；实时出清价落档期望 = ${st.expectation?.toFixed(1)} 元/MWh` : '；样本不足（N<3）不出概率'}。`,
    })
  }

  const send = () => {
    const text = input.trim()
    if (!text) return
    push({ role: 'user', text })
    setInput('')
    push({ role: 'assistant', text: '演示桩：未接模型。请使用下方快捷指令，或在 Step 8 联调后接入真实 LLM（/api/chat）。' })
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 border-b border-[#334155] px-3 py-2 text-xs">
        <span className="font-semibold text-[#94A3B8]">智能助手</span>
        <span className="rounded bg-[#1A1E2F] px-1.5 py-0.5 text-[9px] text-[#F59E0B]">演示桩·未接模型</span>
        <span className="ml-auto rounded bg-[#1A1E2F] px-1.5 py-0.5 text-[10px] text-[#94A3B8]">
          当前时段 {selectedPeriod ?? '—'}
        </span>
      </div>
      <div className="flex-1 space-y-2 overflow-y-auto p-3 text-[11px] leading-relaxed">
        {msgs.map((m, i) => (
          <div
            key={i}
            className={`max-w-[92%] whitespace-pre-wrap rounded-lg px-2.5 py-1.5 ${
              m.role === 'user' ? 'ml-auto bg-[#3B82F6]/20 text-[#F8FAFC]' : 'bg-[#1A1E2F] text-[#94A3B8]'
            }`}
          >
            {m.text}
          </div>
        ))}
      </div>
      <div className="space-y-2 border-t border-[#334155] p-3">
        <div className="flex flex-wrap gap-1">
          <button onClick={cmdAdjustLoad} className="cursor-pointer rounded border border-[#334155] px-2 py-1 text-[10px] text-[#F8FAFC] transition-colors duration-150 hover:border-[#3B82F6] hover:text-[#3B82F6]">
            把 {(selectedPeriod ?? 10)} 时段省调负荷调到 46,000
          </button>
          <button onClick={cmdExplain} className="cursor-pointer rounded border border-[#334155] px-2 py-1 text-[10px] text-[#F8FAFC] transition-colors duration-150 hover:border-[#A855F7] hover:text-[#A855F7]">
            解释该时段预测电价依据
          </button>
          <button onClick={cmdSimilar} className="cursor-pointer rounded border border-[#334155] px-2 py-1 text-[10px] text-[#F8FAFC] transition-colors duration-150 hover:border-[#22C55E] hover:text-[#22C55E]">
            检索相似运行日
          </button>
        </div>
        <div className="flex gap-2">
          <input
            className="flex-1 rounded border border-[#334155] bg-[#020617] px-2 py-1.5 text-xs text-[#F8FAFC] outline-none focus:border-[#3B82F6]"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') send() }}
            placeholder="自由输入（演示桩：未接模型）"
          />
          <button onClick={send} className="cursor-pointer rounded bg-[#22C55E] px-3 text-xs font-semibold text-[#0F172A] transition-opacity duration-150 hover:opacity-90">
            发送
          </button>
        </div>
        <div className="text-[9px] text-[#94A3B8]">修订 {revisions.length} 条 · 快照留痕于 chat_log（Phase 8）｜护栏 G4：只评审不代拟</div>
      </div>
    </div>
  )
}
