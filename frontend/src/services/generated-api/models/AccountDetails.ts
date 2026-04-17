/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AccountCurrencyData } from './AccountCurrencyData';
/**
 * Detailed account information including transaction data.
 */
export type AccountDetails = {
    /**
     * Beancount account name (e.g., 'Assets:Bank:Checking')
     */
    name: string;
    /**
     * Date account was opened
     */
    open_date: string;
    /**
     * Date account was closed (null if open)
     */
    close_date?: (string | null);
    /**
     * Per-currency transaction data
     */
    currencies: Array<AccountCurrencyData>;
    /**
     * Currencies declared on the open directive
     */
    declared_currencies?: Array<string>;
    /**
     * Arbitrary account metadata
     */
    metadata?: Record<string, any>;
};

