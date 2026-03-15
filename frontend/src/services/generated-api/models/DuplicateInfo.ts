/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Information about a potential duplicate transaction in the ledger.
 */
export type DuplicateInfo = {
    /**
     * Stable transaction UUID (UUIDv7)
     */
    id: string;
    /**
     * Content-based SHA256 hash
     */
    content_hash: string;
    /**
     * Date of the duplicate transaction
     */
    date: string;
    /**
     * Payee of the duplicate transaction
     */
    payee: string;
    /**
     * Narration of the duplicate transaction
     */
    narration: string;
    /**
     * Amount of the duplicate transaction
     */
    amount: string;
    /**
     * Account from the matching posting
     */
    account: string;
    /**
     * How duplicate was detected: 'external_id', 'exact_content', or 'fuzzy'
     */
    match_type: string;
};

