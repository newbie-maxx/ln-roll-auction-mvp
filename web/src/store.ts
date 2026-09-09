/** 工作台状态（zustand）：
 *  - demo 模式：web/src/calc 本地计算（UI 标注 demo）。
 *  - live 模式（Step 8）：数据/计算来自后端 API（web/src/api/client.ts），改动经 API 落 SQLite 留痕；
 *    后端不可达时自动降级 demo（顶部横幅区分）。live 异步调用统一 busy 指示 + 错误上浮（lastError）。 */
import { create } from 'zustand'
import mockJson from './mock/boundaries.json'
import { DEFAULT_PARAMS, validateParams, type Params } from './calc/params'
import { biddingSpace, to24, type SpaceInputs } from './calc/space'
import { defaultUnitOn, loadRate96 } from './calc/loadRate'
import { findA1Day, runPricing, type PricingOutput } from './calc/pricing'
import { greyEvaluate, landingStats, similarDays, type GreyResult, type LandingStats } from './calc/probability'
import { BOUNDARY_KEYS, type BoundaryKey, type Series24, type Series96 } from './calc/types'
import { api, type BackendState } from './api/client'

export interface MockPayload {
  dates: { D: string; A: string; A1: string; historyEnd: string }
  days: string[]
  dayAhead: Record<string, Record<string, number[]>>
  realtime: { 实时电价: Record<string, number[]>; '24点平均日前负荷率': Record<string, number[]> }
  rollAuction: Record<string, { volume24: number[]; price24: number[] }>
  intentDefault: { listPrice: number; liftPrice: number; volume: number }
}

export interface Revision {
  id: string
  boundary: BoundaryKey
  period: number
  t: number
  oldValue: number | null
  newValue: number
  reason: string
  opTime: string
  rolledBack?: boolean
}

export interface IntentEntry { listPrice: number | null; liftPrice: number | null; volume: number | null }

export interface ParamChange { time: string; changes: string; reason: string }

export interface DerivedOutputs {
  unitOn: number
  space96: Series96
  lr96: Series96
  lr24: Series24
  pricing: PricingOutput
  landing: LandingStats[]
  grey: GreyResult[]
  a1Day: string
  a1NonPos: number
}

export interface WorkbenchState {
  mode: 'demo' | 'live'
  data: MockPayload
  params: Params
  unitOn: number
  selectedPeriod: number | null
  revisions: Revision[]
  intents: Record<number, IntentEntry>
  paramLog: ParamChange[]
  derived: DerivedOutputs
  outputsFreshAt: string
  chatBusy: boolean
  lastError: string | null
  apiBusy: boolean
  llmInfo: { configured: boolean; model: string; baseUrl: string; maskedKey: string }
  // actions
  initLive: () => Promise<boolean>
  selectPeriod: (p: number | null) => void
  setParams: (patch: Partial<Params> & { 开机常量?: number }, reason: string) => string | null
  modifyBoundary: (boundary: BoundaryKey, period: number, points: { t: number; value: number }[], reason: string) => string | null
  setIntent: (period: number, patch: Partial<IntentEntry>) => void
  rollback: (revId: string) => void
  clearError: () => void
}

const DATA = mockJson as unknown as MockPayload
const D_DAY = DATA.dates.D
const A_DAY = DATA.dates.A
const HISTORY_DAYS = DATA.days.filter((d) => d !== D_DAY)

function boundarySeries(day: string, key: string): Series96 {
  const raw = DATA.dayAhead[key]?.[day]
  return (raw ?? new Array(96).fill(null)).map((v) => (typeof v === 'number' && Number.isFinite(v) ? v : null))
}

function realtimePrice24(day: string): Series24 {
  return to24((DATA.realtime.实时电价[day] ?? []).map((v) => (Number.isFinite(v) ? v : null)))
}

/** demo：全链重算（任意改动后全量刷新 24 时段落点/灰度） */
function recalcDerived(params: Params, unitOn: number, revisions: Revision[], intents: Record<number, IntentEntry>): DerivedOutputs {
  const dEffective = {} as Record<BoundaryKey, Series96>
  for (const k of BOUNDARY_KEYS) dEffective[k] = boundarySeries(D_DAY, k).slice()
  for (const rev of revisions) {
    if (!rev.rolledBack) dEffective[rev.boundary][rev.t - 1] = rev.newValue
  }
  const space96 = biddingSpace(dEffective as SpaceInputs)
  const lr96 = loadRate96(space96, unitOn)
  const lr24 = to24(lr96)

  const spaceOf = (day: string) => {
    const inputs = {} as Record<BoundaryKey, Series96>
    for (const k of BOUNDARY_KEYS) inputs[k] = boundarySeries(day, k)
    return biddingSpace(inputs as SpaceInputs)
  }
  const pricesByDay: Record<string, Series96> = {}
  for (const d of HISTORY_DAYS) pricesByDay[d] = boundarySeries(d, '日前电价')
  const { day: a1Day, nonPosPoints } = findA1Day(HISTORY_DAYS, pricesByDay, A_DAY)

  const pricing = runPricing({
    dSpace96: space96,
    dUnitOn: unitOn,
    zeroLoadRate: params.零价点负荷率,
    aDaySpace96: spaceOf(A_DAY),
    aDayPrice96: boundarySeries(A_DAY, '日前电价'),
    a1DaySpace96: spaceOf(a1Day),
    a1DayPrice96: pricesByDay[a1Day],
    priceFloor: params.现货下限,
    priceCap: params.现货上限,
  })

  const histLR24 = DATA.realtime['24点平均日前负荷率']
  const rt24: Record<string, Series24> = {}
  for (const d of HISTORY_DAYS) rt24[d] = realtimePrice24(d)

  const landing: LandingStats[] = []
  const grey: GreyResult[] = []
  for (let p = 1; p <= 24; p++) {
    const members = similarDays(histLR24, lr24, p, params.相似日阈值, HISTORY_DAYS)
    const scope = `历史日 08-01…08-30（${HISTORY_DAYS.length} 日），时段 ${p}，阈值 ±${(params.相似日阈值 * 100).toFixed(0)}%`
    const st = landingStats(members, rt24, p, params.现货下限, params.现货上限, params.区间宽度, scope)
    landing.push(st)
    const intent = intents[p]
    grey.push(greyEvaluate(
      intent?.listPrice ?? null,
      intent?.volume ?? null,
      st.bins,
      params.现货下限,
      params.现货上限,
      params.区间宽度,
    ))
  }
  return { unitOn, space96, lr96, lr24, pricing, landing, grey, a1Day, a1NonPos: nonPosPoints }
}

const defaultIntents: Record<number, IntentEntry> = Object.fromEntries(
  Array.from({ length: 24 }, (_, i) => [i + 1, { ...DATA.intentDefault }]),
)
const initialUnitOn = defaultUnitOn(boundarySeries(D_DAY, '日前开机'))

/** live：后端状态 → 组件消费的 DerivedOutputs 形状 */
function mapBackend(st: BackendState): { derived: DerivedOutputs; params: Params; revisions: Revision[]; intents: Record<number, IntentEntry> } {
  const params = { ...DEFAULT_PARAMS, ...(st.params as Partial<Params>) }
  const revisions: Revision[] = (st.revisions ?? []).map((r) => ({
    id: r.rev_id,
    boundary: r.boundary_type as BoundaryKey,
    period: r.period ?? Math.ceil(r.t / 4),
    t: r.t,
    oldValue: null,
    newValue: r.revised_value,
    reason: r.reason,
    opTime: r.op_time,
    rolledBack: r.status !== '有效',
  }))
  const intents: Record<number, IntentEntry> = {}
  for (const [p, v] of Object.entries(st.intents ?? {})) {
    intents[Number(p)] = { listPrice: v.listPrice, liftPrice: v.liftPrice, volume: v.volume }
  }
  const p8 = st.m8
  const derived: DerivedOutputs = {
    unitOn: st.m7.final_on_96?.[0] ?? initialUnitOn,
    space96: st.m7.space_96,
    lr96: st.m7.load_rate_96,
    lr24: st.m7.load_rate_24,
    pricing: {
      criticalSpace96: p8.critical_space_96,
      criticalSpace24: p8.critical_space_24,
      k: p8.k, M1: p8.M1, C1: p8.C1, M2: p8.M2, C2: p8.C2,
      pred1_96: p8.pred1_96, pred2_96: p8.pred2_96, final_96: p8.final_96,
      pred1_24: p8.pred1_24, pred2_24: p8.pred2_24, final_24: p8.final_24,
      usedPred2_96: p8.used_pred2_96, usedPred2_24: p8.used_pred2_24,
      minPred1_96: p8.min_pred1_96,
    },
    landing: st.landing_24.map((l) => ({
      members: l.members, n: l.n, bins: l.bins, outOfRange: l.out_of_range,
      expectation: l.expectation, enough: l.enough,
      numerator: l.n, denominator: l.n, scope: l.scope,
    })),
    grey: st.grey_24.map((g) => ({
      evaluated: g.evaluated, reason: g.reason ?? undefined,
      lowestBin: { ...g.lowest_bin, mid: (g.lowest_bin.lo + g.lowest_bin.hi) / 2, count: 0 },
      highestBin: { ...g.highest_bin, mid: (g.highest_bin.lo + g.highest_bin.hi) / 2, count: 0 },
      maxGainPrice: g.max_gain_price, maxRiskPrice: g.max_risk_price,
      maxGainAmount: g.max_gain_amount, maxRiskAmount: g.max_risk_amount,
      probGain: g.prob_gain, probRisk: g.prob_risk,
    })),
    a1Day: p8.a1_day,
    a1NonPos: p8.a1_non_pos_points,
  }
  return { derived, params, revisions, intents }
}

/** 原始（未修订）边界序列缓存（live 模式），供修订曲线并存 */
let liveBoundaries: Record<string, Series96> | null = null

export function cacheLiveBoundaries(b: Record<string, Series96>) {
  liveBoundaries = b
}

export const useWorkbench = create<WorkbenchState>((set, get) => {
  /** live 异步调用包装：busy 指示 + 错误上浮（不再只进 console）+ 成功后统一刷新 */
  const liveCall = async (fn: () => Promise<unknown>, what: string, after?: () => void): Promise<void> => {
    set({ apiBusy: true, lastError: null })
    try {
      await fn()
      const st = await api.state()
      const { derived, params, revisions, intents } = mapBackend(st)
      const bounds: Record<string, Series96> = {}
      for (const [k, v] of Object.entries(st.boundaries ?? {})) bounds[k] = v.values
      cacheLiveBoundaries(bounds)
      const patch: Partial<WorkbenchState> = {
        derived, params, revisions, unitOn: derived.unitOn,
        outputsFreshAt: new Date().toISOString(),
        apiBusy: false,
      }
      if (Object.keys(intents).length > 0) patch.intents = intents
      set(patch)
      after?.()
    } catch (e) {
      set({ apiBusy: false, lastError: `${what}失败：${e instanceof Error ? e.message : String(e)}` })
    }
  }

  return {
    mode: 'demo',
    data: DATA,
    params: { ...DEFAULT_PARAMS },
    unitOn: initialUnitOn,
    selectedPeriod: null,
    revisions: [],
    intents: defaultIntents,
    paramLog: [],
    derived: recalcDerived({ ...DEFAULT_PARAMS }, initialUnitOn, [], defaultIntents),
    outputsFreshAt: new Date().toISOString(),
    chatBusy: false,
    lastError: null,
    apiBusy: false,
    llmInfo: { configured: false, model: '', baseUrl: '', maskedKey: '' },

    initLive: async () => {
      try {
        const st = await api.state()
        const { derived, params, revisions, intents } = mapBackend(st)
        const bounds: Record<string, Series96> = {}
        for (const [k, v] of Object.entries(st.boundaries ?? {})) bounds[k] = v.values
        cacheLiveBoundaries(bounds)
        let llmInfo = get().llmInfo
        try {
          const cfg = await api.llmConfig()
          llmInfo = { configured: cfg.configured, model: cfg.model, baseUrl: cfg.base_url, maskedKey: cfg.api_key_masked }
        } catch { /* 配置接口失败不阻塞 live */ }
        set({
          mode: 'live', derived, params, revisions,
          intents: Object.keys(intents).length ? intents : get().intents,
          unitOn: derived.unitOn,
          outputsFreshAt: new Date().toISOString(),
          llmInfo,
        })
        return true
      } catch {
        set({ mode: 'demo' })
        return false
      }
    },

    selectPeriod: (p) => set({ selectedPeriod: p }),

    clearError: () => set({ lastError: null }),

    setParams: (patch, reason) => {
      const { 开机常量, ...paramPatch } = patch
      if (get().mode === 'live') {
        // live 模式开机由 M7 11 步推演产出（开机常量为 demo 简化参数，忽略）
        const payload: Record<string, number> = {}
        for (const [k, v] of Object.entries(paramPatch)) if (typeof v === 'number') payload[k] = v
        void liveCall(
          () => api.setParams(payload, reason),
          '参数保存',
          () => set({ paramLog: [...get().paramLog, { time: new Date().toLocaleString('zh-CN'), changes: Object.entries(payload).map(([k, v]) => `${k}=${v}`).join('，'), reason }] }),
        )
        return null
      }
      const next = { ...get().params, ...paramPatch }
      const errs = validateParams(next)
      if (errs.length > 0) return errs.join('；')
      if (开机常量 !== undefined && (!Number.isFinite(开机常量) || 开机常量 <= 0)) return '开机常量须为 >0 数值'
      if (reason.trim().length < 5) return '修改理由须 ≥5 字'
      const unitOn = 开机常量 ?? get().unitOn
      const derived = recalcDerived(next, unitOn, get().revisions, get().intents)
      const changes = Object.entries({ ...paramPatch, ...(开机常量 !== undefined ? { 开机常量 } : {}) })
        .map(([k, v]) => `${k}=${v}`).join('，')
      set({
        params: next, unitOn, derived,
        outputsFreshAt: new Date().toISOString(),
        paramLog: [...get().paramLog, { time: new Date().toLocaleString('zh-CN'), changes, reason }],
      })
      return null
    },

    modifyBoundary: (boundary, period, points, reason) => {
      if (get().mode === 'live') {
        void liveCall(() => api.modifyBoundary(boundary, period, points, reason), '边界修订')
        return null
      }
      if (reason.trim().length < 5) return '修改理由须 ≥5 字'
      if (!BOUNDARY_KEYS.includes(boundary)) return `非法边界 ${boundary}`
      const base = boundarySeries(D_DAY, boundary)
      const now = new Date()
      const newRevs: Revision[] = points.map((pt, i) => {
        if (pt.t < 1 || pt.t > 96 || !Number.isFinite(pt.value)) throw new Error(`非法点位 t=${pt.t}`)
        return {
          id: `R${now.getTime().toString(36)}-${period}-${boundary}-${pt.t}-${i}`,
          boundary, period, t: pt.t,
          oldValue: base[pt.t - 1] ?? null,
          newValue: pt.value,
          reason, opTime: now.toISOString(),
        }
      })
      const revisions = [...get().revisions, ...newRevs]
      const derived = recalcDerived(get().params, get().unitOn, revisions, get().intents)
      set({ revisions, derived, outputsFreshAt: new Date().toISOString() })
      return null
    },

    setIntent: (period, patch) => {
      if (get().mode === 'live') {
        void liveCall(() => api.setIntent(period, {
          list_price: patch.listPrice ?? undefined,
          lift_price: patch.liftPrice ?? undefined,
          volume: patch.volume ?? undefined,
        }), '意向录入')
        return
      }
      const cur = get().intents[period] ?? { ...DATA.intentDefault }
      const next = { ...cur, ...patch }
      if (next.listPrice !== null && next.listPrice <= 0) return
      if (next.liftPrice !== null && next.liftPrice <= 0) return
      if (next.volume !== null && next.volume <= 0) return
      const intents = { ...get().intents, [period]: next }
      const derived = recalcDerived(get().params, get().unitOn, get().revisions, intents)
      set({ intents, derived, outputsFreshAt: new Date().toISOString() })
    },

    rollback: (revId) => {
      if (get().mode === 'live') {
        void liveCall(() => api.rollback(revId), '回退')
        return
      }
      const revisions = get().revisions.map((r) => (r.id === revId ? { ...r, rolledBack: true } : r))
      const derived = recalcDerived(get().params, get().unitOn, revisions, get().intents)
      set({ revisions, derived, outputsFreshAt: new Date().toISOString() })
    },
  }
})

export function originalBoundary(key: BoundaryKey): Series96 {
  if (liveBoundaries && liveBoundaries[key]) return liveBoundaries[key]
  return boundarySeries(D_DAY, key)
}

export function latestRevisionAt(revisions: Revision[], key: BoundaryKey, t: number): Revision | undefined {
  let hit: Revision | undefined
  for (const rev of revisions) {
    if (rev.boundary === key && rev.t === t && !rev.rolledBack) hit = rev
  }
  return hit
}

export { D_DAY, A_DAY, HISTORY_DAYS, boundarySeries, realtimePrice24 }
