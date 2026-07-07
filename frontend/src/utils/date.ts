/** Today's date as a `YYYY-MM-DD` string in the user's *local* calendar.
 *
 * Deliberately not `new Date().toISOString().slice(0,10)` — that is UTC and can
 * be off by one near midnight, stamping a directive with the wrong day. Budget
 * and document effective-dates use this so "today" matches the user's clock. */
export function todayLocal(): string {
  const d = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}
