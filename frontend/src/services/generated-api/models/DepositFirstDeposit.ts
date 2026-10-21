/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * The transfer that funds the new deposit.
 */
export type DepositFirstDeposit = {
    /**
     * Assets or Liabilities account the money comes from
     */
    from_account: string;
    /**
     * Amount moved into the deposit (> 0)
     */
    amount: (number | string);
    /**
     * Transfer date; defaults to the open date
     */
    date?: (string | null);
};

