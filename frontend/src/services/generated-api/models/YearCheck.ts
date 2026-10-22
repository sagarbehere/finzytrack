/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type YearCheck = {
    year: number;
    /**
     * Interpretation → row count
     */
    counts: Record<string, number>;
    /**
     * Units in the ledger at year end after this import
     */
    end_units: string;
    /**
     * Statement running balance at year end
     */
    statement_units?: (string | null);
    /**
     * None when the statement has no balance for that year or it can't be compared
     */
    ok?: (boolean | null);
};

