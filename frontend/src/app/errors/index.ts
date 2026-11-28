// The one handler of errors the user sees (ARCHITECTURE.md §6.4; ADR-0039; L2-05, L8-30; S0166). `presentError`
// records each failure once, with its presentation, in the app's error sink; the query and mutation caches call it
// (src/app/queryClient.ts), and so may a composable or a machine's error state. Its visual design (toasts, modals,
// history) comes with the notification primitives (L9-13, L8.M-15); this is the path they will read.
// One sink per app (`createErrorSink`, provided by `installErrors`), never a module-level store (EP-04).
import { inject, readonly, shallowRef, type App, type InjectionKey, type Ref } from 'vue'

import { AppError, failureOf, type ErrorClass } from '../../api/errors'

export type Presentation = 'toast' | 'modal' | 'none'

// ARCHITECTURE.md §6.1's default presentation per class. `invalid` becomes inline field errors once a form passes
// its context (the Field primitive, ADR-0035); until then a toast. A cancelled request is never shown.
const byClass: Record<ErrorClass, Presentation> = {
  invalid: 'toast',
  not_found: 'toast',
  conflict: 'modal',
  contract_refused: 'modal',
  forbidden: 'modal',
  unavailable: 'toast',
  internal: 'modal',
  aborted: 'none',
}

export function presentationOf(error: AppError): Presentation {
  return byClass[error.errorClass]
}

interface ErrorNotice {
  readonly error: AppError
  readonly presentation: Exclude<Presentation, 'none'>
}

/** The most recent notices kept: a fixed-size history ring (INV-10's diagnostic-buffer clause). */
export const NOTICE_HISTORY = 50

export interface ErrorSink {
  readonly notices: Readonly<Ref<readonly ErrorNotice[]>>
  /** Records `thrown` once (as its AppError); a cancelled request or an error already recorded adds nothing. */
  present(thrown: unknown): void
}

export function createErrorSink(limit = NOTICE_HISTORY): ErrorSink {
  const notices = shallowRef<readonly ErrorNotice[]>([])
  const seen = new WeakSet()
  return {
    notices: readonly(notices),
    present(thrown) {
      if (typeof thrown === 'object' && thrown !== null) {
        if (seen.has(thrown)) return
        seen.add(thrown)
      }
      const error = thrown instanceof AppError ? thrown : failureOf(thrown, undefined)
      seen.add(error)
      const presentation = presentationOf(error)
      if (presentation === 'none') return
      notices.value = [...notices.value, { error, presentation }].slice(-limit)
    },
  }
}

const sinkKey: InjectionKey<ErrorSink> = Symbol('ft-error-sink')

export function installErrors(app: App, sink: ErrorSink): void {
  app.provide(sinkKey, sink)
}

/** The app's error sink, inside a component or composable of an app that installed one. */
export function useErrors(): ErrorSink {
  const sink = inject(sinkKey)
  if (sink === undefined) throw new Error('useErrors: no error sink installed')
  return sink
}
