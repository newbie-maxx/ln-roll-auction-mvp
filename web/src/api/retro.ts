/** 复盘 API 客户端（M-14）：全部走 /api/retro/*，前端零业务计算。 */
import { API_BASE, ApiError } from './client'

async function retroRequest<T>(path: string, init?: RequestInit): Promise<T> {
  let resp: Response
  try {
    resp = await fetch(`${API_BASE}${path}`, {
      headers: { 'Content-Type': 'application/json' },
      ...init,
    })
  } catch {
    throw new ApiError('后端不可达（未启动 uvicorn backend.api:app？）')
  }
  const body = await resp.json().catch(() => ({}))
  if (!resp.ok) {
    const detail = body?.detail
    if (detail && typeof detail === 'object') {
      throw new ApiError(detail.message ?? JSON.stringify(detail))
    }
    throw new ApiError(typeof detail === 'string' && detail ? detail : `${resp.status}`)
  }
  return body as T
}

export interface RetroStatus {
  available_days: number
  first: string[]
  last: string[]
  register_rows: number
  register_days: string[]
  snapshots: Array<{ run_date: string; source: string; n: number }>
}

export interface SettleRow {
  t: number
  snapshot_trade_date: string | null
  result_type: string
  direction: string
  price: number
  settle_points: (number | null)[]
  diffs: (number | null)[]
  volume_mwh: number
  pnl: number | null
  note: string
}

export interface SettleTotals {
  filled: { n: number; n_positive: number; volume: number; amount: number }
  unfilled: { n: number; n_positive: number; volume: number; amount: number }
  day_execution_premium: number | null
}

export interface RegisterSaveResult {
  saved: { ok: boolean; version: number; pnl: number | null; is_revision: boolean; snapshot_trade_date: string | null }
  settlement: { run_date: string; rows: SettleRow[]; totals: SettleTotals; has_any: boolean; disclaimer: string }
}

export interface RegisterRecord {
  run_date: string
  t: number
  result_type: string
  direction: string
  price: number
  volume_mwh: number
  version: number
  snapshot_trade_date: string | null
  pnl: number | null
  note: string | null
}

export interface RetroReport {
  report_id: string
  rtype: 'R1' | 'R2'
  n_eff: number
  files: string[]
  figs: string[]
  sha256: string
  dir: string
}

export interface RetroReportMeta {
  report_id: string
  rtype: string
  d_start: string
  d_end: string
  n_eff: number
  sha256: string | null
  file_name: string | null
  generated_at: string
  superseded_by: string | null
}

export interface RetroReportDetail extends RetroReportMeta {
  content: string | null
}

export interface SkillInfo {
  id: string
  name: string
  version: string
  status: string
  sha256: string
  loaded_at: string
  error: string | null
}

export const retroApi = {
  status: (start: string, end: string) =>
    retroRequest<RetroStatus>(`/api/retro/status?start=${start}&end=${end}`),
  register: (rec: { run_date: string; t: number; result_type: string; direction: string; price: number; volume_mwh: number; note?: string }) =>
    retroRequest<RegisterSaveResult>('/api/retro/register', {
      method: 'POST',
      body: JSON.stringify(rec),
    }),
  registerCsv: (text: string) =>
    retroRequest<{ ok: boolean; n_saved: number; errors?: string[]; n_rejected?: number }>('/api/retro/register/csv', {
      method: 'POST',
      body: JSON.stringify({ text }),
    }),
  registerDay: (runDate: string) =>
    retroRequest<RegisterRecord[]>(`/api/retro/register/day?run_date=${runDate}`),
  report: (start: string, end: string) =>
    retroRequest<RetroReport>('/api/retro/report', {
      method: 'POST',
      body: JSON.stringify({ start, end }),
    }),
  reports: () => retroRequest<RetroReportMeta[]>('/api/retro/reports'),
  reportDetail: (reportId: string) => retroRequest<RetroReportDetail>(`/api/retro/reports/${reportId}`),
  skills: () => retroRequest<SkillInfo[]>('/api/retro/skills'),
}
