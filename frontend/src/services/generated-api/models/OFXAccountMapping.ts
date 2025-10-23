/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * OFX account mapping for exact account detection.
 */
export type OFXAccountMapping = {
    /**
     * Institution name from OFX
     */
    institution: string;
    /**
     * Financial Institution ID from OFX
     */
    institution_fid?: (string | null);
    /**
     * Account type from OFX (empty string for credit cards)
     */
    account_type: string;
    /**
     * Full account ID from OFX
     */
    account_id: string;
    /**
     * Currency from OFX
     */
    currency: string;
    /**
     * Corresponding Beancount account
     */
    beancount_account: string;
};

