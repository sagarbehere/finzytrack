/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Request model for creating Beancount accounts.
 */
export type CreateAccountRequest = {
    /**
     * Full Beancount account name
     */
    account_name: string;
    /**
     * Alphanumeric currency code
     */
    currency: string;
};

