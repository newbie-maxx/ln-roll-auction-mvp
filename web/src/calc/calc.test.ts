/** vitest 用例（仅口径边界）：空间单点手算、24↔96 可逆、步骤四双分支、钳制、A-1 回溯、落档 Σ=1 */
import { describe, expect, it } from 'vitest'
import { biddingSpace, expand24, to24 } from './space'
import { fitLinear, findA1Day, runPricing, type PricingInput } from './pricing'
import { binPrices, greyEvaluate, landingStats, similarDays } from './probability'

const ramp = (lo: number, hi: number, n = 96) => Array.from({ length: n }, (_, i) => lo + ((hi - lo) * i) / (n - 1))

describe('空间公式（M7）', () => {
  it('单点手算：负荷−水电−核电−地方燃煤−风电−光伏−联络线−非市场化', () => {
    const b = {
      负荷: [46000], 水电: [2000], 核电: [5000], 地方燃煤: [3000],
      风电: [4000], 光伏: [1000], 联络线: [6000], 非市场化: [2500],
    }
    // 46000 − 2000 − 5000 − 3000 − 4000 − 1000 − 6000 − 2500 = 22500
    expect(biddingSpace(b)[0]).toBe(22500)
  })

  it('任一项缺 → 该点 null（缺输入）', () => {
    const b = {
      负荷: [1], 水电: [null], 核电: [1], 地方燃煤: [1],
      风电: [1], 光伏: [1], 联络线: [1], 非市场化: [1],
    }
    expect(biddingSpace(b)[0]).toBeNull()
  })
})

describe('24↔96 换算', () => {
  it('可逆抽查：expand24 后每 4 点相等；to24 还原为原 24 点值', () => {
    const s24 = Array.from({ length: 24 }, (_, i) => 100 + i)
    const e = expand24(s24)
    expect(e).toHaveLength(96)
    expect(e[0]).toBe(100)
    expect(e[3]).toBe(100)
    expect(e[95]).toBe(123)
    expect(to24(e)).toEqual(s24)
  })
  it('96→24：每 4 点等权平均', () => {
    expect(to24([1, 2, 3, 4, 8, 8, 8, 8])).toEqual([2.5, 8])
  })
})

function makePricing(over: Partial<PricingInput> = {}): PricingInput {
  const dSpace = ramp(10000, 20000)
  return {
    dSpace96: dSpace,
    dUnitOn: 30000,
    zeroLoadRate: 0.5,
    aDaySpace96: dSpace.map((v) => 2 * v),          // k = 2
    aDayPrice96: dSpace.map(() => 0),               // 由覆盖项决定
    a1DaySpace96: dSpace.map((v) => 1.5 * v),
    a1DayPrice96: dSpace.map(() => 0),
    priceFloor: -100,
    priceCap: 1500,
    ...over,
  }
}

describe('M8 四步', () => {
  it('步骤四分支 A：min(预测1) ≥ 10 → 取预测 1；空间 ≤ 临界 → 下限参数', () => {
    const p = makePricing({
      aDayPrice96: ramp(10000, 20000).map((v) => 0.03 * 2 * v + 100),   // A 电价 = 0.03·spaceA + 100
    })
    // 归一后 x = D 空间 → M1 = 0.03·k = 0.06, C1 = 100 → pred1 ∈ [700, 1300]
    const r = runPricing(p)
    expect(r.M1).toBeCloseTo(0.06, 9)
    expect(r.C1).toBeCloseTo(100, 9)
    expect(r.usedPred2_96).toBe(false)
    // 临界：lr ≤ 0.5 → space ≤ 15000；ramp 网格上最大可行点 = t=47 → 10000+47·(10000/95)
    expect(r.criticalSpace96).toBeCloseTo(10000 + (47 * 10000) / 95, 6)
    const firstBelow = r.final_96.findIndex((v) => v !== null && v <= p.priceFloor)
    expect(firstBelow).toBeGreaterThanOrEqual(0)
    // 末点 space=20000 > 临界 → pred1 = 0.06×20000+100 = 1300
    expect(r.final_96[95]).toBeCloseTo(1300, 6)
  })

  it('步骤四分支 B：min(预测1) < 10 → 空间 > 临界的点整体取预测 2', () => {
    const p = makePricing({
      aDayPrice96: ramp(10000, 20000).map(() => -400),   // 拟合为常数 → pred1 钳制到 -100
    })
    const r = runPricing(p)
    expect(r.usedPred2_96).toBe(true)
    expect(r.minPred1_96).toBe(-100)
    // 空间 > 临界的点全部取 pred2（≠ 下限 -100）
    const above = r.final_96.filter((_v, i) => p.dSpace96[i]! > r.criticalSpace96!)
    expect(above.length).toBeGreaterThan(0)
    for (const v of above) expect(v).not.toBe(-100)
  })

  it('钳制：预测值越出 [下限, 上限] 取边界值', () => {
    const p = makePricing({
      aDayPrice96: ramp(10000, 20000).map((v) => 0.2 * 2 * v + 500),   // pred1 ∈ [4500, 8500] → 全钳 1500
      zeroLoadRate: 0,                                                  // 无临界点干扰
    })
    const r = runPricing(p)
    for (const v of r.pred1_96) expect(v === null || v === 1500).toBe(true)
  })

  it('A-1 回溯：电价 ≤0 点 >48 的日被跳过，取更早满足条件的日', () => {
    const days = ['2026-08-26', '2026-08-27', '2026-08-28', '2026-08-29', '2026-08-30']
    const mk = (nonPos: number) => Array.from({ length: 96 }, (_, i) => (i < nonPos ? 0 : 350))
    const prices = {
      '2026-08-30': mk(0),
      '2026-08-29': mk(50),   // >48 → 跳过
      '2026-08-28': mk(10),   // 满足
      '2026-08-27': mk(2),
      '2026-08-26': mk(0),
    }
    const r = findA1Day(days, prices, '2026-08-30')
    expect(r.day).toBe('2026-08-28')
    expect(r.nonPosPoints).toBe(10)
  })

  it('拟合：一次函数精确还原', () => {
    const xs = [1, 2, 3, 4, 5]
    const ys = xs.map((x) => 7 * x - 3)
    const { M, C } = fitLinear(xs, ys)
    expect(M).toBeCloseTo(7, 12)
    expect(C).toBeCloseTo(-3, 12)
  })
})

describe('落点概率与灰度', () => {
  it('相似日检索按 ±阈值 过滤；落档概率 Σ=1；N<3 → 样本不足', () => {
    const prices = [-50, 0, 120, 250, 260, 499, 900, 1499, 700, 710]
    const { bins, n, outOfRange } = binPrices(prices, -100, 1500, 100)
    expect(n).toBe(prices.length)
    expect(outOfRange).toBe(0)
    const sum = bins.reduce((a, b) => a + b.prob, 0)
    expect(sum).toBeCloseTo(1, 12)
    expect(bins.reduce((a, b) => a + b.count, 0)).toBe(n)

    // |0.52−0.53|=0.01、|0.53−0.53|=0、|0.545−0.53|=0.015 均 ≤0.05；0.60 差 0.07 排除
    const lrHist = { '08-01': [0.52, 0.9], '08-02': [0.53, 0.9], '08-03': [0.545, 0.9], '08-04': [0.6, 0.9] }
    const members = similarDays(lrHist, [0.53, 0.5], 1, 0.05, ['08-01', '08-02', '08-03', '08-04'])
    expect(members).toEqual(['08-01', '08-02', '08-03'])

    const rt = { '08-01': [300], '08-02': [450] }   // N=2 < 3
    const st = landingStats(['08-01', '08-02'], rt, 1, -100, 1500, 100, '08-01…08-02')
    expect(st.enough).toBe(false)
    expect(st.expectation).toBeNull()
  })

  it('灰度量价：首/末档与意向价相减 × 量；缺意向价 → 不评估', () => {
    const { bins } = binPrices([300, 320, 1450, 1480, 500, 600, 700, 800], -100, 1500, 100)
    const g = greyEvaluate(385, 100, bins, -100, 1500, 100)
    expect(g.evaluated).toBe(true)
    expect(g.lowestBin.lo).toBe(-100)
    expect(g.highestBin.hi).toBe(1500)
    // 卖方视角：最大收益价 = 挂牌价 − 末档两端 [385−1500, 385−1400]
    expect(g.maxGainPrice).toEqual([385 - 1500, 385 - 1400])
    // 最大风险价 = 挂牌价 − 首档两端 [385−0, 385−(−100)]（首档 [−100,0]）
    expect(g.maxRiskPrice).toEqual([385 - 0, 385 + 100])
    expect(g.maxRiskAmount).toEqual([(385 - 0) * 100, (385 + 100) * 100])
    expect(g.probGain).toBeCloseTo(2 / 8, 12)
    expect(g.probRisk).toBeCloseTo(0, 12)

    const g2 = greyEvaluate(null, 100, bins, -100, 1500, 100)
    expect(g2.evaluated).toBe(false)
    expect(g2.reason).toContain('未录意向挂牌价')
  })
})
