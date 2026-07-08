/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Body for POST/PUT — create or replace a budget directive.
 *
 * To end a budget (tombstone), send ``interval='none'``; ``amount`` is then
 * optional and ignored (a ``0`` is written to carry the currency).
 */
export type BudgetWriteRequest = {
    /**
     * Effective-from date, YYYY-MM-DD
     */
    date: string;
    /**
     * Account the budget applies to
     */
    account: string;
    /**
     * daily | weekly | monthly | quarterly | yearly | none (end)
     */
    interval: string;
    /**
     * Budget amount (decimal string); omit/ignored when ending
     */
    amount?: (string | null);
    /**
     * Currency code
     */
    currency: string;
};

