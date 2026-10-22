/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * A user's fix to one statement row, applied before planning (§13.3).
 */
export type RowCorrection = {
    row_id: string;
    exclude?: boolean;
    interpretation?: ('buy' | 'sell' | 'reinvest' | 'dividend' | 'switch_out' | 'switch_in' | 'merge_out' | 'merge_in' | 'tax' | 'segregation' | 'reversal' | 'info' | 'unsupported' | null);
    /**
     * Row id to pair with; '' = never pair
     */
    pair_with?: (string | null);
    date?: (string | null);
    units?: (number | string | null);
    amount?: (number | string | null);
};

