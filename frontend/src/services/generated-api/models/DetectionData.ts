/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Data model for account detection results.
 */
export type DetectionData = {
    /**
     * Whether account was detected
     */
    detected: boolean;
    /**
     * Detected or default Beancount account
     */
    beancount_account: string;
    /**
     * Detected or default currency
     */
    currency: string;
    /**
     * Detection result message
     */
    message: string;
};

