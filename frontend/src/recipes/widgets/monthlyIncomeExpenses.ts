import type { WidgetRecipe, ChartClickContext, ValueLinkConfig } from '@/types/recipes'

/**
 * Generate year options for the select parameter
 */
function generateYearOptions() {
  const currentYear = new Date().getFullYear()
  const years = []
  for (let year = currentYear; year >= currentYear - 5; year--) {
    years.push({ value: year, label: String(year) })
  }
  return years
}

/**
 * Format number as compact (e.g., 14200 -> "14.2k")
 */
function formatCompact(value: number): string {
  if (Math.abs(value) >= 1000) {
    return (value / 1000).toFixed(1) + 'k'
  }
  return value.toFixed(0)
}

/**
 * Convert YYYY-MM to abbreviated month (e.g., "Jan", "Feb")
 * Year is omitted since it's shown in the parameter selector
 */
function formatMonth(yearMonth: string): string {
  const month = yearMonth.split('-')[1]
  const monthNames = [
    'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
    'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'
  ]
  const monthIndex = parseInt(month, 10) - 1
  return monthNames[monthIndex]
}

/**
 * Monthly Income vs Expenses Chart Widget
 *
 * Shows a bar chart of monthly expenses, income, and savings for a selected year.
 * Styled to match Metabase with data labels, full month names, and muted colors.
 */
export const monthlyIncomeExpensesWidget: WidgetRecipe = {
  id: 'monthly-income-expenses',
  title: 'Monthly Expense Income bar chart',
  description: 'Comparison of monthly expenses, income, and savings',
  dbType: 'sqlite',
  parameters: [
    {
      name: 'year',
      label: 'Year',
      type: 'select',
      default: new Date().getFullYear(),
      options: generateYearOptions(),
    },
    {
      name: 'currency',
      label: 'Currency',
      type: 'select',
      default: 'USD',
      optionsFrom: 'currencies',
    },
  ],
  query: `
    SELECT
      year_month as month,
      SUM(CASE WHEN account_type = 'Income' THEN -amount ELSE 0 END) as income,
      SUM(CASE WHEN account_type = 'Expenses' THEN amount ELSE 0 END) as expenses
    FROM postings
    WHERE year = :year AND currency = :currency
      AND account_type IN ('Income', 'Expenses')
    GROUP BY year_month
    ORDER BY year_month
  `,
  transform: (rows) => {
    return rows.map((row) => {
      const income = Math.round(Number(row.income) || 0)
      const expenses = Math.round(Number(row.expenses) || 0)
      const savings = income - expenses

      return {
        month: formatMonth(String(row.month)),
        yearMonth: String(row.month), // Keep original YYYY-MM for click links
        expenses,
        income,
        savings,
      }
    })
  },
  visualization: {
    type: 'chart',
    chartType: 'bar',
    getSeriesClickLink: (context: ChartClickContext): ValueLinkConfig | null => {
      const { seriesName, data } = context
      const yearMonth = data.yearMonth as string
      if (!yearMonth) return null

      // Only link Income and Expenses bars (not Savings, which is derived)
      let accountType: string | undefined
      if (seriesName === 'Income') {
        accountType = 'Income'
      } else if (seriesName === 'Expenses') {
        accountType = 'Expenses'
      } else {
        return null
      }

      // Build date range for the month
      const [year, month] = yearMonth.split('-')
      const lastDay = new Date(Number(year), Number(month), 0).getDate()
      const dateFrom = `${yearMonth}-01`
      const dateTo = `${yearMonth}-${String(lastDay).padStart(2, '0')}`

      return {
        name: 'transactions',
        query: {
          accountContains: accountType,
          dateFrom,
          dateTo,
        },
      }
    },
    options: {
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'shadow' },
        formatter: (params: { seriesName: string; value: Record<string, number>; color: string }[]) => {
          if (!params.length) return ''
          const month = params[0].value.month
          let html = `<strong>${month}</strong><br/>`
          params.forEach((p) => {
            const field = p.seriesName.toLowerCase()
            const val = p.value[field] as number
            html += `<span style="color:${p.color}">●</span> ${p.seriesName}: ${val.toLocaleString()}<br/>`
          })
          return html
        },
      },
      legend: {
        data: ['Expenses', 'Income', 'Savings'],
        top: 0,
        left: 'left',
        itemGap: 20,
      },
      grid: {
        top: 40,
        bottom: 40,
        left: 50,
        right: 20,
      },
      xAxis: {
        type: 'category',
        axisLabel: {
          rotate: 0,
          interval: 0,
          fontSize: 11,
        },
        axisTick: { alignWithLabel: true },
      },
      yAxis: {
        type: 'value',
        axisLabel: {
          formatter: (value: number) => formatCompact(value),
        },
        splitLine: {
          lineStyle: {
            type: 'dashed',
            opacity: 0.6,
          },
        },
      },
      series: [
        {
          name: 'Expenses',
          type: 'bar',
          encode: { x: 'month', y: 'expenses' },
          itemStyle: { color: '#E8A951' }, // Muted orange
          barGap: '10%',
          label: {
            show: true,
            position: 'top',
            fontSize: 10,
            formatter: (params: { value: Record<string, number> }) =>
              formatCompact(params.value.expenses),
          },
        },
        {
          name: 'Income',
          type: 'bar',
          encode: { x: 'month', y: 'income' },
          itemStyle: { color: '#7DD3C0' }, // Muted teal
          label: {
            show: true,
            position: 'top',
            fontSize: 10,
            formatter: (params: { value: Record<string, number> }) =>
              formatCompact(params.value.income),
          },
        },
        {
          name: 'Savings',
          type: 'bar',
          encode: { x: 'month', y: 'savings' },
          itemStyle: { color: '#7B83AD' }, // Muted purple
          label: {
            show: true,
            position: 'top',
            fontSize: 10,
            formatter: (params: { value: Record<string, number> }) =>
              formatCompact(params.value.savings),
          },
        },
      ],
    },
  },
}
