/** M8 落点区间概率 / 电价期望 / 灰度量价（口径：docu/交易员计算流程与逻辑.md §9–§10）。
 *  相似日唯一用途 = 落点统计：该时段日前口径负荷率 vs 计算负荷率差 ≤ ±阈值。 */
import type { Series24 } from './types'

export interface Bin {
  lo: number
  hi: number
  mid: number
  count: number
  prob: number          // count / n（n = 有效样本数）
}

/** 将 [下限, 上限] 按宽度分档（末档含上限；越界价格丢弃并计数） */
export function binPrices(prices: number[], floor: number, cap: number, width: number): {
  bins: Bin[]
  n: number
  outOfRange: number
} {
  const nBins = Math.floor((cap - floor) / width)
  const bins: Bin[] = Array.from({ length: nBins }, (_, i) => ({
    lo: floor + i * width,
    hi: floor + (i + 1) * width,
    mid: floor + (i + 0.5) * width,
    count: 0,
    prob: 0,
  }))
  let n = 0
  let outOfRange = 0
  for (const price of prices) {
    if (!Number.isFinite(price)) continue
    if (price < floor || price > cap) { outOfRange++; continue }
    const idx = Math.min(nBins - 1, Math.floor((price - floor) / width))
    bins[idx].count++
    n++
  }
  for (const b of bins) b.prob = n > 0 ? b.count / n : 0
  return { bins, n, outOfRange }
}

/** 相似运行日检索：|历史日该时段日前口径负荷率 − D 日该时段计算负荷率| ≤ 阈值 */
export function similarDays(
  histLoadRate24: Record<string, Series24>,   // 历史日"24点平均日前负荷率"
  dLoadRate24: Series24,
  period: number,                              // 1–24
  threshold: number,
  historyDays: string[],
): string[] {
  const target = dLoadRate24[period - 1]
  if (target === null || target === undefined) return []
  const members: string[] = []
  for (const d of historyDays) {
    const lr = histLoadRate24[d]?.[period - 1]
    if (lr === null || lr === undefined || !Number.isFinite(lr)) continue
    if (Math.abs(lr - target) <= threshold) members.push(d)
  }
  return members
}

export interface LandingStats {
  members: string[]
  n: number
  bins: Bin[]
  outOfRange: number
  expectation: number | null      // Σ(档中值 × 概率)
  enough: boolean                 // N ≥ 3 才出概率，否则"样本不足"
  numerator: number               // 落入该档…（逐档见 bins.count）
  denominator: number             // = n
  scope: string                   // 统计范围描述
}

export function landingStats(
  members: string[],
  realtimePriceByDay: Record<string, (number | null)[]>,   // 历史日实时出清价（24 点）
  period: number,
  floor: number,
  cap: number,
  width: number,
  scope: string,
): LandingStats {
  const prices: number[] = []
  for (const d of members) {
    const v = realtimePriceByDay[d]?.[period - 1]
    if (v !== null && v !== undefined && Number.isFinite(v)) prices.push(v)
  }
  const { bins, n, outOfRange } = binPrices(prices, floor, cap, width)
  const enough = n >= 3
  const expectation = enough
    ? bins.reduce((acc, b) => acc + b.mid * b.prob, 0)
    : null
  return { members, n, bins, outOfRange, expectation, enough, numerator: n, denominator: n, scope }
}

export interface GreySide {
  evaluated: boolean
  reason?: string
  gainPrice: [number, number] | null   // 最大收益价差区间（元/MWh）
  lossPrice: [number, number] | null   // 最大亏损价差区间
  gainAmount: [number, number] | null  // × 对应意向量（元）
  lossAmount: [number, number] | null
  probGain: number                     // 收益对应落点档概率（可为 0）
  probLoss: number
}

export interface GreyResult {
  lowestBin: Bin                  // 最小区间 = 贴下限首档 [下限, 下限+宽度]
  highestBin: Bin                 // 最大区间 = 贴上限末档 [上限−宽度, 上限]
  /** 2026-09-11 业务锁定买卖分列：卖方（挂牌）/买方（摘牌）独立评估 */
  seller: GreySide                // 收益 = 挂牌价−最小区间；亏损 = 最大区间−挂牌价（×挂牌量）
  buyer: GreySide                 // 收益 = 最大区间−摘牌价；亏损 = 摘牌价−最小区间（×摘牌量）
}

export interface GreyIntent {
  listPrice: number | null
  listVolume: number | null
  liftPrice: number | null
  liftVolume: number | null
}

/** 灰度量价（买卖分列，2026-09-11）：
 *  最小/最大区间 = 实际有落点样本的最低/最高实时出清价档（无样本回退贴限首/末档，概率 0）；
 *  卖方填挂牌价+挂牌量：最大收益=(挂牌价−最小区间两端)×挂牌量(概率=最低档)，
 *    最大亏损=(最大区间两端−挂牌价)×挂牌量(概率=最高档)；
 *  买方填摘牌价+摘牌量：最大收益=(最大区间两端−摘牌价)×摘牌量(概率=最高档)，
 *    最大亏损=(摘牌价−最小区间两端)×摘牌量(概率=最低档)。两侧独立，缺价/量不评估。 */
export function greyEvaluate(intent: GreyIntent, bins: Bin[], floor: number, cap: number, width: number): GreyResult {
  const present = bins.filter((b) => b.count > 0)
  // 最小/最大区间 = 实际有落点样本（实时出清价）的最低/最高档；无样本回退贴限首/末档（概率 0）
  const lowest = present.length > 0
    ? present.reduce((a, b) => (b.lo < a.lo ? b : a))
    : (bins.find((b) => b.lo === floor) ?? { lo: floor, hi: floor + width, mid: floor + width / 2, count: 0, prob: 0 })
  const highest = present.length > 0
    ? present.reduce((a, b) => (b.hi > a.hi ? b : a))
    : (bins.find((b) => b.hi === cap) ?? { lo: cap - width, hi: cap, mid: cap - width / 2, count: 0, prob: 0 })
  const side = (
    price: number | null, volume: number | null,
    gain: (p: number) => [number, number], loss: (p: number) => [number, number],
    probGain: number, probLoss: number, priceLabel: string, volLabel: string,
  ): GreySide => {
    const base: GreySide = {
      evaluated: false, gainPrice: null, lossPrice: null, gainAmount: null, lossAmount: null, probGain, probLoss,
    }
    if (price === null || price <= 0) return { ...base, reason: `未录${priceLabel}，不评估` }
    if (volume === null || volume <= 0) return { ...base, reason: `未录${volLabel}，不评估` }
    const g = gain(price)
    const l = loss(price)
    return {
      ...base, evaluated: true,
      gainPrice: g, lossPrice: l,
      gainAmount: [g[0] * volume, g[1] * volume],
      lossAmount: [l[0] * volume, l[1] * volume],
    }
  }
  return {
    lowestBin: lowest, highestBin: highest,
    seller: side(intent.listPrice, intent.listVolume,
      (p) => [p - lowest.hi, p - lowest.lo], (p) => [highest.lo - p, highest.hi - p],
      lowest.prob, highest.prob, '意向挂牌价', '意向挂牌量'),
    buyer: side(intent.liftPrice, intent.liftVolume,
      (p) => [highest.lo - p, highest.hi - p], (p) => [p - lowest.hi, p - lowest.lo],
      highest.prob, lowest.prob, '意向摘牌价', '意向摘牌量'),
  }
}
