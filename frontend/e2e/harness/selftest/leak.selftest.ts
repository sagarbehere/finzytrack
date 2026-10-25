// Each test leaks on purpose (a recorded page problem, an unscripted LLM call); the fixtures' teardown must fail
// both (e2e/harness.spec.ts).
import { test } from '../fixtures'

test('a recorded problem left at the end', async ({ page, problems }) => {
  await page.setContent(
    '<html lang="en"><head><title>leak</title></head><body><main><h1>leak</h1></main></body></html>',
  )
  problems.push('provoked leak')
})

test('an unscripted LLM call left at the end', async ({ llm, run }) => {
  await llm.calls()
  await fetch(`${run.llm}/chat/completions`, {
    method: 'POST',
    body: JSON.stringify({ model: 'stub-model', messages: [{ role: 'user', content: 'provoked unscripted' }] }),
  })
})
