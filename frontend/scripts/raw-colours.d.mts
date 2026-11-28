// Types for scripts/raw-colours.mjs, so the rule's test can import it under the strict tsconfig.
import type { ESLint } from 'eslint'

export declare function rawColourProblems(text: string): { line: number; text: string }[]
export declare function isExempt(path: string): boolean
export declare const plugin: ESLint.Plugin
