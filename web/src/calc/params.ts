/** 全局参数默认表 + 校验（口径：PRD §7 / docu/交易员计算流程与逻辑.md §11） */

export interface Params {
  调频容量: number
  正备用: number
  负备用: number
  受阻系数: number
  新能源平衡系数: number
  平衡时段: [number, number][]   // [起,止] 小时，默认 10–12/14–16/19–21
  零价点负荷率: number
  最小开机方式: number
  辽宁装机: number
  检修计划: number
  现货上限: number
  现货下限: number
  区间宽度: number
  相似日阈值: number             // ±5% → 0.05
}

export const DEFAULT_PARAMS: Params = {
  调频容量: 1500,
  正备用: 3000,
  负备用: 400,
  受阻系数: 0.10,
  新能源平衡系数: 0.8,
  平衡时段: [[10, 12], [14, 16], [19, 21]],
  零价点负荷率: 0.5,
  最小开机方式: 15000,
  辽宁装机: 28715,
  检修计划: 2000,
  现货上限: 1500,
  现货下限: -100,
  区间宽度: 100,
  相似日阈值: 0.05,
}

/** 校验规则 = docu/智能体工具与技能设计.md §5；返回错误列表（空 = 通过） */
export function validateParams(p: Partial<Params>): string[] {
  const errs: string[] = []
  const num = (v: unknown) => typeof v === 'number' && Number.isFinite(v)
  if (!num(p.调频容量) || p.调频容量! < 0) errs.push('调频容量须为 ≥0 数值')
  if (!num(p.正备用) || p.正备用! < 0) errs.push('正备用须为 ≥0 数值')
  if (!num(p.负备用) || p.负备用! < 0) errs.push('负备用须为 ≥0 数值')
  if (!num(p.受阻系数) || p.受阻系数! < 0 || p.受阻系数! >= 1) errs.push('受阻系数须 ∈ [0,1)')
  if (!num(p.新能源平衡系数) || p.新能源平衡系数! <= 0 || p.新能源平衡系数! > 1) errs.push('新能源平衡系数须 ∈ (0,1]')
  if (!num(p.零价点负荷率) || p.零价点负荷率! <= 0 || p.零价点负荷率! > 2) errs.push('零价点负荷率须 ∈ (0,2]')
  if (!num(p.最小开机方式) || p.最小开机方式! < 0) errs.push('最小开机方式须为 ≥0 数值')
  if (!num(p.辽宁装机) || p.辽宁装机! <= 0) errs.push('辽宁装机须为 >0 数值')
  if (!num(p.检修计划) || p.检修计划! < 0) errs.push('检修计划须为 ≥0 数值')
  if (!num(p.现货上限) || !num(p.现货下限) || p.现货上限! <= p.现货下限!) errs.push('现货上限须 > 现货下限')
  if (!num(p.区间宽度) || !Number.isInteger(p.区间宽度) || p.区间宽度! <= 0) errs.push('区间宽度须为正整数')
  if (!num(p.相似日阈值) || p.相似日阈值! <= 0 || p.相似日阈值! > 1) errs.push('相似日阈值须 ∈ (0,1]')
  if (p.最小开机方式 != null && p.辽宁装机 != null && p.检修计划 != null
      && p.最小开机方式 > p.辽宁装机 - p.检修计划) {
    errs.push('最小开机方式不得大于 装机 − 检修（开机硬上限）')
  }
  return errs
}
