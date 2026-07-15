/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ModeColor } from './ModeColor';
import type { ModePalette } from './ModePalette';
import type { Overflow } from './Overflow';
import type { States } from './States';
import type { Stickiness } from './Stickiness';
import type { Thresholds } from './Thresholds';
import type { Tints } from './Tints';
import type { Valence } from './Valence';
/**
 * A complete dashboard color theme.
 *
 * Editing order (tiered — most users only touch tier 1):
 * 1. `valence` (the favorability bars) + `categorical` (pie/treemap identity) + `brand`.
 * 2. `baseline`, `series` (named-series mapping), `accountPins`.
 * 3. `thresholds`, `stickiness`, `overflow`, `states`, `tints` — fine knobs;
 * adjust only if you really want to. Every one has a sensible default.
 */
export type DashboardTheme = {
    id: string;
    name: string;
    description?: string;
    version?: number;
    readme?: (string | null);
    brand: ModeColor;
    baseline: ModeColor;
    valence: Valence;
    categorical: ModePalette;
    series?: Record<string, string>;
    accountPins?: Record<string, string>;
    thresholds?: Thresholds;
    stickiness?: Stickiness;
    overflow?: Overflow;
    states?: States;
    tints?: Tints;
};

