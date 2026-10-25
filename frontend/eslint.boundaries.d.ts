// Types for eslint.boundaries.js, so the boundaries test can import it under the strict tsconfig.
import type { Linter } from 'eslint'

export declare const FEATURE_EDGES: [string, string][]
export declare function boundariesConfig(edges?: [string, string][]): Linter.Config[]
declare const config: Linter.Config[]
export default config
