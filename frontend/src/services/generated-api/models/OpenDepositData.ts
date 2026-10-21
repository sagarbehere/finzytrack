/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type OpenDepositData = {
    /**
     * The Beancount text that is (or would be) written
     */
    preview: string;
    /**
     * False for a dry run
     */
    created: boolean;
    accounts_created?: Array<string>;
    /**
     * Things that won't block, e.g. an existing income account not tagged interest
     */
    warnings?: Array<string>;
};

