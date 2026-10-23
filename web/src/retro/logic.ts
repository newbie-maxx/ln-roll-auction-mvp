/** 复盘前端纯逻辑（M-14）：日期门/快捷区间、登记校验、CSV 解析、报告章节与 CSV 导出转换。
 * 组件零业务计算：所有数值来自后端 /api/retro/*；本文件只做表单门控与格式转换。 */

export interface DateRange {
  start: string
  end: string
}

export type QuickKind = '7d' | '30d' | 'thisMonth' | 'lastMonth'

export const QUICK_LABELS: Record<QuickKind, string> = {
  '7d': '近 7 日',
  '30d': '近 30 日',
  thisMonth: '本月至今',
  lastMonth: '上月',
}

function toIso(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

function fromIso(s: string): Date {
  const [y, m, d] = s.split('-').map(Number)
  return new Date(y, (m ?? 1) - 1, d ?? 1)
}

export function daysBetween(start: string, end: string): number {
  return Math.round((fromIso(end).getTime() - fromIso(start).getTime()) / 86400000)
}

/** 快捷区间（§1.6.1）：只把起止日填进选择器，不改变统计口径、不触发生成。 */
export function quickRange(kind: QuickKind, base: string): DateRange {
  const b = fromIso(base)
  if (kind === '7d') return { start: toIso(new Date(b.getFullYear(), b.getMonth(), b.getDate() - 6)), end: base }
  if (kind === '30d') return { start: toIso(new Date(b.getFullYear(), b.getMonth(), b.getDate() - 29)), end: base }
  if (kind === 'thisMonth') return { start: toIso(new Date(b.getFullYear(), b.getMonth(), 1)), end: base }
  const y = b.getMonth() === 0 ? b.getFullYear() - 1 : b.getFullYear()
  const m = b.getMonth() === 0 ? 11 : b.getMonth() - 1
  const last = new Date(y, m + 1, 0).getDate()
  return { start: toIso(new Date(y, m, 1)), end: toIso(new Date(y, m, last)) }
}

export function isIsoDate(s: string | null | undefined): boolean {
  return typeof s === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(s) && !Number.isNaN(fromIso(s).getTime())
}

/** 生成门（§8.13 红线）：起止日必选且合法、start ≤ end、区间 ≤180 天；否则按钮置灰。 */
export function rangeError(start: string | null, end: string | null): string | null {
  if (!start || !end) return '请选择复盘区间（系统不预选天数）'
  if (!isIsoDate(start) || !isIsoDate(end)) return '日期格式须为 YYYY-MM-DD'
  if (start > end) return '起止日须满足 起 ≤ 止'
  if (daysBetween(start, end) + 1 > 180) return '区间超上限 180 天，请改用明细导出'
  return null
}

export function canGenerate(start: string | null, end: string | null, busy: boolean): boolean {
  return !busy && rangeError(start, end) === null
}

// ---------------- 登记表单（镜像后端 6 条录入口径，后端为权威） ----------------

export interface RegisterDraft {
  run_date: string
  t: number | null
  result_type: string
  direction: string
  price: number | null
  volume_mwh: number | null
  note?: string
}

export const RESULT_TYPES = ['成交', '未成交'] as const
export const DIRECTIONS = ['买', '卖'] as const
export const PRICE_RANGE = [-1000, 1500] as const   // 电量界值 (0, 10000] 在 validateRegister 内联

export function validateRegister(d: RegisterDraft): string[] {
  const errs: string[] = []
  if (!isIsoDate(d.run_date)) errs.push('运行日无效（YYYY-MM-DD）')
  if (!Number.isInteger(d.t) || d.t === null || d.t < 0 || d.t > 23) errs.push('时段必填且须为 0–23')
  if (!(RESULT_TYPES as readonly string[]).includes(d.result_type)) errs.push('结果只能是 成交/未成交 其一')
  if (!(DIRECTIONS as readonly string[]).includes(d.direction)) errs.push('方向只能是 买/卖')
  if (d.price === null || d.price < PRICE_RANGE[0] || d.price > PRICE_RANGE[1]) errs.push('价格须在 -1000~1500 元/MWh')
  if (d.volume_mwh === null || !(d.volume_mwh > 0) || d.volume_mwh > 10000) errs.push('电量须大于 0 且不超过 10000 MWh')
  return errs
}

/** CSV 批量预检（utf-8-sig；部分行非法 → 整批拒绝，与后端一致）。 */
export function parseRegisterCsv(text: string): { rows: RegisterDraft[]; errors: string[] } {
  const clean = text.replace(/^\ufeff/, '')
  const lines = clean.split(/\r?\n/).filter((l) => l.trim() !== '')
  if (lines.length === 0) return { rows: [], errors: ['空文件'] }
  const header = lines[0].split(',').map((h) => h.trim())
  const need = ['运行日', '时段', '结果', '方向', '价格', '电量']
  const missing = need.filter((c) => !header.includes(c))
  if (missing.length) return { rows: [], errors: [`CSV 缺列：${missing.join(',')}`] }
  const idx = (c: string) => header.indexOf(c)
  const rows: RegisterDraft[] = []
  const errors: string[] = []
  for (let i = 1; i < lines.length; i++) {
    const cols = lines[i].split(',')
    const row: RegisterDraft = {
      run_date: (cols[idx('运行日')] ?? '').trim(),
      t: Number((cols[idx('时段')] ?? '').trim()),
      result_type: (cols[idx('结果')] ?? '').trim(),
      direction: (cols[idx('方向')] ?? '').trim(),
      price: Number((cols[idx('价格')] ?? '').trim()),
      volume_mwh: Number((cols[idx('电量')] ?? '').trim()),
    }
    const bad = validateRegister(row)
    if (bad.length) errors.push(`第 ${i + 1} 行：${bad.join('；')}`)
    else rows.push(row)
  }
  if (errors.length) return { rows: [], errors }   // 部分非法 → 整批拒绝（与后端一致）
  return { rows, errors }
}

// ---------------- 报告预览与导出转换 ----------------

export interface Chapter {
  title: string
  body: string
}

/** 章节切分：按 `## ` 标题（一级 `# ` 作为报告头）。 */
export function splitChapters(md: string): { head: string; chapters: Chapter[] } {
  const lines = md.split('\n')
  const head: string[] = []
  const chapters: Chapter[] = []
  let cur: Chapter | null = null
  for (const line of lines) {
    if (line.startsWith('## ')) {
      if (cur) chapters.push(cur)
      cur = { title: line.slice(3).trim(), body: '' }
    } else if (cur) {
      cur.body += line + '\n'
    } else {
      head.push(line)
    }
  }
  if (cur) chapters.push(cur)
  return { head: head.join('\n').trim(), chapters }
}

/** 正文管道表 → CSV（导出明细用；纯格式转换，数字全部来自后端正文）。 */
export function mdTablesToCsv(md: string): string {
  const out: string[] = []
  for (const line of md.split('\n')) {
    const t = line.trim()
    if (!t.startsWith('|') || !t.endsWith('|')) continue
    const cells = t.slice(1, -1).split('|').map((c) => c.trim())
    if (cells.every((c) => /^[-:\s]*$/.test(c))) continue // 分隔行（转义前判断）
    out.push(cells.map((c) => (/^[=+\-@]/.test(c) ? `'${c}` : c)).join(','))
  }
  return out.join('\n')
}

/** 前端 Blob 下载（md/csv 明细；后端文件路径与 sha256 一并展示）。 */
export function downloadText(name: string, text: string, mime = 'text/plain;charset=utf-8') {
  const blob = new Blob([text], { type: mime })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = name
  a.click()
  URL.revokeObjectURL(url)
}

// ---------------- 章节正文 → 安全 HTML（报告预览；零依赖轻量渲染） ----------------

function escapeHtml(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;')
}

function inline(s: string): string {
  return escapeHtml(s)
    .replace(/!\[([^\]]*)\]\(([^)]+)\)/g, (_m, alt: string, file: string) =>
      `<img src="${file}" alt="${alt}" loading="lazy" />`)
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
}

/** 章节体 → HTML：图片改写为 figBase 相对地址；管道表→table；标题/引用/段落。 */
export function mdChapterToHtml(md: string, figBase: string): string {
  const lines = md.split('\n')
  const out: string[] = []
  let tableRows: string[][] = []
  const flushTable = () => {
    if (!tableRows.length) return
    const rows = tableRows.filter((r) => !r.every((c) => /^[-:\s]*$/.test(c)))
    if (rows.length) {
      const html = [`<table><thead><tr>${rows[0].map((c) => `<th>${inline(c)}</th>`).join('')}</tr></thead><tbody>`]
      for (const r of rows.slice(1)) html.push(`<tr>${r.map((c) => `<td>${inline(c)}</td>`).join('')}</tr>`)
      html.push('</tbody></table>')
      out.push(html.join(''))
    }
    tableRows = []
  }
  for (const raw of lines) {
    const t = raw.trim()
    if (t.startsWith('|') && t.endsWith('|')) {
      tableRows.push(t.slice(1, -1).split('|').map((c) => c.trim()))
      continue
    }
    flushTable()
    if (!t) { out.push('') ; continue }
    if (t.startsWith('### ')) { out.push(`<h5>${inline(t.slice(4))}</h5>`) ; continue }
    if (t.startsWith('## ')) { out.push(`<h4>${inline(t.slice(3))}</h4>`) ; continue }
    if (t.startsWith('> ')) { out.push(`<blockquote>${inline(t.slice(2))}</blockquote>`) ; continue }
    out.push(`<p>${inline(t).replace(/src="([^"]+)"/g, (_m, f: string) => `src="${figBase}/${f}"`)}</p>`)
  }
  flushTable()
  return out.filter((x, i, a) => !(x === '' && a[i - 1] === '')).join('\n')
}
