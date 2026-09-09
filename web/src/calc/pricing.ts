/** M8 电价预测四步（09-08 确定性算法，口径：docu/交易员计算流程与逻辑.md §8）。
 *  输入均为已就绪数值序列（null 点不参与拟合/判定，输出以 null 透传）。 */
import type { Series96 } from './types'
import { to24 } from './space'

/** 最小二乘一次拟合 y = M·x + C */
export function fitLinear(xs: number[], ys: number[]): { M: number; C: number } {
  const n = xs.length
  if (n < 2) throw new Error('拟合样本不足（<2 点）')
  const mx = xs.reduce((a, c) => a + c, 0) / n
  const my = ys.reduce((a, c) => a + c, 0) / n
  let num = 0
  let den = 0
  for (let i = 0; i < n; i++) {
    num += (xs[i] - mx) * (ys[i] - my)
    den += (xs[i] - mx) ** 2
  }
  if (den === 0) throw new Error('拟合自变量方差为 0')
  const M = num / den
  return { M, C: my - M * mx }
}

/** A-1 回溯：从 A 日前一日开始，向前找第一个满足"电价 ≤0 点 ≤48（96 点粒度）"的历史日 */
export function findA1Day(
  daysAsc: string[],            // 历史日升序（08-01…08-30）
  pricesByDay: Record<string, Series96>,
  aDay: string,
): { day: string; nonPosPoints: number } {
  const idx = daysAsc.indexOf(aDay)
  if (idx <= 0) throw new Error(`A 日 ${aDay} 不在历史日内`)
  for (let i = idx - 1; i >= 0; i--) {
    const d = daysAsc[i]
    const prices = (pricesByDay[d] ?? []).filter((v): v is number => v !== null && Number.isFinite(v))
    const nonPos = prices.filter((v) => v <= 0).length
    if (nonPos <= 48) return { day: d, nonPosPoints: nonPos }
  }
  throw new Error('回溯至数据起点仍无满足"电价 ≤0 点 ≤48"的拟合日 2')
}

export interface PricingInput {
  dSpace96: Series96            // D 日火电竞价空间（含人工修订后的生效值）
  dUnitOn: number               // D 日开机（demo 常量；后端 = 11 步推演产出）
  zeroLoadRate: number          // 零价点负荷率（M7 参数，可改）
  aDaySpace96: Series96         // A 日 96 点空间
  aDayPrice96: Series96         // A 日 96 点日前电价
  a1DaySpace96: Series96        // A-1 日（回溯后）96 点空间
  a1DayPrice96: Series96        // A-1 日 96 点日前电价
  priceFloor: number            // 现货下限（默认 -100）
  priceCap: number              // 现货上限（默认 1500）
}

export interface PricingOutput {
  criticalSpace96: number | null    // 步骤一（96 点口径临界空间；无可行点 → null）
  criticalSpace24: number | null    // 24 点口径（24 点版独立判定用）
  k: number                         // A 日放缩系数
  M1: number; C1: number            // A 日放缩拟合
  M2: number; C2: number            // A-1 日不缩放拟合
  pred1_96: Series96; pred2_96: Series96   // 步骤三（已钳制）
  final_96: Series96                // 步骤四最终电价
  pred1_24: Series96; pred2_24: Series96
  final_24: Series96
  usedPred2_96: boolean             // 步骤四整体切换标志（min(预测1) < 10）
  usedPred2_24: boolean
  minPred1_96: number | null
}

function pairs(a: Series96, b: Series96): [number[], number[]] {
  const xs: number[] = []
  const ys: number[] = []
  for (let t = 0; t < 96; t++) {
    const x = a[t]
    const y = b[t]
    if (x !== null && y !== null && Number.isFinite(x) && Number.isFinite(y)) { xs.push(x); ys.push(y) }
  }
  return [xs, ys]
}

function clamp(v: number | null, lo: number, hi: number): number | null {
  if (v === null || !Number.isFinite(v)) return null
  return Math.min(hi, Math.max(lo, v))
}

function maxOf(s: Series96): number | null {
  const seg = s.filter((v): v is number => v !== null && Number.isFinite(v))
  return seg.length ? Math.max(...seg) : null
}

function criticalSpace(space: Series96, unitOn: number, zeroLR: number): number | null {
  // 步骤一：负荷率 ≤ 零价点负荷率 的点中，火电竞价空间最大值
  let best: number | null = null
  for (let t = 0; t < space.length; t++) {
    const v = space[t]
    if (v === null || !Number.isFinite(v) || unitOn <= 0) continue
    if (v / unitOn <= zeroLR && (best === null || v > best)) best = v
  }
  return best
}

/** 四步主入口（96 点版与 24 点版各自独立判定，不强制 24 = 96 聚合） */
export function runPricing(p: PricingInput): PricingOutput {
  // 步骤一
  const criticalSpace96 = criticalSpace(p.dSpace96, p.dUnitOn, p.zeroLoadRate)
  const dSpace24 = to24(p.dSpace96)
  const criticalSpace24 = criticalSpace(dSpace24, p.dUnitOn, p.zeroLoadRate)

  // 步骤二·拟合日 1 = A 日（放缩）
  const dMax = maxOf(p.dSpace96)
  const aMax = maxOf(p.aDaySpace96)
  if (dMax === null || aMax === null || dMax === 0) throw new Error('A/D 日空间最大值缺失，无法放缩')
  const k = aMax / dMax
  const [ax, ay] = pairs(p.aDaySpace96, p.aDayPrice96)
  const f1 = fitLinear(ax.map((x) => x / k), ay)
  // 步骤二·拟合日 2 = A-1 日（不缩放）
  const [bx, by] = pairs(p.a1DaySpace96, p.a1DayPrice96)
  const f2 = fitLinear(bx, by)

  // 步骤三：预测 1/2（96 + 24 两版）+ 统一钳制
  const apply = (s: Series96, M: number, C: number): Series96 =>
    s.map((v) => clamp(v === null ? null : M * v + C, p.priceFloor, p.priceCap))
  const pred1_96 = apply(p.dSpace96, f1.M, f1.C)
  const pred2_96 = apply(p.dSpace96, f2.M, f2.C)
  const pred1_24 = apply(dSpace24, f1.M, f1.C)
  const pred2_24 = apply(dSpace24, f2.M, f2.C)

  // 步骤四：逐点判定（空间 ≤ 临界 → 下限参数；否则看预测 1 全序列最小 < 10 整体切换）
  const minOf = (s: Series96) => {
    const seg = s.filter((v): v is number => v !== null)
    return seg.length ? Math.min(...seg) : null
  }
  const minPred1_96 = minOf(pred1_96)
  const minPred1_24 = minOf(pred1_24)
  const usedPred2_96 = minPred1_96 !== null && minPred1_96 < 10
  const usedPred2_24 = minPred1_24 !== null && minPred1_24 < 10

  const finalize = (space: Series96, crit: number | null, useP2: boolean, p1: Series96, p2: Series96): Series96 =>
    space.map((v, i) => {
      if (v === null) return null
      if (crit !== null && v <= crit) return p.priceFloor
      return useP2 ? p2[i] : p1[i]
    })

  return {
    criticalSpace96,
    criticalSpace24,
    k,
    M1: f1.M, C1: f1.C,
    M2: f2.M, C2: f2.C,
    pred1_96, pred2_96,
    final_96: finalize(p.dSpace96, criticalSpace96, usedPred2_96, pred1_96, pred2_96),
    pred1_24, pred2_24,
    final_24: finalize(dSpace24, criticalSpace24, usedPred2_24, pred1_24, pred2_24),
    usedPred2_96,
    usedPred2_24,
    minPred1_96,
  }
}
