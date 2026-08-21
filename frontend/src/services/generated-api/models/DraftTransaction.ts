/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DraftPosting } from './DraftPosting';
/**
 * A balanced draft transaction (maps 1:1 to CommitTransaction).
 */
export type DraftTransaction = {
    date: string;
    flag?: string;
    payee?: string;
    memo?: (string | null);
    narration?: string;
    tags?: Array<string>;
    links?: Array<string>;
    postings: Array<DraftPosting>;
    source_account: string;
    meta?: Record<string, string>;
};

