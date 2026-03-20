/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Lightweight listing model for XLS rules.
 */
export type XlsRuleSummary = {
    /**
     * YAML filename of the rule
     */
    filename: string;
    /**
     * Human-readable rule name
     */
    name: string;
    /**
     * Default Beancount account
     */
    default_account: string;
    /**
     * Default currency
     */
    default_currency?: string;
};

