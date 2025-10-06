/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CommitPosting } from './CommitPosting';
/**
 * Complete transaction data for committing to the ledger.
 *
 * Note: source_account is REQUIRED and provided as a top-level field.
 * The backend automatically copies it into meta dict before writing to ledger.
 */
export type CommitTransaction = {
    /**
     * Transaction date
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
     * Convenience field (backend converts to ofx_memo metadata)
     */
    memo?: (string | null);
    /**
     * Transaction narration
     */
    narration: string;
    /**
     * Transaction tags
     */
    tags?: Array<string>;
    /**
     * Transaction links
     */
    links?: Array<string>;
    /**
     * Transaction postings (must balance)
     */
    postings: Array<CommitPosting>;
    /**
     * REQUIRED: Source account that originated this transaction. Backend copies this into meta.
     */
    source_account: string;
    /**
     * Additional arbitrary metadata (ofx_id, custom fields, etc.). Backend adds source_account automatically.
     */
    meta?: Record<string, string>;
};

