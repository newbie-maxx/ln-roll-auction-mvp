/** 负荷率 = 该点火电竞价空间 ÷ 该点开机。
 *  【demo 简化】开机 = 全天常量参数（默认 08-31 日前开机均值）；
 *  11 步推演不在 demo 实现（标注简化，Phase 8 由 Python 后端完整实现，
 *  见 docu/交易员计算流程与逻辑.md §7）。 */
import type { Series24, Series96 } from './types'
import { to24 } from './space'

export function loadRate96(space96: Series96, unitOn: number): Series96 {
  return space96.map((v) =>
    v === null || !Number.isFinite(v) || unitOn <= 0 ? null : v / unitOn,
  )
}

/** 24 点合成负荷率 = 96 点负荷率每 4 点平均（PRD：96→24 合成口径） */
export function loadRate24(lr96: Series96): Series24 {
  return to24(lr96)
}

/** demo 默认开机常量：某日日前开机 96 点均值（缺数跳过） */
export function defaultUnitOn(dayAheadOn: Series96): number {
  const seg = dayAheadOn.filter((v): v is number => v !== null && Number.isFinite(v))
  return seg.length ? seg.reduce((a, c) => a + c, 0) / seg.length : 0
}
