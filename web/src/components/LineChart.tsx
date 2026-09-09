/** ECharts 折线封装：96/24 点曲线、markArea 选中时段、修订曲线并存、dataZoom */
import * as echarts from 'echarts'
import { useEffect, useRef } from 'react'
import type { EChartsOption, SeriesOption } from 'echarts'

export interface ChartSpec {
  labels: string[]                      // x 轴标签（96 点用时刻，24 点用时段）
  series: SeriesOption[]
  markAreaIndex?: [number, number] | null   // 选中时段（series 索引区间，含端）
  yName?: string
  height?: number
}

export function LineChart({ spec }: { spec: ChartSpec }) {
  const ref = useRef<HTMLDivElement>(null)
  const chartRef = useRef<echarts.ECharts | null>(null)

  useEffect(() => {
    if (!ref.current) return
    chartRef.current = echarts.init(ref.current)
    const onResize = () => chartRef.current?.resize()
    window.addEventListener('resize', onResize)
    return () => {
      window.removeEventListener('resize', onResize)
      chartRef.current?.dispose()
      chartRef.current = null
    }
  }, [])

  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return
    const markArea = spec.markAreaIndex
      ? {
          silent: true,
          itemStyle: { color: 'rgba(239,68,68,0.18)' },
          data: [[{ xAxis: spec.markAreaIndex[0] }, { xAxis: spec.markAreaIndex[1] }]],
        }
      : undefined
    const option: EChartsOption = {
      backgroundColor: 'transparent',
      animation: false,
      grid: { left: 56, right: 16, top: 28, bottom: 44 },
      tooltip: { trigger: 'axis', backgroundColor: '#0E1223', borderColor: '#334155', textStyle: { color: '#F8FAFC', fontSize: 11 } },
      legend: { textStyle: { color: '#94A3B8', fontSize: 11 }, top: 0, itemWidth: 14, itemHeight: 8 },
      xAxis: {
        type: 'category',
        data: spec.labels,
        axisLine: { lineStyle: { color: '#334155' } },
        axisLabel: { color: '#94A3B8', fontSize: 10, interval: 11 },
      },
      yAxis: {
        type: 'value',
        name: spec.yName ?? '',
        nameTextStyle: { color: '#94A3B8', fontSize: 10 },
        axisLabel: { color: '#94A3B8', fontSize: 10 },
        splitLine: { lineStyle: { color: '#1A1E2F' } },
      },
      dataZoom: [
        { type: 'inside', start: 0, end: 100 },
        { type: 'slider', height: 14, bottom: 4, borderColor: '#334155', backgroundColor: '#0E1223', fillerColor: 'rgba(59,130,246,0.15)', textStyle: { color: '#94A3B8', fontSize: 9 } },
      ],
      series: spec.series.map((s) => ({ ...s, markArea })) as SeriesOption[],
    }
    chart.setOption(option, true)
  }, [spec])

  return <div ref={ref} style={{ width: '100%', height: spec.height ?? 260 }} />
}
