/** M-14 报告页：起止日必选（未选生成置灰）+ 快捷按钮只填日期 → 生成 → 章节导航预览 → 导出。
 *  生成走 skill SOP→MCP 全链（进度可见）；预览/导出数据全部来自后端报告留档。 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ApiError } from '../../api/client'
import { retroApi, type RetroReport, type RetroReportDetail, type RetroReportMeta } from '../../api/retro'
import { QUICK_LABELS, canGenerate, downloadText, mdChapterToHtml, mdTablesToCsv, quickRange, rangeError, splitChapters, type QuickKind } from '../../retro/logic'

function todayIso(): string {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

export function RetroReportPage() {
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  const [busy, setBusy] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  const [result, setResult] = useState<RetroReport | null>(null)
  const [detail, setDetail] = useState<RetroReportDetail | null>(null)
  const [history, setHistory] = useState<RetroReportMeta[]>([])
  const [chapterIdx, setChapterIdx] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const timerRef = useRef<number | null>(null)

  const gate = rangeError(start || null, end || null)

  const loadHistory = useCallback(async () => {
    try {
      setHistory(await retroApi.reports())
    } catch {
      /* 后端未起时历史区留空，主错误由生成动作暴露 */
    }
  }, [])

  const loadDetail = useCallback(async (reportId: string) => {
    const d = await retroApi.reportDetail(reportId)
    setDetail(d)
    setChapterIdx(0)
  }, [])

  useEffect(() => { void loadHistory() }, [loadHistory])

  useEffect(() => {
    if (busy) {
      const t0 = Date.now()
      timerRef.current = setInterval(() => setElapsed(Math.round((Date.now() - t0) / 1000)), 500)
    } else if (timerRef.current) {
      clearInterval(timerRef.current)
      timerRef.current = null
    }
    return () => clearInterval(timerRef.current ?? undefined)
  }, [busy])

  const chapters = useMemo(
    () => (detail?.content ? splitChapters(detail.content) : { head: '', chapters: [] }),
    [detail],
  )

  async function generate() {
    setBusy(true)
    setError(null)
    setResult(null)
    try {
      const r = await retroApi.report(start, end)
      setResult(r)
      await loadDetail(r.report_id)
      await loadHistory()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function pickHistory(id: string) {
    setError(null)
    try {
      await loadDetail(id)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-hidden p-4">
      {/* 区间选择 + 快捷按钮（只填日期，不触发生成） */}
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <label className="flex items-center gap-1 text-[#94A3B8]">起
          <input type="date" value={start} onChange={(e) => setStart(e.target.value)}
            className="rounded border border-[#334155] bg-[#0E1223] px-2 py-1 text-[#F8FAFC] outline-none focus:border-[#3B82F6]" />
        </label>
        <label className="flex items-center gap-1 text-[#94A3B8]">止
          <input type="date" value={end} onChange={(e) => setEnd(e.target.value)}
            className="rounded border border-[#334155] bg-[#0E1223] px-2 py-1 text-[#F8FAFC] outline-none focus:border-[#3B82F6]" />
        </label>
        {(Object.keys(QUICK_LABELS) as QuickKind[]).map((k) => (
          <button key={k} onClick={() => { const r = quickRange(k, todayIso()); setStart(r.start); setEnd(r.end) }}
            className="cursor-pointer rounded border border-[#334155] px-2 py-1 text-[10px] text-[#94A3B8] transition-colors hover:border-[#3B82F6] hover:text-[#3B82F6]">
            {QUICK_LABELS[k]}
          </button>
        ))}
        <button onClick={() => void generate()} disabled={!canGenerate(start || null, end || null, busy)}
          className="cursor-pointer rounded bg-[#22C55E] px-3 py-1.5 font-semibold text-[#0F172A] transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40">
          {busy ? `生成中… ${elapsed}s` : '生成报告'}
        </button>
        {gate && <span className="text-[10px] text-[#F59E0B]">{gate}</span>}
        {result && !busy && (
          <span className="text-[10px] text-[#22C55E]">
            {result.rtype} · 有效日 {result.n_eff} · {result.figs.length} 图 · sha256 {result.sha256.slice(0, 8)} · 已留档（文件在后端 {result.dir}）
          </span>
        )}
      </div>

      {error && <div role="alert" className="rounded border border-[#EF4444]/50 bg-[#EF4444]/10 px-3 py-1.5 text-[11px] text-[#EF4444]">{error} <button className="underline" onClick={() => setError(null)}>关闭</button></div>}
      {busy && (
        <div className="rounded border border-[#3B82F6]/40 bg-[#3B82F6]/10 px-3 py-1.5 text-[11px] text-[#3B82F6]">
          复盘全链运行中（偏差→分解→跃变→画像→报告，含词表与数字校验闸门）……已耗时 {elapsed}s
        </div>
      )}

      <div className="flex min-h-0 flex-1 gap-3 overflow-hidden">
        {/* 左：章节导航 + 历史清单 */}
        <aside className="flex w-56 shrink-0 flex-col gap-2 overflow-y-auto">
          <div className="text-[10px] font-semibold text-[#94A3B8]">章节导航</div>
          {chapters.chapters.length === 0 && <div className="text-[10px] text-[#475569]">尚未生成/选择报告</div>}
          {chapters.chapters.map((c, i) => (
            <button key={c.title} onClick={() => setChapterIdx(i)}
              className={`cursor-pointer rounded px-2 py-1 text-left text-[11px] ${i === chapterIdx ? 'bg-[#3B82F6]/20 text-[#60A5FA]' : 'text-[#94A3B8] hover:bg-[#1A1E2F]'}`}>
              {c.title}
            </button>
          ))}
          <div className="mt-2 border-t border-[#334155] pt-2 text-[10px] font-semibold text-[#94A3B8]">历史报告</div>
          {history.slice(0, 12).map((h) => (
            <button key={h.report_id} onClick={() => void pickHistory(h.report_id)}
              className={`cursor-pointer rounded px-2 py-1 text-left text-[10px] ${detail?.report_id === h.report_id ? 'bg-[#3B82F6]/20 text-[#60A5FA]' : 'text-[#94A3B8] hover:bg-[#1A1E2F]'}`}>
              {h.rtype} {h.d_start.slice(5)}~{h.d_end.slice(5)}（{h.n_eff} 日）
            </button>
          ))}
        </aside>

        {/* 右：章节正文 + 导出 */}
        <section className="flex min-w-0 flex-1 flex-col overflow-hidden rounded border border-[#334155] bg-[#0E1223]">
          <div className="flex items-center gap-2 border-b border-[#334155] px-3 py-1.5">
            <span className="text-[11px] font-semibold text-[#F8FAFC]">{chapters.chapters[chapterIdx]?.title ?? '报告预览'}</span>
            <div className="ml-auto flex gap-2">
              <button disabled={!detail?.content} onClick={() => detail?.content && downloadText(`${detail.report_id}.md`, detail.content, 'text/markdown;charset=utf-8')}
                className="cursor-pointer rounded border border-[#334155] px-2 py-0.5 text-[10px] text-[#F8FAFC] transition-colors hover:border-[#22C55E] hover:text-[#22C55E] disabled:opacity-40">
                导出 .md
              </button>
              <button disabled={!detail?.content} onClick={() => detail?.content && downloadText(`${detail.report_id}_明细.csv`, mdTablesToCsv(detail.content), 'text/csv;charset=utf-8')}
                className="cursor-pointer rounded border border-[#334155] px-2 py-0.5 text-[10px] text-[#F8FAFC] transition-colors hover:border-[#22C55E] hover:text-[#22C55E] disabled:opacity-40">
                导出明细 .csv
              </button>
            </div>
          </div>
          {detail?.content ? (
            <div
              className="md-report min-h-0 flex-1 overflow-auto px-4 py-3 text-[11px] leading-5 text-[#CBD5E1]"
              dangerouslySetInnerHTML={{
                __html:
                  (chapterIdx === 0 && chapters.head
                    ? mdChapterToHtml(chapters.head, `/api/retro/reports/${detail.report_id}/figs`) + '<hr/>'
                    : '') +
                  mdChapterToHtml(
                    chapters.chapters[chapterIdx]?.body ?? '',
                    `/api/retro/reports/${detail.report_id}/figs`,
                  ),
              }}
            />
          ) : (
            <pre className="mono min-h-0 flex-1 overflow-auto whitespace-pre-wrap px-4 py-3 text-[11px] leading-5 text-[#CBD5E1]">
              选择起止日后点「生成报告」；或从左侧历史报告打开。
            </pre>
          )}
        </section>
      </div>
    </div>
  )
}
