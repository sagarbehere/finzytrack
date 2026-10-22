/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PlannedPosting } from './PlannedPosting';
export type PlannedTransaction = {
    index: number;
    date: string;
    narration: string;
    payee: string;
    /**
     * buy, sell, switch, merge, reinvest, dividend, opening, reversal, segregation
     */
    kind: string;
    row_ids: Array<string>;
    postings: Array<PlannedPosting>;
    /**
     * True: matched to a bank transaction; False: fallback; None: no cash side
     */
    cash_matched?: (boolean | null);
};

