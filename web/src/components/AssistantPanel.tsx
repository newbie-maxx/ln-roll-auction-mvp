/** 右栏·智能助手：
 *  - live 模式（Step 8）：真实 LLM + 工具调用（/api/chat，非流式整段返回 + loading 态）；
 *    快捷指令以真实自然语言发送；LLM 设置面板（/api/llm/config，key 不回显）。
 *  - demo 模式：桩（快捷指令真实改 store 触发重算 + 确认回复；自由输入 → 「演示桩：未接模型」）。 */
import { useState } from 'react'
import { api, ApiError } from '../api/client'
import { useWorkbench } from '../store'

interface Msg { role: 'user' | 'assistant'; text: string }

export function AssistantPanel() {
  const selectedPeriod = useWorkbench((s) => s.selectedPeriod)
  const mode = useWorkbench((s) => s.mode)
  const derived = useWorkbench((s) => s.derived)
  const llmInfo = useWorkbench((s) => s.llmInfo)
  const initLive = useWorkbench((s) => s.initLive)
  const chatBusy = useWorkbench((s) => s.chatBusy)
  const [msgs, setMsgs] = useState<Msg[]>([
    { role: 'assistant', text: '我是滚搓交易决策助手。仅评审不代拟、不产可申报数值；确定性计算在 Python 工具内执行，我只引用工具返回数值。' },
  ])
  const [input, setInput] = useState('')
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [cfg, setCfg] = useState({ baseUrl: '', apiKey: '', model: '' })
  const [cfgNote, setCfgNote] = useState<string | null>(null)

  const push = (m: Msg) => setMsgs((prev) => [...prev, m])

  const sendLive = async (text: string) => {
    push({ role: 'user', text })
    useWorkbench.setState({ chatBusy: true })
    try {
      const r = await api.chat(text, selectedPeriod)
      if (r.ok && r.reply) {
        push({ role: 'assistant', text: r.reply })
        // LLM 工具可能已改动工作台（修订/参数/意向）→ 刷新全量状态，左栏与输出即时更新
        await initLive()
      } else {
        push({ role: 'assistant', text: `调用失败：${r.error ?? '未知错误'}` })
      }
    } catch (e) {
      push({ role: 'assistant', text: `调用失败：${e instanceof ApiError ? e.message : String(e)}` })
    } finally {
      useWorkbench.setState({ chatBusy: false })
    }
  }

  const sendDemo = (text: string) => {
    push({ role: 'user', text })
    push({ role: 'assistant', text: '演示桩：未接模型。请使用下方快捷指令，或启动后端（uvicorn api:app）接入真实 LLM。' })
  }

  const send = () => {
    const text = input.trim()
    if (!text || chatBusy) return
    setInput('')
    if (mode === 'live') void sendLive(text)
    else sendDemo(text)
  }

  const quickAdjustLoad = () => {
    const p = selectedPeriod ?? 10
    const text = `把 ${p} 时段省调负荷调到 46,000（该小时 4 个 96 点），理由：午间负荷上调`
    if (mode === 'live') {
      void sendLive(text)
      return
    }
    push({ role: 'user', text })
    const state = useWorkbench.getState()
    const points = [1, 2, 3, 4].map((j) => ({ t: (p - 1) * 4 + j, value: 46000 }))
    const err = state.modifyBoundary('负荷', p, points, '午间负荷上调')
    if (err) {
      push({ role: 'assistant', text: `修改被拒绝：${err}` })
      return
    }
    setTimeout(() => {
      const d = useWorkbench.getState().derived
      push({
        role: 'assistant',
        text: `已把时段 ${p} 的省调负荷 4 个 96 点改为 46,000 MW（理由：午间负荷上调），并触发全链重算：该时段 24 点预测电价 = ${d.pricing.final_24[p - 1]?.toFixed(1) ?? '缺输入'} 元/MWh，24 点合成负荷率 = ${d.lr24[p - 1]?.toFixed(3) ?? '缺输入'}。修订链现有 ${useWorkbench.getState().revisions.length} 条记录，原值保留可回退。`,
      })
    }, 200)
  }

  const quickExplain = () => {
    const p = selectedPeriod ?? 10
    if (mode === 'live') {
      void sendLive(`请解释时段 ${p} 的预测电价依据（含拟合系数 M1/C1、M2/C2 与 -100 临界空间）`)
      return
    }
    push({ role: 'user', text: `解释时段 ${p} 的预测电价依据` })
    const { pricing, landing } = derived
    push({
      role: 'assistant',
      text: `时段 ${p} 预测电价依据（demo 计算，数值取自工具返回）：\n` +
        `· 步骤一 −100 临界空间（96 点口径）= ${pricing.criticalSpace96?.toFixed(1) ?? '缺'} MW\n` +
        `· 步骤二 A 日（08-30）放缩拟合：k = ${pricing.k.toFixed(4)}，M1 = ${pricing.M1.toFixed(4)}，C1 = ${pricing.C1.toFixed(2)}；A-1 日（${derived.a1Day.slice(5)}）不缩放拟合：M2 = ${pricing.M2.toFixed(4)}，C2 = ${pricing.C2.toFixed(2)}\n` +
        `· 步骤四：预测1 全序列最小 = ${pricing.minPred1_96?.toFixed(1) ?? '缺'} ${pricing.usedPred2_96 ? '< 10 → 空间 > 临界点整体取预测2' : '≥ 10 → 取预测1'}；该时段 24 点最终价 = ${pricing.final_24[p - 1]?.toFixed(1) ?? '缺输入'} 元/MWh\n` +
        `· 落点：相似运行日 N = ${landing[p - 1].n}（${landing[p - 1].enough ? `期望 = ${landing[p - 1].expectation?.toFixed(1)} 元/MWh` : '样本不足'}）`,
    })
  }

  const quickSimilar = () => {
    const p = selectedPeriod ?? 10
    if (mode === 'live') {
      void sendLive(`检索时段 ${p} 的相似运行日并给出落点统计`)
      return
    }
    push({ role: 'user', text: `检索时段 ${p} 的相似运行日` })
    const st = derived.landing[p - 1]
    push({
      role: 'assistant',
      text: `时段 ${p} 相似运行日（日前口径负荷率 vs 计算负荷率差 ≤ ±5%）：N = ${st.n}${st.members.length ? '，成员：' + st.members.map((d) => d.slice(5)).join('、') : '（无）'}。统计范围：${st.scope}${st.enough ? `；实时出清价落档期望 = ${st.expectation?.toFixed(1)} 元/MWh` : '；样本不足（N<3）不出概率'}。`,
    })
  }

  const saveCfg = async () => {
    try {
      await api.setLlmConfig(cfg.baseUrl, cfg.apiKey, cfg.model)
      await initLive()
      setCfgNote(`已保存（key 不回显，仅存 .env/内存）`)
    } catch (e) {
      setCfgNote(`保存失败：${e instanceof ApiError ? e.message : String(e)}`)
    }
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 border-b border-[#334155] px-3 py-2 text-xs">
        <span className="font-semibold text-[#94A3B8]">智能助手</span>
        <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${mode === 'live' ? (llmInfo.configured ? 'bg-[#22C55E]/20 text-[#22C55E]' : 'bg-[#EF4444]/20 text-[#EF4444]') : 'bg-[#1A1E2F] text-[#F59E0B]'}`}>
          {mode === 'live' ? (llmInfo.configured ? `真实 LLM · ${llmInfo.model}` : '未配置 key') : '演示桩·未接模型'}
        </span>
        <button onClick={() => setSettingsOpen(!settingsOpen)} className="ml-auto cursor-pointer rounded border border-[#334155] px-1.5 py-0.5 text-[10px] text-[#94A3B8] hover:text-[#F8FAFC]">
          LLM 设置
        </button>
        <span className="rounded bg-[#1A1E2F] px-1.5 py-0.5 text-[10px] text-[#94A3B8]">时段 {selectedPeriod ?? '—'}</span>
      </div>

      {settingsOpen && (
        <div className="space-y-2 border-b border-[#334155] bg-[#020617] p-3 text-[10px]">
          <div className="text-[#94A3B8]">OpenAI 兼容 /chat/completions；key 只存 .env/内存、不回显、不入库。</div>
          <input className="w-full rounded border border-[#334155] bg-[#0E1223] px-2 py-1 text-[#F8FAFC] outline-none focus:border-[#3B82F6]" placeholder={`base_url（当前 ${llmInfo.baseUrl || '未设'}）`} value={cfg.baseUrl} onChange={(e) => setCfg({ ...cfg, baseUrl: e.target.value })} />
          <input className="w-full rounded border border-[#334155] bg-[#0E1223] px-2 py-1 text-[#F8FAFC] outline-none focus:border-[#3B82F6]" type="password" placeholder={`API key（当前 ${llmInfo.maskedKey || '未设'}，不回显）`} value={cfg.apiKey} onChange={(e) => setCfg({ ...cfg, apiKey: e.target.value })} />
          <input className="w-full rounded border border-[#334155] bg-[#0E1223] px-2 py-1 text-[#F8FAFC] outline-none focus:border-[#3B82F6]" placeholder={`model（当前 ${llmInfo.model || '未设'}）`} value={cfg.model} onChange={(e) => setCfg({ ...cfg, model: e.target.value })} />
          <div className="flex items-center gap-2">
            <button onClick={saveCfg} className="cursor-pointer rounded bg-[#22C55E] px-3 py-1 font-semibold text-[#0F172A] hover:opacity-90">保存</button>
            {cfgNote && <span className="text-[#94A3B8]">{cfgNote}</span>}
          </div>
        </div>
      )}

      <div className="flex-1 space-y-2 overflow-y-auto p-3 text-[11px] leading-relaxed">
        {msgs.map((m, i) => (
          <div key={i} className={`max-w-[92%] whitespace-pre-wrap rounded-lg px-2.5 py-1.5 ${m.role === 'user' ? 'ml-auto bg-[#3B82F6]/20 text-[#F8FAFC]' : 'bg-[#1A1E2F] text-[#94A3B8]'}`}>
            {m.text}
          </div>
        ))}
        {chatBusy && <div className="max-w-[92%] rounded-lg bg-[#1A1E2F] px-2.5 py-1.5 text-[#94A3B8]">思考中（工具调用循环，≤8 次）…</div>}
      </div>

      <div className="space-y-2 border-t border-[#334155] p-3">
        <div className="flex flex-wrap gap-1">
          <button onClick={quickAdjustLoad} className="cursor-pointer rounded border border-[#334155] px-2 py-1 text-[10px] text-[#F8FAFC] transition-colors duration-150 hover:border-[#3B82F6] hover:text-[#3B82F6]">
            把 {(selectedPeriod ?? 10)} 时段省调负荷调到 46,000
          </button>
          <button onClick={quickExplain} className="cursor-pointer rounded border border-[#334155] px-2 py-1 text-[10px] text-[#F8FAFC] transition-colors duration-150 hover:border-[#A855F7] hover:text-[#A855F7]">
            解释该时段预测电价依据
          </button>
          <button onClick={quickSimilar} className="cursor-pointer rounded border border-[#334155] px-2 py-1 text-[10px] text-[#F8FAFC] transition-colors duration-150 hover:border-[#22C55E] hover:text-[#22C55E]">
            检索相似运行日
          </button>
        </div>
        <div className="flex gap-2">
          <input
            className="flex-1 rounded border border-[#334155] bg-[#020617] px-2 py-1.5 text-xs text-[#F8FAFC] outline-none focus:border-[#3B82F6]"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') send() }}
            placeholder={mode === 'live' ? '向助手提问（将携带当前时段上下文）' : '自由输入（演示桩：未接模型）'}
          />
          <button onClick={send} disabled={chatBusy} className="cursor-pointer rounded bg-[#22C55E] px-3 text-xs font-semibold text-[#0F172A] transition-opacity duration-150 hover:opacity-90 disabled:opacity-40">
            发送
          </button>
        </div>
        <div className="text-[10px] text-[#94A3B8]">护栏 G4：只评审不代拟、不产可申报数值｜每轮快照留痕于 chat_log</div>
      </div>
    </div>
  )
}
