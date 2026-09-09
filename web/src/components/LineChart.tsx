/** ECharts 折线封装（设计 tokens = design-system/ln-roll-auction-workbench/MASTER.md）：
 *  96/24 点曲线、markArea 选中时段、修订曲线并存、dataZoom、阈值参考线（markLine）。
 *  图表规则（ui-ux-pro-max · chart 域）：系列区分 = 颜色 + 线型 + 直接图例，不靠色相单打；
 *  面积填充 ≤20% 透明度；可见数据表兜底（数值格即 a11y fallback）。 */
import * as echarts from 'echarts'
import { useEffect, useRef } from 'react'
import type { EChartsOption, LineSeriesOption } from 'echarts'

export interface ChartSeries {
  name: string
  data: (number | null)[]
  color: string
  dashed?: boolean          // 线型区分（不靠色相单打）
  faded?: boolean           // 并存时的原值淡化
  area?: boolean            // 主序列渐隐面积填充（≤20%）
}

export interface ThresholdLine {
  yAxis: number
  name: string
  color?: string
}

export interface ChartSpec {
  labels: string[]
  series: ChartSeries[]
  markAreaIndex?: [number, number] | null
  thresholds?: ThresholdLine[]          // 参考线（零价点负荷率 / 现货上下限）
  yName?: string
  height?: number
}

const TOKENS = {
  fg: '#F8FAFC',
  muted: '#94A3B8',
  border: '#334155',
  grid: '#1A1E2F',
  card: '#0E1223',
  blue: '#3B82F6',
}

export function LineChart({ spec }: { spec: ChartSpec }) {
  const ref = useRef<HTMLDivElement>(null)
  const chartRef = useRef<echarts.ECharts | null>(null)

  useEffect(() => {
    if (!ref.current) return
    chartRef.current = echarts.init(ref.current)
    const onResize = () => chartRef.current?.resize()
    window.addEventListener('resize', onResize)
    const ro = new ResizeObserver(() => chartRef.current?.resize())
    ro.observe(ref.current)
    return () => {
      window.removeEventListener('resize', onResize)
      ro.disconnect()
      chartRef.current?.dispose()
      chartRef.current = null
    }
  }, [])

  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return
    const markArea: LineSeriesOption['markArea'] = spec.markAreaIndex
      ? {
          silent: true,
          itemStyle: { color: 'rgba(239,68,68,0.16)' },
          data: [[{ xAxis: spec.markAreaIndex[0] }, { xAxis: spec.markAreaIndex[1] }]],
        }
      : undefined
    const markLine: LineSeriesOption['markLine'] = spec.thresholds?.length
      ? {
          silent: true,
          symbol: 'none',
          data: spec.thresholds.map((th) => ({
            yAxis: th.yAxis,
            lineStyle: { type: 'dashed' as const, color: th.color ?? TOKENS.muted, width: 1 },
            label: {
              formatter: `${th.name} ${th.yAxis}`,
              color: th.color ?? TOKENS.muted,
              fontSize: 10,
              position: 'insideEndTop' as const,
            },
          })),
        }
      : undefined

    const series: LineSeriesOption[] = spec.series.map((s, i) => ({
      name: s.name,
      type: 'line' as const,
      showSymbol: false,
      data: s.data,
      lineStyle: {
        color: s.color,
        width: i === 0 && !s.faded ? 2 : 1.5,
        type: s.dashed || s.faded ? ('dashed' as const) : ('solid' as const),
        opacity: s.faded ? 0.65 : 1,
      },
      itemStyle: { color: s.color },
      emphasis: { focus: 'series' as const, lineStyle: { width: i === 0 && !s.faded ? 2.5 : 2 } },
      areaStyle: s.area
        ? {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: `${s.color}33` },   // 20% 填充（渐隐）
              { offset: 1, color: `${s.color}00` },
            ]),
          }
        : undefined,
      markArea,
      ...(markLine && i === 0 ? { markLine } : {}),
    }))

    const option: EChartsOption = {
      backgroundColor: 'transparent',
      animation: false,
      grid: { left: 56, right: 20, top: 30, bottom: 46 },
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'cross', label: { backgroundColor: TOKENS.border, fontSize: 10 } },
        backgroundColor: TOKENS.card,
        borderColor: TOKENS.border,
        borderWidth: 1,
        padding: [6, 10],
        textStyle: { color: TOKENS.fg, fontSize: 11 },
        valueFormatter: (v) =>
          typeof v === 'number' ? v.toLocaleString('zh-CN', { maximumFractionDigits: 2 }) : String(v ?? '—'),
      },
      legend: {
        textStyle: { color: TOKENS.muted, fontSize: 10 },
        top: 0, right: 4, itemWidth: 14, itemHeight: 8, itemGap: 12,
        icon: 'roundRect',
      },
      xAxis: {
        type: 'category',
        data: spec.labels,
        axisLine: { lineStyle: { color: TOKENS.border } },
        axisTick: { show: false },
        axisLabel: { color: TOKENS.muted, fontSize: 10, interval: 11, hideOverlap: true },
      },
      yAxis: {
        type: 'value',
        name: spec.yName ?? '',
        nameTextStyle: { color: TOKENS.muted, fontSize: 10, align: 'left' },
        axisLabel: {
          color: TOKENS.muted, fontSize: 10,
          formatter: (v: number) => (Math.abs(v) >= 10000 ? `${(v / 1000).toFixed(0)}k` : String(v)),
        },
        splitLine: { lineStyle: { color: TOKENS.grid } },
      },
      dataZoom: [
        { type: 'inside', start: 0, end: 100, filterMode: 'none' },
        {
          type: 'slider', height: 14, bottom: 6,
          borderColor: TOKENS.border, backgroundColor: TOKENS.card,
          fillerColor: 'rgba(59,130,246,0.14)',
          handleStyle: { color: TOKENS.blue, borderColor: TOKENS.blue },
          moveHandleStyle: { color: TOKENS.border },
          emphasis: { handleStyle: { borderColor: TOKENS.fg } },
          dataBackground: { lineStyle: { color: TOKENS.border }, areaStyle: { color: TOKENS.grid } },
          selectedDataBackground: { lineStyle: { color: TOKENS.blue }, areaStyle: { color: 'rgba(59,130,246,0.12)' } },
          textStyle: { color: TOKENS.muted, fontSize: 9 },
        },
      ],
      series,
    }
    chart.setOption(option, true)
  }, [spec])

  return <div ref={ref} style={{ width: '100%', height: spec.height ?? 260 }} />
}
