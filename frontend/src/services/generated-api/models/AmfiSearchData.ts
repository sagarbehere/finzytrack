/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AmfiScheme } from './AmfiScheme';
export type AmfiSearchData = {
    /**
     * Matching schemes, those with a recent NAV first.
     */
    schemes: Array<AmfiScheme>;
    /**
     * How many schemes matched; only the first ones are listed.
     */
    total: number;
    /**
     * Set when the latest NAVs could not be loaded; the schemes are still listed.
     */
    nav_warning?: (string | null);
};

