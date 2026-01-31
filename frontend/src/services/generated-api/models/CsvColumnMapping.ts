/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Column index mapping for CSV files (0-based indices).
 *
 * Use either 'amount' for a single amount column, or both 'amount_debit'
 * and 'amount_credit' for banks that use separate DR/CR columns.
 */
export type CsvColumnMapping = {
    /**
     * Column index for transaction date
     */
    date: number;
    /**
     * Column index for transaction amount (single column)
     */
    amount?: (number | null);
    /**
     * Column index for debit amounts (money out)
     */
    amount_debit?: (number | null);
    /**
     * Column index for credit amounts (money in)
     */
    amount_credit?: (number | null);
    /**
     * Column index for payee name
     */
    payee?: (number | null);
    /**
     * Column index for narration/description
     */
    narration?: (number | null);
    /**
     * Column index for memo/reference
     */
    memo?: (number | null);
};

