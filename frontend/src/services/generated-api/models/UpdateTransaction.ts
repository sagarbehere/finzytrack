/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { UpdatePosting } from './UpdatePosting';
/**
 * A transaction to be updated in the ledger.
 */
export type UpdateTransaction = {
    /**
     * Transaction ID (UUIDv7)
     */
    id: string;
    /**
     * Transaction date (ISO format YYYY-MM-DD)
     */
    date: string;
    /**
     * Transaction flag (* or !)
     */
    flag: string;
    /**
     * Transaction payee
     */
    payee: string;
    /**
     * Transaction narration/description
     */
    narration: string;
    /**
     * Transaction memo (stored as ofx_memo metadata)
     */
    memo?: (string | null);
    /**
     * Transaction tags
     */
    tags?: Array<string>;
    /**
     * Transaction links
     */
    links?: Array<string>;
    /**
     * Transaction postings
     */
    postings: Array<UpdatePosting>;
    /**
     * Transaction metadata
     */
    meta?: Record<string, string>;
};

