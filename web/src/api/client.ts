/** 后端 API 客户端（Step 8 联调）：VITE_API_BASE（dev 默认 http://127.0.0.1:8000）。
 *  后端未启动 → 抛 ApiError；store 捕获后降级 demo 模式（顶部横幅区分）。 */
export const API_BASE: string = import.meta.env.VITE_API_BASE ?? 'http://127.0.0.1:8000'

export class ApiError extends Error {
  status?: number
  constructor(message: string, status?: number) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let resp: Response
  try {
    resp = await fetch(`${API_BASE}${path}`, {
      headers: { 'Content-Type': 'application/json' },
      ...init,
    })
  } catch {
    throw new ApiError('后端不可达（未启动 uvicorn api:app？）')
  }
  if (!resp.ok) {
    let detail = `${resp.status}`
    try {
      const body = await resp.json()
      detail = body.detail ?? JSON.stringify(body).slice(0, 200)
    } catch { /* keep status */ }
    throw new ApiError(detail, resp.status)
  }
  return resp.json() as Promise<T>
}

export interface DayInfo {
  target_day: string
  a_day: string
  available_days: string[]
  all_days: string[]
  history_count: number
  dataset_version: number
  roll_source: string
}

export interface BackendState {
  day_info?: DayInfo
  dates: { D: string; A?: string; A1?: string }
  params: Record<string, unknown>
  run_id: string
  m7: { mode: string; final_on_96: number[]; space_96: (number | null)[]; load_rate_96: (number | null)[]; load_rate_24: (number | null)[] }
  m8: {
    a_day: string; a1_day: string; a1_non_pos_points: number
    k: number; M1: number; C1: number; M2: number; C2: number
    critical_space_96: number | null; critical_space_24: number | null
    pred1_96: (number | null)[]; pred2_96: (number | null)[]; final_96: (number | null)[]
    pred1_24: (number | null)[]; pred2_24: (number | null)[]; final_24: (number | null)[]
    min_pred1_96: number | null; used_pred2_96: boolean; used_pred2_24: boolean
    clamp: [number, number]
  }
  landing_24: Array<{
    period: number; members: string[]; n: number; enough: boolean
    bins: Array<{ lo: number; hi: number; mid: number; count: number; prob: number }>
    out_of_range: number; expectation: number | null; scope: string
  }>
  grey_24: Array<{
    evaluated: boolean; reason: string | null
    lowest_bin: { lo: number; hi: number; prob: number }
    highest_bin: { lo: number; hi: number; prob: number }
    prob_gain: number; prob_risk: number
    max_gain_price: [number, number] | null; max_risk_price: [number, number] | null
    max_gain_amount: [number, number] | null; max_risk_amount: [number, number] | null
  }>
  boundaries: Record<string, { values: (number | null)[]; kinds: string[]; srcDays: (string | null)[]; notes: (string | null)[] }>
  intents: Record<string, { listPrice: number | null; liftPrice: number | null; volume: number | null }>
  revisions: Array<{ rev_id: string; boundary_type: string; t: number; period: number | null; revised_value: number; reason: string; status: string; op_time: string }>
  m1_ready: { roll_source: string; warnings: string[] }
  timings_ms: Record<string, number>
}

export const api = {
  state: (period?: number) => request<BackendState>(`/api/state${period ? `?period=${period}` : ''}`),
  recalc: () => request<{ ok: boolean; timings_ms: Record<string, number> }>('/api/recalc', { method: 'POST' }),
  setParams: (params: Record<string, number>, reason: string) =>
    request<{ ok: boolean }>('/api/params', { method: 'POST', body: JSON.stringify({ params, reason }) }),
  modifyBoundary: (boundary: string, period: number, points: { t: number; value: number }[], reason: string) =>
    request<{ ok: boolean; rev_ids: string[]; price_24: number[] }>('/api/boundary', {
      method: 'POST', body: JSON.stringify({ boundary, period, points, reason }),
    }),
  setIntent: (period: number, patch: { list_price?: number | null; lift_price?: number | null; volume?: number | null }) =>
    request<{ ok: boolean }>('/api/intent', { method: 'POST', body: JSON.stringify({ period, ...patch }) }),
  rollback: (revId: string) =>
    request<{ ok: boolean }>('/api/revision/rollback', { method: 'POST', body: JSON.stringify({ rev_id: revId }) }),
  exportReport: () => request<{ ok: boolean; path: string }>('/api/export', { method: 'POST' }),
  chat: (message: string, period: number | null) =>
    request<{ ok: boolean; reply: string | null; error?: string; tool_calls?: unknown[] }>('/api/chat', {
      method: 'POST', body: JSON.stringify({ message, period }),
    }),
  days: () => request<DayInfo>('/api/days'),
  setTargetDay: (date: string) =>
    request<{ ok: boolean; day_info: DayInfo }>('/api/target-day', { method: 'POST', body: JSON.stringify({ date }) }),
  uploadData: async (file: File) => {
    const form = new FormData()
    form.append('file', file)
    let resp: Response
    try {
      resp = await fetch(`${API_BASE}/api/data/upload`, { method: 'POST', body: form })
    } catch {
      throw new ApiError('后端不可达（未启动 uvicorn api:app？）')
    }
    const body = await resp.json().catch(() => ({}))
    if (!resp.ok) {
      const detail = body.detail
      if (detail && typeof detail === 'object') {
        throw new ApiError(`格式校验未通过：${(detail.report?.errors ?? []).join('；')}`)
      }
      throw new ApiError(typeof detail === 'string' ? detail : `${resp.status}`)
    }
    return body as { ok: boolean; kind: string; report: { days: string[] }; dataset: { days: number }; day_info: DayInfo }
  },
  llmConfig: () => request<{ base_url: string; model: string; api_key_masked: string; configured: boolean }>('/api/llm/config'),
  setLlmConfig: (baseUrl: string, apiKey: string, model: string) =>
    request<{ ok: boolean }>('/api/llm/config', { method: 'POST', body: JSON.stringify({ base_url: baseUrl, api_key: apiKey, model }) }),
}
