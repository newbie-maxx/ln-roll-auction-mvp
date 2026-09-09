/** 工作台状态（zustand）：mock 加载 / 边界 / 参数 / 时段选择 / 修订 append-only（内存态）/ recalc。
 *  demo 计算 = web/src/calc（Phase 8 由后端替换，UI 标注"demo 计算"）。 */
import { create } from 'zustand'
import mockJson from './mock/boundaries.json'
import { DEFAULT_PARAMS, validateParams, type Params } from './calc/params'
import { biddingSpace, to24, type SpaceInputs } from './calc/space'
import { defaultUnitOn, loadRate96 } from './calc/loadRate'
import { findA1Day, runPricing, type PricingOutput } from './calc/pricing'
import { greyEvaluate, landingStats, similarDays, type GreyResult, type LandingStats } from './calc/probability'
import { BOUNDARY_KEYS, type BoundaryKey, type Series24, type Series96 } from './calc/types'

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
  period: number          // 1–24（触发时段）
  t: number               // 1–96（点位）
  oldValue: number | null
  newValue: number
  reason: string          // ≥5 字
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
  landing: LandingStats[]       // 24 时段
  grey: GreyResult[]            // 24 时段
  a1Day: string
  a1NonPos: number
}

export interface WorkbenchState {
  mode: 'demo' | 'live'
  data: MockPayload
  params: Params
  unitOn: number                // demo 简化：开机全天常量
  selectedPeriod: number | null
  revisions: Revision[]         // append-only
  intents: Record<number, IntentEntry>
  paramLog: ParamChange[]
  derived: DerivedOutputs
  outputsFreshAt: string
  // actions
  selectPeriod: (p: number | null) => void
  setParams: (patch: Partial<Params> & { 开机常量?: number }, reason: string) => string | null
  modifyBoundary: (boundary: BoundaryKey, period: number, points: { t: number; value: number }[], reason: string) => string | null
  setIntent: (period: number, patch: Partial<IntentEntry>) => void
  rollback: (revId: string) => void
}

const DATA = mockJson as unknown as MockPayload
const D_DAY = DATA.dates.D
const A_DAY = DATA.dates.A

/** 历史日（不含 D 日）升序 */
const HISTORY_DAYS = DATA.days.filter((d) => d !== D_DAY)

function boundarySeries(day: string, key: string): Series96 {
  const raw = DATA.dayAhead[key]?.[day]
  return (raw ?? new Array(96).fill(null)).map((v) => (typeof v === 'number' && Number.isFinite(v) ? v : null))
}

function spaceOf(day: string): Series96 {
  const inputs = {} as Record<BoundaryKey, Series96>
  for (const k of BOUNDARY_KEYS) inputs[k] = boundarySeries(day, k)
  return biddingSpace(inputs as SpaceInputs)
}

function realtimePrice24(day: string): Series24 {
  return to24((DATA.realtime.实时电价[day] ?? []).map((v) => (Number.isFinite(v) ? v : null)))
}

/** 全链重算（demo：任意改动后全量重算，24 时段落点/灰度一并刷新） */
function recalcDerived(params: Params, unitOn: number, revisions: Revision[], intents: Record<number, IntentEntry>): DerivedOutputs {
  const dEffective = {} as Record<BoundaryKey, Series96>
  for (const k of BOUNDARY_KEYS) dEffective[k] = boundarySeries(D_DAY, k).slice()
  for (const rev of revisions) {
    if (!rev.rolledBack) dEffective[rev.boundary][rev.t - 1] = rev.newValue
  }
  const space96 = biddingSpace(dEffective as SpaceInputs)
  const lr96 = loadRate96(space96, unitOn)
  const lr24 = to24(lr96)

  // A / A-1（回溯）日空间与电价
  const aSpace = spaceOf(A_DAY)
  const pricesByDay: Record<string, Series96> = {}
  for (const d of HISTORY_DAYS) pricesByDay[d] = boundarySeries(d, '日前电价')
  const { day: a1Day, nonPosPoints } = findA1Day(HISTORY_DAYS, pricesByDay, A_DAY)

  const pricing = runPricing({
    dSpace96: space96,
    dUnitOn: unitOn,
    zeroLoadRate: params.零价点负荷率,
    aDaySpace96: aSpace,
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

export const useWorkbench = create<WorkbenchState>((set, get) => ({
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

  selectPeriod: (p) => set({ selectedPeriod: p }),

  setParams: (patch, reason) => {
    const { 开机常量, ...paramPatch } = patch
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
      params: next,
      unitOn,
      derived,
      outputsFreshAt: new Date().toISOString(),
      paramLog: [...get().paramLog, { time: new Date().toLocaleString('zh-CN'), changes, reason }],
    })
    return null
  },

  modifyBoundary: (boundary, period, points, reason) => {
    if (reason.trim().length < 5) return '修改理由须 ≥5 字'
    if (!BOUNDARY_KEYS.includes(boundary)) return `非法边界 ${boundary}`
    const base = boundarySeries(D_DAY, boundary)
    const now = new Date()
    const newRevs: Revision[] = points.map((pt, i) => {
      if (pt.t < 1 || pt.t > 96 || !Number.isFinite(pt.value)) throw new Error(`非法点位 t=${pt.t}`)
      return {
        id: `R${now.getTime().toString(36)}-${period}-${boundary}-${pt.t}-${i}`,
        boundary,
        period,
        t: pt.t,
        oldValue: base[pt.t - 1] ?? null,
        newValue: pt.value,
        reason,
        opTime: now.toISOString(),
      }
    })
    const revisions = [...get().revisions, ...newRevs]
    const derived = recalcDerived(get().params, get().unitOn, revisions, get().intents)
    set({ revisions, derived, outputsFreshAt: new Date().toISOString() })
    return null
  },

  setIntent: (period, patch) => {
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
    // 回退本身记新修订：以回退标记实现（原值经读时合并恢复，append-only 不物理删除）
    const revisions = get().revisions.map((r) => (r.id === revId ? { ...r, rolledBack: true } : r))
    const derived = recalcDerived(get().params, get().unitOn, revisions, get().intents)
    set({ revisions, derived, outputsFreshAt: new Date().toISOString() })
  },
}))

/** 原始（未修订）边界序列，供修订曲线并存 */
export function originalBoundary(key: BoundaryKey): Series96 {
  return boundarySeries(D_DAY, key)
}

/** 某边界当前生效修订（未回退的最新一条），供数值格显示 新值/理由 */
export function latestRevisionAt(revisions: Revision[], key: BoundaryKey, t: number): Revision | undefined {
  let hit: Revision | undefined
  for (const rev of revisions) {
    if (rev.boundary === key && rev.t === t && !rev.rolledBack) hit = rev
  }
  return hit
}

export { D_DAY, A_DAY, HISTORY_DAYS, boundarySeries, realtimePrice24 }
