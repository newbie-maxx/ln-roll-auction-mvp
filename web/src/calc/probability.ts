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

export interface GreyResult {
  evaluated: boolean
  reason?: string
  lowestBin: Bin                  // 贴下限首档 [下限, 下限+宽度]
  highestBin: Bin                 // 贴上限末档 [上限−宽度, 上限]
  /** 卖方（挂牌）视角；方向语义待业务方按滚撮"价差撮合"口径校正（登记项） */
  maxGainPrice: [number, number] | null   // [min, max]：挂牌价 − 末档两端
  maxRiskPrice: [number, number] | null   // 挂牌价 − 首档两端
  maxGainAmount: [number, number] | null  // × 交易量（元）
  maxRiskAmount: [number, number] | null
  probGain: number                // = 末档概率（与落点概率一致，可为 0）
  probRisk: number                // = 首档概率
}

/** 灰度量价：最低/最高落点档与意向挂牌价相减 × 交易量；缺意向价/量 → 不评估 */
export function greyEvaluate(
  listPrice: number | null,
  volume: number | null,
  bins: Bin[],
  floor: number,
  cap: number,
  width: number,
): GreyResult {
  const lowest = bins.find((b) => b.lo === floor) ?? {
    lo: floor, hi: floor + width, mid: floor + width / 2, count: 0, prob: 0,
  }
  const highest = bins.find((b) => b.hi === cap) ?? {
    lo: cap - width, hi: cap, mid: cap - width / 2, count: 0, prob: 0,
  }
  const base: GreyResult = {
    evaluated: false, lowestBin: lowest, highestBin: highest,
    maxGainPrice: null, maxRiskPrice: null, maxGainAmount: null, maxRiskAmount: null,
    probGain: highest.prob, probRisk: lowest.prob,
  }
  if (listPrice === null || listPrice <= 0) {
    return { ...base, reason: '未录意向挂牌价，不评估收益风险' }
  }
  if (volume === null || volume <= 0) {
    return { ...base, reason: '未录交易量，不评估收益风险' }
  }
  const gain: [number, number] = [listPrice - highest.hi, listPrice - highest.lo]
  const risk: [number, number] = [listPrice - lowest.hi, listPrice - lowest.lo]
  return {
    ...base,
    evaluated: true,
    maxGainPrice: gain,
    maxRiskPrice: risk,
    maxGainAmount: [gain[0] * volume, gain[1] * volume],
    maxRiskAmount: [risk[0] * volume, risk[1] * volume],
  }
}
