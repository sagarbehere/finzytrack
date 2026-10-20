/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * One recognized metadata key in one scope.
 */
export type MetadataFieldSpec = {
    /**
     * Metadata key as written in the ledger (e.g. 'asset-class')
     */
    key: string;
    /**
     * 'commodity', 'account' or 'income-account'
     */
    scope: string;
    /**
     * enum, text, date, integer, percent, account or commodity
     */
    kind: string;
    /**
     * Short UI label
     */
    label: string;
    /**
     * One line on what setting this key adds
     */
    adds: string;
    /**
     * Recognized values (enum only); custom values are allowed
     */
    values?: Array<string>;
    /**
     * Whether an unset value is listed in the review
     */
    review: boolean;
    /**
     * Relevant only when this other key on the same item...
     */
    requires_key?: (string | null);
    /**
     * ...has one of these values (case-insensitive)
     */
    requires_values?: Array<string>;
};

