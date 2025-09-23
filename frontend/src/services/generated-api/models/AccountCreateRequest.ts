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
     * List of currencies for this account (at least one required)
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

