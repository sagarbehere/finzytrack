import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import type { EChartsOption } from 'echarts'

// Capture what RecipeChart hands to ECharts instead of rendering a canvas.
const m = vi.hoisted(() => ({ options: [] as Record<string, unknown>[] }))
vi.mock('echarts/core', () => ({
  use: vi.fn(),
  init: vi.fn(() => ({
    setOption: (o: Record<string, unknown>) => m.options.push(o),
    on: vi.fn(),
    getZr: () => ({ on: vi.fn() }),
    resize: vi.fn(),
    dispose: vi.fn(),
  })),
}))

import RecipeChart from '@/components/recipes/RecipeChart.vue'

const lineOptions = (dataZoom: unknown): EChartsOption => ({
  xAxis: { type: 'time' },
  yAxis: { type: 'value' },
  series: [{ type: 'line', encode: { x: 'date', y: 'v' } }],
  dataZoom,
} as EChartsOption)

const data = [{ date: '2024-01-01', v: 1 }, { date: '2024-02-01', v: 2 }]

beforeEach(() => { m.options = [] })

describe('RecipeChart — dataZoom', () => {
  it('passes dataZoom through, giving the slider default styling the recipe can override', () => {
    mount(RecipeChart, {
      props: {
        data,
        chartOptions: lineOptions([
          { type: 'inside', zoomOnMouseWheel: 'ctrl' },
          { type: 'slider', height: 18, borderColor: '#123456' },
        ]),
      },
    })
    const dz = m.options[0].dataZoom as Record<string, unknown>[]
    expect(dz).toHaveLength(2)
    expect(dz[0]).toEqual({ type: 'inside', zoomOnMouseWheel: 'ctrl' })
    expect(dz[1]).toMatchObject({ type: 'slider', height: 18, borderColor: '#123456', backgroundColor: 'transparent' })
    expect(dz[1].selectedDataBackground).toBeDefined()
  })

  it('leaves dataZoom out when the recipe has none', () => {
    mount(RecipeChart, { props: { data, chartOptions: lineOptions(undefined) } })
    expect(m.options[0].dataZoom).toBeUndefined()
  })

  it('presents ⌘+wheel as Ctrl+wheel to the chart', () => {
    const wrapper = mount(RecipeChart, { props: { data, chartOptions: lineOptions([{ type: 'inside' }]) } })
    const inner = document.createElement('div')
    wrapper.element.appendChild(inner)
    const seen: boolean[] = []
    inner.addEventListener('wheel', (e) => seen.push((e as MouseEvent).ctrlKey))
    inner.dispatchEvent(new MouseEvent('wheel', { metaKey: true, bubbles: true }))
    inner.dispatchEvent(new MouseEvent('wheel', { bubbles: true }))
    expect(seen).toEqual([true, false])
  })
})
