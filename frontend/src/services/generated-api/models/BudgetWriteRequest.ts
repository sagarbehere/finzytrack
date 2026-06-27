/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Body for POST/PUT — create or replace a budget directive.
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
     * daily | weekly | monthly | quarterly | yearly
     */
    interval: string;
    /**
     * Budget amount (decimal string)
     */
    amount: string;
    /**
     * Currency code
     */
    currency: string;
};

