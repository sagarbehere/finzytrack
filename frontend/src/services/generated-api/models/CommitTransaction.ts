/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CommitPosting } from './CommitPosting';
/**
 * Complete transaction data for committing to the ledger.
 * Includes all Beancount fields needed to write a valid transaction.
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
     * OFX transaction ID
     */
    ofx_id?: (string | null);
    /**
     * Source account (needed for transaction_id generation)
     */
    source_account: string;
};

