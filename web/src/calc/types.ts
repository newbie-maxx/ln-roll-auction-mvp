/** demo 计算共享类型（Phase 8 由后端替换；口径见 docu/交易员计算流程与逻辑.md） */

/** 96 点序列（t=1..96，00:15…24:00）；null = 缺输入 */
export type Series96 = (number | null)[]
/** 24 点时段序列（period=1..24） */
export type Series24 = (number | null)[]

/** 可改边界 96 点（白名单，见 docu/智能体工具与技能设计.md §5） */
export const BOUNDARY_KEYS = [
  '负荷', '风电', '光伏', '水电', '核电', '地方燃煤', '非市场化', '联络线',
] as const
export type BoundaryKey = (typeof BOUNDARY_KEYS)[number]

/** 某日的日前边界集 */
export interface DayBoundaries {
  负荷: Series96
  水电: Series96
  核电: Series96
  地方燃煤: Series96
  风电: Series96
  光伏: Series96
  联络线: Series96
  非市场化: Series96
  日前电价: Series96
  日前开机: Series96
  检修容量: Series96
}

/** 边界值标注（披露/推断/补齐 + 来源日） */
export type ValueKind = '披露' | '推断' | '补齐' | '意向' | '计算'

export interface Annotation {
  kind: ValueKind
  srcDay?: string
  note?: string
}
