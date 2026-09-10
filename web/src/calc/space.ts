/** M7 空间公式 + 24↔96 换算（口径：火电竞价空间(t) = 负荷 − 水电 − 核电 − 地方燃煤 − 风电 − 光伏 − 联络线 − 非市场化出力） */
import type { Series24, Series96 } from './types'

export interface SpaceInputs {
  负荷: Series96
  水电: Series96
  核电: Series96
  地方燃煤: Series96
  风电: Series96
  光伏: Series96
  联络线: Series96          // 联络线基线
  非市场化: Series96
  省间交易总量?: Series96   // 交易员预测（24 点展开 96 点；缺省 = 0）
}

/** 96 点火电竞价空间（联络线项 = 实时联络线预测 = 基线 − 省间交易总量）；任一项缺 → 该点 null（"缺输入"） */
export function biddingSpace(b: SpaceInputs): Series96 {
  const subs = [b.水电, b.核电, b.地方燃煤, b.风电, b.光伏, b.联络线, b.非市场化]
  const out: Series96 = new Array(96).fill(null)
  for (let t = 0; t < 96; t++) {
    const load = b.负荷[t]
    if (load === null || load === undefined || !Number.isFinite(load)) continue
    let s = load
    let ok = true
    for (const p of subs) {
      const v = p[t]
      if (v === null || v === undefined || !Number.isFinite(v)) { ok = false; break }
      s -= v
    }
    if (ok && b.省间交易总量) {
      const v = b.省间交易总量[t]
      if (v === null || v === undefined || !Number.isFinite(v)) { ok = false }
      else s -= v
    }
    out[t] = ok ? s : null
  }
  return out
}

/** 96→24：每 4 点等权算术平均（缺数跳过；组内全缺 → null）。任意 4 的倍数长度通用。 */
export function to24(s96: Series96): Series24 {
  const groups = Math.floor(s96.length / 4)
  const out: Series24 = new Array(groups).fill(null)
  for (let i = 0; i < groups; i++) {
    const seg = s96.slice(i * 4, i * 4 + 4).filter((v): v is number => v !== null && Number.isFinite(v))
    out[i] = seg.length ? seg.reduce((a, c) => a + c, 0) / seg.length : null
  }
  return out
}

/** 24→96 展开：小时值复制 4 份（省间滚撮口径）。任意长度通用，与 to24 互逆。 */
export function expand24(s24: Series24): Series96 {
  const out: Series96 = []
  for (const v of s24) {
    for (let j = 0; j < 4; j++) out.push(v)
  }
  return out
}
