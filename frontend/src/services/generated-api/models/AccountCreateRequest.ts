/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Request model for creating new Beancount accounts.
 */
export type AccountCreateRequest = {
    /**
     * Full Beancount account name with format validation
     */
    name: string;
    /**
     * Opening date
     */
    open_date: string;
    /**
     * Currencies and securities this account may hold; empty = any
     */
    currencies: Array<string>;
    /**
     * Optional account description or notes
     */
    description?: (string | null);
    /**
     * Optional account metadata
     */
    metadata?: (Record<string, any> | null);
};

