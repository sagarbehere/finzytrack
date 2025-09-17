/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Request model for learning account mappings.
 */
export type LearnAccountRequest = {
    /**
     * Institution name from OFX
     */
    institution: string;
    /**
     * Financial Institution ID
     */
    institution_fid?: (string | null);
    /**
     * Account type (empty string for credit cards)
     */
    account_type: string;
    /**
     * Full account ID
     */
    account_id: string;
    /**
     * User-specified Beancount account
     */
    beancount_account: string;
    /**
     * Alphanumeric currency code
     */
    currency: string;
};

