/** M-14 复盘前端口径用例：日期门/快捷区间、登记 6 条校验、CSV 整批拒绝、章节切分与 CSV 转换。 */
import { describe, expect, it } from 'vitest'
import {
  canGenerate, daysBetween, isIsoDate, mdChapterToHtml, mdTablesToCsv, tradingSessionCandidates, parseRegisterCsv, quickRange,
  rangeError, splitChapters, validateRegister, type RegisterDraft,
} from './logic'

const okDraft = (patch: Partial<RegisterDraft> = {}): RegisterDraft => ({
  run_date: '2026-08-14', t: 15, result_type: '成交', direction: '卖',
  price: 283, volume_mwh: 80, ...patch,
})

describe('快捷区间（只填日期，不触发生成）', () => {
  it('近 7/30 日以基准日收尾', () => {
    expect(quickRange('7d', '2026-08-24')).toEqual({ start: '2026-08-18', end: '2026-08-24' })
    expect(quickRange('30d', '2026-08-24')).toEqual({ start: '2026-07-26', end: '2026-08-24' })
  })
  it('本月至今 / 上月（跨年）', () => {
    expect(quickRange('thisMonth', '2026-08-24')).toEqual({ start: '2026-08-01', end: '2026-08-24' })
    expect(quickRange('lastMonth', '2026-01-15')).toEqual({ start: '2025-12-01', end: '2025-12-31' })
  })
  it('天数差按闭区间语义', () => {
    expect(daysBetween('2026-08-13', '2026-08-15')).toBe(2)
  })
})

describe('生成门（未选不得生成）', () => {
  it('未选日期置灰', () => {
    expect(canGenerate(null, null, false)).toBe(false)
    expect(rangeError(null, null)).toContain('请选择复盘区间')
    expect(canGenerate('2026-08-14', null, false)).toBe(false)
  })
  it('起 > 止 / 超 180 天 / 非法格式均拒绝', () => {
    expect(rangeError('2026-08-15', '2026-08-14')).toContain('起 ≤ 止')
    expect(rangeError('2026-01-01', '2026-12-31')).toContain('180')
    expect(rangeError('2026/08/14', '2026-08-15')).toContain('YYYY-MM-DD')
  })
  it('合法区间 + 忙碌态', () => {
    expect(canGenerate('2026-08-01', '2026-08-31', false)).toBe(true)
    expect(canGenerate('2026-08-01', '2026-08-31', true)).toBe(false)
  })
  it('isIsoDate', () => {
    expect(isIsoDate('2026-08-14')).toBe(true)
    expect(isIsoDate('2026-8-14')).toBe(false)
    expect(isIsoDate('')).toBe(false)
  })
})

describe('登记录入口径（镜像后端 6 条）', () => {
  it('合法记录零错误', () => {
    expect(validateRegister(okDraft())).toEqual([])
  })
  it('逐条触发：日期/时段/结果/方向/价格/电量', () => {
    expect(validateRegister(okDraft({ run_date: '8/14' })).join()).toContain('运行日')
    expect(validateRegister(okDraft({ t: 24 })).join()).toContain('0–23')
    expect(validateRegister(okDraft({ t: 1.5 })).join()).toContain('0–23')
    expect(validateRegister(okDraft({ result_type: '挂单' })).join()).toContain('成交/未成交')
    expect(validateRegister(okDraft({ direction: '买入' })).join()).toContain('买/卖')
    expect(validateRegister(okDraft({ price: 2000 })).join()).toContain('价格')
    expect(validateRegister(okDraft({ price: -2000 })).join()).toContain('价格')
    expect(validateRegister(okDraft({ volume_mwh: 0 })).join()).toContain('电量')
    expect(validateRegister(okDraft({ volume_mwh: 99999 })).join()).toContain('电量')
  })
})

describe('CSV 批量（部分非法 → 整批拒绝）', () => {
  const head = '运行日,时段,结果,方向,价格,电量'
  it('合法批', () => {
    const { rows, errors } = parseRegisterCsv(`${head}\n2026-08-14,10,成交,卖,342,120\n2026-08-14,18,未成交,卖,396,160`)
    expect(errors).toEqual([])
    expect(rows).toHaveLength(2)
  })
  it('缺列整批拒绝', () => {
    expect(parseRegisterCsv('日期,时段\n2026-08-14,10').errors[0]).toContain('缺列')
  })
  it('任一行非法 → 整批拒绝并列行号', () => {
    const { rows, errors } = parseRegisterCsv(`${head}\n2026-08-14,10,成交,卖,342,120\n2026-08-14,24,成交,卖,342,120`)
    expect(rows).toEqual([])
    expect(errors[0]).toContain('第 3 行')
  })
  it('utf-8-sig BOM 剥离', () => {
    expect(parseRegisterCsv(`\ufeff${head}\n2026-08-14,10,成交,卖,342,120`).errors).toEqual([])
  })
})

describe('报告预览与导出转换', () => {
  const md = [
    '# 单日复盘报告', '| 项 | 内容 |', '| --- | --- |',
    '## 一、图景', '正文A', '## 二、当日结论', '正文B', '## 三、全天概况', '正文C',
  ].join('\n')
  it('章节切分与顺序', () => {
    const { head, chapters } = splitChapters(md)
    expect(head).toContain('单日复盘报告')
    expect(chapters.map((c) => c.title)).toEqual(['一、图景', '二、当日结论', '三、全天概况'])
    expect(chapters[0].body).toContain('正文A')
  })
  it('管道表转 CSV：跳过分隔行、公式注入转义', () => {
    const csv = mdTablesToCsv('| a | b |\n| --- | --- |\n| 1 | =CMD |\n普通行')
    expect(csv.split('\n')).toEqual(['a,b', '1,\'=CMD'])
  })
})

describe('章节正文渲染（图片/表格/转义）', () => {
  it('图片改写到 figBase 地址，表格转 table，粗体生效，HTML 转义', () => {
    const md = [
      '**G-01　负荷（MW）**', '',
      '![G-01 负荷](G-01_负荷.png)', '',
      '| a | b |', '| --- | --- |', '| 1 | <x> |',
    ].join('\n')
    const html = mdChapterToHtml(md, '/api/retro/reports/R1/figs')
    if (html.includes('src="/api/retro/reports/R1/figs/G-01_负荷.png"') !== true) throw new Error('img')
    if (html.includes('<table>') !== true || html.includes('<th>a</th>') !== true) throw new Error('table')
    if (html.includes('<strong>G-01') !== true) throw new Error('bold')
    if (html.includes('<x>') !== false || html.includes('&lt;x&gt;') !== true) throw new Error('escape')
    if (html.includes('---') !== false) throw new Error('sep row leaked')
  })
})

describe('交易日（场次）候选', () => {
  it('历史运行日：从运行日前一天起往前 7 个日历日、近的在前', () => {
    expect(tradingSessionCandidates('2026-10-23', '2026-10-23')).toEqual([
      '2026-10-22', '2026-10-21', '2026-10-20', '2026-10-19', '2026-10-18', '2026-10-17', '2026-10-16',
    ])
    expect(tradingSessionCandidates('2026-10-01', '2026-10-23').slice(0, 2)).toEqual(['2026-09-30', '2026-09-29'])
    expect(tradingSessionCandidates('bad')).toEqual([])
  })
  it('未来交付日（今天 10-23、运行日 10-25/27）：交易日从今天起往前，不含未来日', () => {
    expect(tradingSessionCandidates('2026-10-25', '2026-10-23')).toEqual([
      '2026-10-23', '2026-10-22', '2026-10-21', '2026-10-20', '2026-10-19', '2026-10-18', '2026-10-17',
    ])
    expect(tradingSessionCandidates('2026-10-27', '2026-10-23')[0]).toBe('2026-10-23')
  })
})
