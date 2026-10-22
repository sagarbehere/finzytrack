/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type StatementPlanSummary = {
    period_start: string;
    period_end: string;
    first_date?: (string | null);
    last_date?: (string | null);
    years: number;
    rows: number;
    funds: number;
    funds_held: number;
    transactions: number;
    new_commodities: number;
    already_imported: number;
    matched: number;
    fallback: number;
    investors: Array<string>;
};

