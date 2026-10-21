/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * The interest income account for a new deposit: a new one created with it,
 * or an existing one.
 */
export type DepositIncomeAccount = {
    /**
     * 'new' creates it; 'existing' uses an open Income account
     */
    mode: DepositIncomeAccount.mode;
    /**
     * Income account name
     */
    account: string;
};
export namespace DepositIncomeAccount {
    /**
     * 'new' creates it; 'existing' uses an open Income account
     */
    export enum mode {
        NEW = 'new',
        EXISTING = 'existing',
    }
}

