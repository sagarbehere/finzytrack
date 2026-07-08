/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * A single budget directive.
 */
export type BudgetItem = {
    /**
     * Stable identifier (source location hash)
     */
    id: string;
    /**
     * Effective-from date, YYYY-MM-DD
     */
    date: string;
    /**
     * Account the budget applies to
     */
    account: string;
    /**
     * daily | weekly | monthly | quarterly | yearly (or 'none' for an end)
     */
    interval: string;
    /**
     * Budget amount (decimal string); '0' and ignored when ended
     */
    amount: string;
    /**
     * Currency code
     */
    currency: string;
    /**
     * True if this is a 'budget end' tombstone (no budget from here)
     */
    ended?: boolean;
    /**
     * File the directive lives in
     */
    source_file?: (string | null);
    /**
     * Line number in the source file
     */
    source_lineno?: number;
};

